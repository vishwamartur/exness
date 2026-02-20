"""
Risk Manager Layer
Centralizes all pre-trade risk checks and position sizing.
"""
import time
from datetime import datetime, timezone, timedelta
import MetaTrader5 as mt5
from config import settings
from utils.news_filter import is_news_blackout, get_active_events
from utils.correlation_filter import check_correlation_conflict
from utils.shared_state import SharedState

class RiskManager:
    def __init__(self, mt5_client=None):
        self.client = mt5_client
        self.state = SharedState()
        
        # Restore Daily Trades with Date Check
        cached_count = self.state.get("daily_trades", 0)
        last_date_str = self.state.get("daily_trades_date", "")
        
        current_date = datetime.now(timezone.utc).date()
        current_date_str = current_date.isoformat()
        
        if last_date_str != current_date_str:
            # New day (or first run), reset
            self.daily_trades = 0
            self.state.set("daily_trades", 0)
            self.state.set("daily_trades_date", current_date_str)
            print(f"[RISK] Daily trades reset to 0 (New Day: {current_date_str})")
        else:
            self.daily_trades = cached_count
            print(f"[RISK] Restored daily trades: {self.daily_trades}")
            
        self.current_trade_date = current_date
        self.last_trade_time = {}  # {symbol: timestamp}
        self.symbol_stats = {} # {symbol: {'net_pnl': 0, 'avg_win': 0, 'avg_loss': 0, 'kill_switch': False}}
        self.last_stats_update = {} # {symbol: timestamp}
        
    def _check_daily_reset(self):
        """Checks if a new day has started in UTC and resets daily limits."""
        now_date = datetime.now(timezone.utc).date()
        if now_date != self.current_trade_date:
            print(f"[RISK] New Day Detected: {now_date} (Was: {self.current_trade_date}) - Resetting Limits")
            self.daily_trades = 0
            self.current_trade_date = now_date
            self.state.set("daily_trades", 0)
            self.state.set("daily_trades_date", now_date.isoformat())

    def check_pre_scan(self, symbol):
        """
        Fast checks run BEFORE heavy analysis.
        Checks: Daily Limit, Cooldown, Spread, News.
        """
        # 0. Circuit Breaker (Shared State)
        breaker = self.state.get("circuit_breaker", "CLOSED")
        if breaker == "OPEN":
            return False, "Circuit Breaker TRIPPED via Shared Memory"

        # 1. Daily Trade Limit
        self._check_daily_reset()
        if self.daily_trades >= settings.MAX_DAILY_TRADES:
            return False, "Daily Limit Reached"
            
        # 1a. Kill Switch & Payoff Mandate
        # Update stats if stale (e.g. every 5 mins or on every check if fast enough? Let's do 5 mins)
        if time.time() - self.last_stats_update.get(symbol, 0) > 300:
             self._update_symbol_stats(symbol)
             
        if not self._check_kill_switch(symbol):
             return False, f"Kill Switch Active (Loss Limit Hit)"
             
        if not self._check_payoff_mandate(symbol):
             return False, f"Payoff Mandate Fail (AvgLoss > {settings.AVG_LOSS_RATIO_THRESHOLD}x AvgWin)"
            
        # 1.5 Daily Loss Limit (Expectancy Guard)
        # We need to calculate realized P&L for today.
        # Ideally this is tracked in SharedState or queried from MT5/Journal.
        # For speed/robustness, let's query MT5 directly for today's history.
        try:
            now = datetime.now(timezone.utc)
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Get history
            # Note: history_deals_get returns deals, we sum profit, commission, swap
            deals = self.client.get_history_deals(start_of_day, now)
            
            day_pnl = 0.0
            if deals:
                for deal in deals:
                    # Filter output deals (entry/exit) - profit is on exit deals usually, commission on entry/exit
                    day_pnl += deal.profit + deal.commission + deal.swap
                    
            if day_pnl < -settings.MAX_DAILY_LOSS_USD:
                 return False, f"Daily Loss Limit Hit (${day_pnl:.2f} < -${settings.MAX_DAILY_LOSS_USD})"
                 
        except Exception as e:
            # If fail, don't block, but log?
            pass

        # 2. Cooldown (3 mins per symbol)
        last = self.last_trade_time.get(symbol, 0)
        if time.time() - last < settings.COOLDOWN_SECONDS:
            return False, f"Cooldown active ({int(settings.COOLDOWN_SECONDS - (time.time()-last))}s left)"

        # 3. Spread Check
        # Ensure we have tick data
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            # Often happens if symbol not in MarketWatch or market closed
            return False, "No Tick Data"
        
        # Dynamic Spread Calculation
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
             return False, "Symbol Info Not Found"
        point = symbol_info.point
        if point == 0: point = 0.00001 # Fallback
        
        spread_points = (tick.ask - tick.bid) / point
        spread_pips = spread_points / 10.0 # Standardize 1 Pip = 10 Points
        
        # Dynamic Threshold based on Asset Class
        max_spread = settings.MAX_SPREAD_PIPS
        if symbol in getattr(settings, 'SYMBOLS_CRYPTO', []):
            max_spread = settings.MAX_SPREAD_PIPS_CRYPTO # e.g. 50 (needs boost)
        elif symbol in getattr(settings, 'SYMBOLS_COMMODITIES', []):
            max_spread = settings.MAX_SPREAD_PIPS_COMMODITY
            
        if spread_pips > max_spread:
            # Check if market is even open/active by spread
            return False, f"Spread High ({spread_pips:.1f} > {max_spread})"

        # 4. News Filter
        is_blocked, event_name = is_news_blackout(symbol)
        if is_blocked:
            return False, f"News Blackout: {event_name}"

        return True, "OK"

    def _update_symbol_stats(self, symbol):
        """Re-calculates rolling metrics for specific symbol."""
        try:
            now = datetime.now(timezone.utc)
            start = now - timedelta(days=30)  # Lookback 30 days for robustness
            
            # Use client helper
            if not self.client: return
            deals = self.client.get_history_deals(start, now)
            if not deals: return

            # Filter for this symbol
            symbol_deals = [d for d in deals if d.symbol == symbol and d.entry == mt5.DEAL_ENTRY_OUT]
            
            if not symbol_deals: return
            
            # Sort by time desc
            symbol_deals.sort(key=lambda x: x.time, reverse=True)
            
            # 1. Kill Switch Stats (Last N trades)
            recent = symbol_deals[:settings.KILL_SWITCH_LOOKBACK_TRADES]
            recent_pnl = sum(d.profit + d.commission + d.swap for d in recent)
            
            # 2. Payoff Stats (All valid trades in window)
            wins = [d.profit for d in symbol_deals if d.profit > 0]
            losses = [d.profit for d in symbol_deals if d.profit <= 0]
            
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = abs(sum(losses) / len(losses)) if losses else 0
            
            self.symbol_stats[symbol] = {
                'kill_pnl': recent_pnl,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'win_rate': (len(wins) / len(symbol_deals)) if symbol_deals else 0,
                'count': len(symbol_deals)
            }
            self.last_stats_update[symbol] = time.time()
            
        except Exception as e:
            print(f"[RISK] Stats update failed for {symbol}: {e}")

    def _check_kill_switch(self, symbol):
        stats = self.symbol_stats.get(symbol)
        if not stats: return True
        
        # Check Net Loss Threshold
        if stats['kill_pnl'] < settings.KILL_SWITCH_LOSS_THRESHOLD:
            # Check if we have enough trades to validly trigger
            if stats['count'] >= 10: 
                return False
        return True

    def _check_payoff_mandate(self, symbol):
        if not settings.MANDATE_MIN_RR: return True
        
        stats = self.symbol_stats.get(symbol)
        if not stats: return True
        
        # If we have enough data (e.g. 20 trades)
        if stats['count'] < 20: return True
        
        # If Avg Loss is huge compared to Avg Win
        if stats['avg_win'] > 0:
            ratio = stats['avg_loss'] / stats['avg_win']
            if ratio > settings.AVG_LOSS_RATIO_THRESHOLD:
                # Allow if Win Rate is stellar? e.g. > 80%? 
                # For now, strict: NO.
                return False
                
        return True

    def check_execution(self, symbol, direction, sl, tp, active_positions=[]):
        """
        Final checks run just BEFORE placing an order.
        Checks: Correlation, Profitability (Commission Awareness).
        """
        # 5. Correlation Filter
        conflict, reason = check_correlation_conflict(symbol, direction, active_positions)
        if conflict:
            return False, f"Correlation Conflict: {reason}"

        # 6. Profitability Check (Net Profit > Commission * Ratio)
        # Estimate cost
        # We need point value. 
        # For major FX, 1 lot = $10 per pip. 
        # Commission = $7.
        # We need to ensure (TP - Entry) * PipValue > Commission * 2
        
        try:
            # Simple approximation if client not available or for speed
            tick = mt5.symbol_info_tick(symbol)
            if tick:
                # Calculate spread cost
                spread = (tick.ask - tick.bid)
                
                # Gross Profit (Approximate)
                entry = tick.ask if direction == "BUY" else tick.bid
                gross_profit_points = abs(tp - entry)
                
                # Check Ratio
                # We normalize everything to points/pips to avoid currency conversion complex logic here
                # Commision in pips approx: $7 / $10 = 0.7 pips. 
                # Spread = X pips.
                # Cost = 0.7 + Spread.
                
                point = tick.point
                if point == 0: point = 0.00001
                
                spread_points = spread / point
                comm_points = (settings.COMMISSION_PER_LOT / 10.0) if "JPY" not in symbol else (settings.COMMISSION_PER_LOT / 1000.0) 
                # JPY 1 lot = 1000 units? No 100,000. 1 pip = 1000 JPY approx $7?
                # Let's use a safe simplified buffer: Cost = 2.0 pips (Spread+Comm).
                
                cost_pips = spread_points + 1.0 # Buffer for commission
                profit_pips = gross_profit_points / point
                
                net_pips = profit_pips - cost_pips
                
                if net_pips < (cost_pips * settings.MIN_NET_PROFIT_RATIO):
                     return False, f"Low Profitability (Net {net_pips:.1f} pips < Cost {cost_pips:.1f} * {settings.MIN_NET_PROFIT_RATIO})"
                     
        except Exception as e:
            pass # Don't block on calculation error
            
        return True, "OK"

    def record_trade(self, symbol):
        """Updates internal counters after a successful trade."""
        self.daily_trades += 1
        self.state.set("daily_trades", self.daily_trades)
        self.last_trade_time[symbol] = time.time()

    def calculate_position_size(self, symbol, sl_pips, confluence_score, scaling_factor=1.0):
        """
        Calculates dynamic lot size based on risk percent and confluence.
        High Confluence (6+) -> Max Risk
        Medium (5) -> Avg Risk
        Low (3-4) -> Min Risk
        scaling_factor: Float (0.0-1.0) to reduce size (e.g. Volatile regime)
        """
        base_risk = settings.RISK_PERCENT 
        max_risk = settings.MAX_RISK_PERCENT

        if confluence_score >= 6:
            risk_pct = max_risk
        elif confluence_score >= 5:
            risk_pct = (base_risk + max_risk) / 2
        else:
            risk_pct = base_risk
            
        # Apply regime scaling
        risk_pct *= scaling_factor
            
        # Ensure we have client reference to calculate
        if self.client:
            lot = self.client.calculate_lot_size(symbol, sl_pips, risk_pct)
            
            # --- TAIL RISK CLAMP ---
            # For dangerous symbols, enforce hard dollar cap
            if symbol in getattr(settings, "TAIL_RISK_SYMBOLS", []):
                # Calculate Risk amount in USD for this lot
                # Risk = (Entry - SL) * pip_value * lot
                # Easier: logic used in calculate_lot_size is Risk = Balance * %
                # We want Lot such that Lot * RiskPerLot <= MAX_CAP
                
                # Reverse engineer:
                # current_risk_usd = (balance * risk_pct / 100)
                # If current_risk_usd > CAP, reduce lot.
                
                balance = self.client.get_account_balance()
                planned_risk_usd = balance * risk_pct / 100.0
                
                if planned_risk_usd > settings.MAX_TAIL_RISK_LOSS_USD:
                    # Scaling ratio
                    scale = settings.MAX_TAIL_RISK_LOSS_USD / planned_risk_usd
                    lot = lot * scale
                    
                    # Re-normalize to steps
                    symbol_info = mt5.symbol_info(symbol)
                    if symbol_info:
                        step = symbol_info.volume_step
                        lot = round(lot / step) * step
                        lot = round(lot, 2)
                        
            return lot
        return 0.01

    def monitor_positions(self, symbol, positions, current_tick, atr=None):
        """
        Monitors open positions for exit conditions (Trailing Stop, BE, Partial).
        Returns a list of actions to execute.
        Action format: {'type': 'MODIFY'|'PARTIAL', 'ticket': int, ...}
        
        atr: Average True Range (optional but recommended for dynamic trailing)
        """
        actions = []
        if not positions or not current_tick:
            return actions

        # Initialize tracking sets if not present
        if not hasattr(self, 'breakeven_set'): self.breakeven_set = set()
        if not hasattr(self, 'partial_closed'): self.partial_closed = set()

        for pos in positions:
            entry_price = pos.price_open
            current_sl = pos.sl
            current_tp = pos.tp
            ticket = pos.ticket

            if pos.type == mt5.ORDER_TYPE_BUY:
                current_price = current_tick.bid
                risk = entry_price - current_sl if current_sl > 0 else 0
                profit = current_price - entry_price

                # 1. Break-Even (Risk Free)
                # Move to slightly ABOVE entry to cover fees
                if (risk > 0 and profit >= risk * settings.BREAKEVEN_RR and
                    ticket not in self.breakeven_set):
                    
                    # Target: Entry + 10% of risk (buffer)
                    be_sl = entry_price + (risk * 0.1) 
                    
                    if be_sl > current_sl:
                        actions.append({
                            'type': 'MODIFY', 'ticket': ticket, 
                            'sl': be_sl, 'tp': current_tp, 'reason': 'Break-Even'
                        })
                        self.breakeven_set.add(ticket)

                # 2. Partial Close (Bank Profits)
                # Logic: If 50% to TP, close partial fraction
                if (current_tp > 0 and ticket not in self.partial_closed):
                    tp_dist = current_tp - entry_price
                    # If we are 60% of the way to TP, take some off
                    if profit >= tp_dist * 0.6: 
                        actions.append({
                            'type': 'PARTIAL', 'ticket': ticket, 
                            'fraction': settings.PARTIAL_CLOSE_FRACTION, 'reason': 'Partial Profit'
                        })
                        self.partial_closed.add(ticket)

                # 3. Trailing Stop (Dynamic ATR or Fixed %)
                if entry_price > 0:
                    new_sl = None
                    reason = ""
                    
                    # Preference: ATR Based
                    if atr and atr > 0:
                        # Activate if profit > 2 ATR (settings.TRAILING_STOP_ATR_ACTIVATE)
                        if profit >= settings.TRAILING_STOP_ATR_ACTIVATE * atr:
                            # Trail behind by 0.5 ATR (settings.TRAILING_STOP_ATR_STEP)
                            proposed_sl = current_price - (settings.TRAILING_STOP_ATR_STEP * atr)
                            if proposed_sl > current_sl:
                                new_sl = proposed_sl
                                reason = f"Trailing Stop (ATR {atr:.5f})"
                                
                    # Fallback: Fixed % (Legacy)
                    else:
                        profit_pct = profit / entry_price
                        if profit_pct > settings.TRAILING_STOP_ACTIVATE_PERCENT:
                            proposed_sl = current_price - (settings.TRAILING_STOP_STEP_PERCENT * entry_price)
                            if proposed_sl > current_sl:
                                new_sl = proposed_sl
                                reason = "Trailing Stop (%)"
                                
                    # OPTIMIZATION: Only modify if change is significant (> 1 pip/point)
                    if new_sl:
                        # Get point size (approximate if not available, or use small value)
                        # For EURUSD point is 0.00001, for JPY 0.001
                        # We use 10 points as threshold
                        threshold = 0.0001 if "JPY" not in symbol else 0.01 
                        if abs(new_sl - current_sl) > threshold:
                            actions.append({
                                'type': 'MODIFY', 'ticket': ticket, 
                                'sl': new_sl, 'tp': current_tp, 'reason': reason
                            })

            elif pos.type == mt5.ORDER_TYPE_SELL:
                current_price = current_tick.ask
                risk = current_sl - entry_price if current_sl > 0 else 0
                profit = entry_price - current_price

                # 1. Break-Even
                if (risk > 0 and profit >= risk * settings.BREAKEVEN_RR and
                    ticket not in self.breakeven_set):
                    
                    be_sl = entry_price - (risk * 0.1)
                    
                    if be_sl < current_sl or current_sl == 0:
                        actions.append({
                            'type': 'MODIFY', 'ticket': ticket, 
                            'sl': be_sl, 'tp': current_tp, 'reason': 'Break-Even'
                        })
                        self.breakeven_set.add(ticket)

                # 2. Partial Close
                if (current_tp > 0 and ticket not in self.partial_closed):
                    tp_dist = entry_price - current_tp
                    if profit >= tp_dist * 0.6:
                        actions.append({
                            'type': 'PARTIAL', 'ticket': ticket, 
                            'fraction': settings.PARTIAL_CLOSE_FRACTION, 'reason': 'Partial Profit'
                        })
                        self.partial_closed.add(ticket)

                # 3. Trailing Stop
                if entry_price > 0:
                    new_sl = None
                    reason = ""
                    
                    if atr and atr > 0:
                        if profit >= settings.TRAILING_STOP_ATR_ACTIVATE * atr:
                            proposed_sl = current_price + (settings.TRAILING_STOP_ATR_STEP * atr)
                            if proposed_sl < current_sl or current_sl == 0:
                                new_sl = proposed_sl
                                reason = f"Trailing Stop (ATR {atr:.5f})"
                    else:
                        profit_pct = profit / entry_price
                        if profit_pct > settings.TRAILING_STOP_ACTIVATE_PERCENT:
                            proposed_sl = current_price + (settings.TRAILING_STOP_STEP_PERCENT * entry_price)
                            if proposed_sl < current_sl or current_sl == 0:
                                new_sl = proposed_sl
                                reason = "Trailing Stop (%)"

                    # OPTIMIZATION: Threshold check
                    if new_sl:
                        threshold = 0.0001 if "JPY" not in symbol else 0.01
                         # For Sell, new_sl is lower (closer to price) or we are moving it down?
                         # Trailing for SELL: SL moves DOWN as price drops.
                         # current_sl is above price. new_sl should be lower than current_sl.
                        if abs(new_sl - current_sl) > threshold:
                            actions.append({
                                'type': 'MODIFY', 'ticket': ticket, 
                                'sl': new_sl, 'tp': current_tp, 'reason': reason
                            })

        return actions
