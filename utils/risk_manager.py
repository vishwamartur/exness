"""
Risk Manager Layer
Centralizes all pre-trade risk checks and position sizing.
"""
import time
import numpy as np
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
        
        # 1c. Per-Symbol Cooldown (prevent over-trading same pair)
        last_trade = self.last_trade_time.get(symbol, 0)
        minutes_since = (time.time() - last_trade) / 60
        min_interval = getattr(settings, 'MIN_TRADE_INTERVAL_MINUTES', 15)
        if minutes_since < min_interval:
            return False, f"Symbol Cooldown ({minutes_since:.0f}min < {min_interval}min)"
            
        # 1a. Kill Switch & Payoff Mandate
        # Update stats if stale (e.g. every 5 mins or on every check if fast enough? Let's do 5 mins)
        if time.time() - self.last_stats_update.get(symbol, 0) > 300:
             self._update_symbol_stats(symbol)
             
        # Override Check (User Request)
        if symbol in getattr(settings, "RISK_OVERRIDE_SYMBOLS", []):
            # Skip checks for whitelisted symbols
            pass
        else:
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

        # 5. Session Filter (London Open & NY Open typically)
        #    Crypto trades 24/7 — exempt from session gate
        if getattr(settings, 'SESSION_FILTER', False):
            crypto_bases = ('BTC', 'ETH', 'LTC', 'XRP', 'BCH', 'BNB', 'SOL', 'ADA', 'DOT')
            is_crypto = any(symbol.upper().startswith(b) for b in crypto_bases)
            if not is_crypto:
                current_hour = datetime.now(timezone.utc).hour
                in_session = any(
                    s['start'] <= current_hour < s['end']
                    for s in getattr(settings, 'TRADE_SESSIONS', {}).values()
                )
                if not in_session:
                    session_str = ', '.join(
                        f"{name} ({s['start']}:00-{s['end']}:00 UTC)"
                        for name, s in getattr(settings, 'TRADE_SESSIONS', {}).items()
                    )
                    return False, f"Off-Session (UTC {current_hour}:00 | Active: {session_str})"

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
        Checks: Max concurrent trades, Correlation, Profitability.
        """
        # 5a. Hard cap on concurrent trades
        max_trades = getattr(settings, 'MAX_CONCURRENT_TRADES', 3)
        if len(active_positions) >= max_trades:
            return False, f"Max Concurrent Trades ({max_trades}) reached"

        # 5b. Live Correlation Filter (dynamic — uses recent price returns)
        conflict, reason = self._check_live_correlation(symbol, direction, active_positions)
        if conflict:
            return False, f"Correlation Conflict: {reason}"
            
        # 5c. Covariance Risk Matrix (Macro-Hedging)
        over_exposed, covar_reason = self.calculate_portfolio_covariance(symbol, direction, active_positions)
        if over_exposed:
            return False, f"Covariance Matrix Guard: {covar_reason}"

        # 6. Cost-Aware Profitability Gate (Strict)
        # Reject any trade where total cost (spread + commission) exceeds 30% of expected profit.
        try:
            tick = mt5.symbol_info_tick(symbol)
            sym_info = mt5.symbol_info(symbol)
            if not tick or not sym_info:
                print(f"[RISK] Warning: profitability gate skipped for {symbol} — no tick/symbol info")
            else:
                spread = (tick.ask - tick.bid)
                entry = tick.ask if direction == "BUY" else tick.bid
                gross_profit_points = abs(tp - entry)
                
                point = sym_info.point
                if point == 0: point = 0.00001
                
                spread_pips = spread / point
                # Commission buffer: ~0.7 pips for Raw Spread accounts
                commission_pips = 1.0
                total_cost_pips = spread_pips + commission_pips
                profit_pips = gross_profit_points / point
                
                # STRICT: Cost must NOT exceed 30% of planned profit
                cost_ratio = total_cost_pips / max(profit_pips, 0.01)
                if cost_ratio > 0.30:
                    return False, f"Cost Too High ({cost_ratio*100:.0f}% of TP | Cost:{total_cost_pips:.1f} vs Profit:{profit_pips:.1f} pips)"
                    
                # STRICT: Net profit must be at least 3x the cost
                net_pips = profit_pips - total_cost_pips
                min_net = total_cost_pips * settings.MIN_NET_PROFIT_RATIO
                if net_pips < min_net:
                    return False, f"Low Net Profit ({net_pips:.1f} < {min_net:.1f} pips)"
                    
        except Exception as e:
            print(f"[RISK] Warning: profitability gate error for {symbol}: {e}")
            
        return True, "OK"

    def record_trade(self, symbol):
        """Updates internal counters after a successful trade."""
        self.daily_trades += 1
        self.state.set("daily_trades", self.daily_trades)
        self.last_trade_time[symbol] = time.time()

    def _check_live_correlation(self, symbol, direction, active_positions):
        """
        Live correlation check using recent M1 returns.
        Falls back to static group check if data unavailable.
        """
        if not active_positions:
            return False, ""
        try:
            # Fetch last 60 bars for candidate symbol
            rates_c = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 60)
            if rates_c is None or len(rates_c) < 10:
                # Fall back to static
                return check_correlation_conflict(symbol, direction, active_positions)
            returns_c = np.diff(np.array([r['close'] for r in rates_c]))

            for pos in active_positions:
                pos_symbol = pos.symbol if hasattr(pos, 'symbol') else str(pos)
                pos_dir = 'BUY' if (hasattr(pos, 'type') and pos.type == 0) else 'SELL'
                rates_p = mt5.copy_rates_from_pos(pos_symbol, mt5.TIMEFRAME_M1, 0, 60)
                if rates_p is None or len(rates_p) < 10:
                    continue
                returns_p = np.diff(np.array([r['close'] for r in rates_p]))
                min_len = min(len(returns_c), len(returns_p))
                if min_len < 5:
                    continue
                corr = np.corrcoef(returns_c[-min_len:], returns_p[-min_len:])[0, 1]

                # Block if correlation is very high AND adding to same directional exposure
                if abs(corr) > 0.85:
                    # Positively correlated: block same direction
                    # Negatively correlated: block opposite direction
                    if (corr > 0 and direction == pos_dir) or (corr < 0 and direction != pos_dir):
                        return True, f"Live Corr {corr:.2f} with {pos_symbol} ({pos_dir})"
        except Exception:
            # Fall back to static on any error
            return check_correlation_conflict(symbol, direction, active_positions)
        return False, ""
        
    def calculate_portfolio_covariance(self, new_symbol, new_direction, active_positions):
        """
        Covariance Risk Guard.
        Deconstructs MT5 pairs into Quote/Base vectors to prevent 
        massive directional exposure to a single currency (e.g. USD).
        """
        if not active_positions:
            return False, ""
            
        currency_exposure = {}
        
        # 1. Parse Existing Open Positions
        for pos in active_positions:
            pos_symbol = pos.symbol if hasattr(pos, 'symbol') else str(pos)
            pos_vol = pos.volume if hasattr(pos, 'volume') else 0.01  # Default fallback
            pos_dir = 'BUY' if (hasattr(pos, 'type') and pos.type == 0) else 'SELL'
            
            # Simple Pair Extraction (e.g., EURUSD -> Base: EUR, Quote: USD)
            if len(pos_symbol) >= 6:
                base = pos_symbol[:3]
                quote = pos_symbol[3:6]
                
                # If Long EURUSD -> Long EUR, Short USD
                if pos_dir == 'BUY':
                    currency_exposure[base] = currency_exposure.get(base, 0.0) + pos_vol
                    currency_exposure[quote] = currency_exposure.get(quote, 0.0) - pos_vol
                # If Short EURUSD -> Short EUR, Long USD
                else:
                    currency_exposure[base] = currency_exposure.get(base, 0.0) - pos_vol
                    currency_exposure[quote] = currency_exposure.get(quote, 0.0) + pos_vol
                    
        # Clone current state to compare if the new trade actually helps
        old_currency_exposure = currency_exposure.copy()
                    
        # 2. Add the PROPOSED trade
        # Assume minimum lot if not passed (worst-case scalar)
        proposed_vol = getattr(settings, 'LOT_SIZE', 0.01)
        if len(new_symbol) >= 6:
            base = new_symbol[:3]
            quote = new_symbol[3:6]
            
            if new_direction == 'BUY':
                currency_exposure[base] = currency_exposure.get(base, 0.0) + proposed_vol
                currency_exposure[quote] = currency_exposure.get(quote, 0.0) - proposed_vol
            else:
                currency_exposure[base] = currency_exposure.get(base, 0.0) - proposed_vol
                currency_exposure[quote] = currency_exposure.get(quote, 0.0) + proposed_vol
                
        # 3. Evaluate Gross Vector Concentration
        # Normalizing to a 1.0 limit based on the Max allowable setting.
        # e.g., if MAX_PORTFOLIO_CORRELATION is 0.75, and we reach 0.76 exposure in USD, we block.
        max_limit = getattr(settings, 'MAX_PORTFOLIO_CORRELATION', 0.75) 
        
        # To normalize, we treat max_limit as a direct proxy for fractional lot caps.
        # (Alternatively, could ratio it to the Account Balance, but lot-sum is faster)
        # Assuming typical micro-accounts (0.01 lots), exposure > 0.03 in one direction is dangerous.
        
        # Because different users have different standard deviations of lot sizes, we'll bound checking 
        # to a relative count if max_limit is < 1.0. 
        # E.g if Limit is 0.75, assume maximum 3 concurrent directions allowed.
        directional_cap = 0.01 * 3 # Baseline 3 positions
        
        for curr, new_exp in currency_exposure.items():
            # Only block if the new_exp has GROWN beyond the cap
            # AND its absolute magnitude makes it worse than the previous state
            old_exp = old_currency_exposure.get(curr, 0.0)
            
            if abs(new_exp) > directional_cap:
                # Did we make it worse?
                if abs(new_exp) > abs(old_exp):
                    side = "LONG" if new_exp > 0 else "SHORT"
                    return True, f"Dangerously {side} on [{curr}]. Risk grows from {old_exp:.2f} to {new_exp:.2f} lots."

        return False, ""

    def calculate_position_size(self, symbol, sl_pips, confluence_score, scaling_factor=1.0, ml_prob=None, emotion_state='NEUTRAL', emotion_score=0.5):
        """
        Calculates dynamic lot size using Quarter-Kelly when history available.
        Falls back to confluence tiers when insufficient trade history.
        
        ml_prob: float (0-1) — ML ensemble predicted win probability.
                 When provided, blends with historical win rate for smarter sizing.
        emotion_state: Market fear/greed state used to modulate risk overlay.
        """
        # ── Emotion Risk Overlay ─────────────────────────────────────────────
        if emotion_state in ['FEAR', 'PANIC']:
            if symbol in ['XAUUSD', 'XAUUSDm', 'USDCHF']:
                # Safe havens thrive on fear, maintain standard size
                pass
            else:
                # Risk-on assets face high volatility, reduce risk by 40%
                scaling_factor *= 0.6
        elif emotion_state in ['GREED', 'EUPHORIA']:
            # Overextended market, fear of impending mean-reversion, reduce risk 20%
            scaling_factor *= 0.8

        # ── Kelly Criterion ──────────────────────────────────────────────────
        kelly_risk_pct = None
        if getattr(settings, 'USE_KELLY', False):
            stats = self.symbol_stats.get(symbol, {})
            hist_win_rate = stats.get('win_rate', 0)
            avg_win  = stats.get('avg_win', 0)
            avg_loss = stats.get('avg_loss', 0)
            count    = stats.get('count', 0)

            # Blend historical win rate with ML prediction if available
            # ML-weighted (70%) when confident, historical (30%) for stability
            if ml_prob is not None and ml_prob > 0:
                if count >= settings.KELLY_MIN_TRADES and hist_win_rate > 0:
                    # Blend: 70% ML prediction + 30% historical
                    win_rate = (ml_prob * 0.7) + (hist_win_rate * 0.3)
                else:
                    # No history — use ML directly but apply conservative discount
                    win_rate = ml_prob * 0.8  # 20% haircut for safety
            else:
                win_rate = hist_win_rate

            if count >= settings.KELLY_MIN_TRADES and avg_loss > 0 and avg_win > 0:
                rr = avg_win / avg_loss           # reward-to-risk ratio
                kelly_f = win_rate - (1 - win_rate) / rr  # full Kelly fraction
                kelly_f = max(0.0, kelly_f)       # never negative
                kelly_risk_pct = min(
                    kelly_f * settings.KELLY_FRACTION * 100,  # quarter-Kelly → %
                    settings.MAX_RISK_PERCENT                  # hard cap
                )
            elif ml_prob is not None and ml_prob > 0.6:
                # Even without trade history, use ML-only Kelly with conservative defaults
                # Assume R:R from ATR settings as proxy
                atr_tp = getattr(settings, 'ATR_TP_MULTIPLIER', 4.0)
                atr_sl = getattr(settings, 'ATR_SL_MULTIPLIER', 2.0)
                rr = atr_tp / atr_sl if atr_sl > 0 else 2.0
                kelly_f = (ml_prob * 0.8) - (1 - ml_prob * 0.8) / rr
                kelly_f = max(0.0, kelly_f)
                kelly_risk_pct = min(
                    kelly_f * settings.KELLY_FRACTION * 100,
                    settings.MAX_RISK_PERCENT
                )

        # ── Confluence Tier Fallback ─────────────────────────────────────────
        base_risk = settings.RISK_PERCENT
        max_risk  = settings.MAX_RISK_PERCENT
        if confluence_score >= 6:
            tier_risk = max_risk
        elif confluence_score >= 5:
            tier_risk = (base_risk + max_risk) / 2
        else:
            tier_risk = base_risk

        risk_pct = kelly_risk_pct if kelly_risk_pct is not None else tier_risk
        risk_pct *= scaling_factor

        # Ensure we have client reference to calculate
        if self.client:
            lot = self.client.calculate_lot_size(symbol, sl_pips, risk_pct)
            
            # --- TAIL RISK CLAMP ---
            if symbol in getattr(settings, "TAIL_RISK_SYMBOLS", []):
                balance = self.client.get_account_balance()
                planned_risk_usd = balance * risk_pct / 100.0
                
                if planned_risk_usd > settings.MAX_TAIL_RISK_LOSS_USD:
                    scale = settings.MAX_TAIL_RISK_LOSS_USD / planned_risk_usd
                    lot = lot * scale
                    symbol_info = mt5.symbol_info(symbol)
                    if symbol_info:
                        step = symbol_info.volume_step
                        lot = round(lot / step) * step
                        lot = round(lot, 2)
                        
            return lot
        return 0.01


    def monitor_positions(self, symbol, positions, current_tick, atr=None, df=None, emotion_state='NEUTRAL', emotion_score=0.5):
        """
        Smart Exit System: Let winners run, cut losers fast.
        
        Uses progressive trailing SL (tightens as profit grows) instead of fixed TP.
        Detects momentum reversals to cut losing trades early.
        
        Args:
            symbol: Trading symbol
            positions: List of MT5 positions
            current_tick: Current tick data
            atr: Current ATR value (required for smart exits)
            df: DataFrame with technical features (optional, for momentum analysis)
            emotion_state: Market emotion state for dynamic trailing adjustments
        
        Returns: List of actions: {'type': 'MODIFY'|'PARTIAL'|'CLOSE', ...}
        """
        actions = []
        if not positions or not current_tick:
            return actions

        # Initialize tracking sets
        if not hasattr(self, 'breakeven_set'): self.breakeven_set = set()
        if not hasattr(self, 'partial_closed'): self.partial_closed = set()
        if not hasattr(self, 'trail_high'): self.trail_high = {}  # {ticket: best_price_seen}

        smart_exit = getattr(settings, 'SMART_EXIT_ENABLED', True)

        for pos in positions:
            entry_price = pos.price_open
            current_sl = pos.sl
            current_tp = pos.tp
            ticket = pos.ticket

            is_buy = pos.type == mt5.ORDER_TYPE_BUY
            current_price = current_tick.bid if is_buy else current_tick.ask
            
            # Calculate profit in price distance
            if is_buy:
                profit = current_price - entry_price
                risk = entry_price - current_sl if current_sl > 0 else (atr or 0)
            else:
                profit = entry_price - current_price
                risk = current_sl - entry_price if current_sl > 0 else (atr or 0)

            # Profit in ATR multiples (key metric for all decisions)
            profit_atr = profit / atr if atr and atr > 0 else 0

            if not smart_exit or not atr or atr <= 0:
                # Fallback: simple trailing at 1.5x ATR
                if atr and atr > 0 and profit > 0:
                    self._apply_simple_trail(actions, pos, current_price, current_sl, current_tp, atr, is_buy, symbol)
                continue

            # ═══════════════════════════════════════════════════════════════
            #  SMART EXIT LOGIC
            # ═══════════════════════════════════════════════════════════════

            # ── 1. EARLY LOSS CUTTING (momentum-based) ────────────────────
            if getattr(settings, 'EARLY_CUT_ENABLED', True) and profit < 0:
                cut_threshold = -getattr(settings, 'EARLY_CUT_LOSS_ATR', 0.5) * atr
                
                if profit < cut_threshold and df is not None and len(df) >= 14:
                    should_cut, cut_reason = self._check_momentum_against(df, is_buy)
                    if should_cut:
                        actions.append({
                            'type': 'CLOSE', 'ticket': ticket,
                            'reason': f'Early Cut: {cut_reason} (loss: {profit_atr:.1f} ATR)'
                        })
                        continue  # Skip other actions for this position

            # ── 2. BREAKEVEN (eliminate risk ASAP) ────────────────────────
            be_activate = getattr(settings, 'BREAKEVEN_ACTIVATE_ATR', 0.5) * atr
            be_buffer = getattr(settings, 'BREAKEVEN_BUFFER_ATR', 0.05) * atr
            
            if profit >= be_activate and ticket not in self.breakeven_set:
                if is_buy:
                    be_sl = entry_price + be_buffer
                    if be_sl > current_sl:
                        actions.append({
                            'type': 'MODIFY', 'ticket': ticket,
                            'sl': be_sl, 'tp': current_tp,
                            'reason': f'Breakeven (profit: {profit_atr:.1f} ATR)'
                        })
                        self.breakeven_set.add(ticket)
                else:
                    be_sl = entry_price - be_buffer
                    if be_sl < current_sl or current_sl == 0:
                        actions.append({
                            'type': 'MODIFY', 'ticket': ticket,
                            'sl': be_sl, 'tp': current_tp,
                            'reason': f'Breakeven (profit: {profit_atr:.1f} ATR)'
                        })
                        self.breakeven_set.add(ticket)

            # ── 3. PARTIAL CLOSE at BE (lock some profit risk-free) ───────
            if (getattr(settings, 'PARTIAL_AT_BE', True) and 
                profit >= be_activate and 
                ticket not in self.partial_closed and
                ticket in self.breakeven_set):
                fraction = getattr(settings, 'PARTIAL_CLOSE_FRACTION', 0.30)
                actions.append({
                    'type': 'PARTIAL', 'ticket': ticket,
                    'fraction': fraction,
                    'reason': f'Partial {fraction*100:.0f}% at BE (risk-free)'
                })
                self.partial_closed.add(ticket)

            # ── 4. PROGRESSIVE TRAILING SL (the core "let winners run") ───
            trail_activate = getattr(settings, 'TRAIL_ACTIVATE_ATR', 0.3) * atr
            
            if profit >= trail_activate:
                # Calculate dynamic trail distance based on profit level
                trail_dist = self._calc_progressive_trail(profit_atr, atr)
                
                # Emotion Overlay: tighten trailing if market is extremely fearful/volatile
                if emotion_state in ['FEAR', 'PANIC']:
                    trail_dist *= 0.7  # 30% tighter to protect profits in high volatility
                
                # Track best price seen (for ratcheting)
                if ticket not in self.trail_high:
                    self.trail_high[ticket] = current_price
                else:
                    if is_buy:
                        self.trail_high[ticket] = max(self.trail_high[ticket], current_price)
                    else:
                        self.trail_high[ticket] = min(self.trail_high[ticket], current_price)
                
                best_price = self.trail_high[ticket]
                
                # Calculate trailing SL from best price seen
                if is_buy:
                    proposed_sl = best_price - trail_dist
                    # Ratchet: SL can only move UP
                    if proposed_sl > current_sl:
                        threshold = 0.01 if symbol in ['XAUUSD', 'XAUUSDm'] else 0.0001
                        if abs(proposed_sl - current_sl) > threshold:
                            actions.append({
                                'type': 'MODIFY', 'ticket': ticket,
                                'sl': proposed_sl, 'tp': current_tp,
                                'reason': f'Trail SL ↑ (profit: {profit_atr:.1f} ATR, trail: {trail_dist/atr:.1f} ATR)'
                            })
                else:
                    proposed_sl = best_price + trail_dist
                    # Ratchet: SL can only move DOWN
                    if proposed_sl < current_sl or current_sl == 0:
                        threshold = 0.01 if symbol in ['XAUUSD', 'XAUUSDm'] else 0.0001
                        if abs(proposed_sl - current_sl) > threshold:
                            actions.append({
                                'type': 'MODIFY', 'ticket': ticket,
                                'sl': proposed_sl, 'tp': current_tp,
                                'reason': f'Trail SL ↓ (profit: {profit_atr:.1f} ATR, trail: {trail_dist/atr:.1f} ATR)'
                            })

        return actions

    def _calc_progressive_trail(self, profit_atr: float, atr: float) -> float:
        """
        Calculate trail distance that TIGHTENS as profit grows.
        
        Profit Level → Trail Distance:
          0.3 ATR    → 1.0x ATR  (wide — give room to breathe)
          1.0 ATR    → 0.8x ATR  (moderate)
          2.0 ATR    → 0.6x ATR  (tighter)
          3.0+ ATR   → 0.4x ATR  (tight — lock the gains)
        """
        initial = getattr(settings, 'TRAIL_INITIAL_ATR', 1.0)
        tight = getattr(settings, 'TRAIL_TIGHT_ATR', 0.4)
        
        # Linear interpolation: from initial at 0.3 ATR to tight at 3.0 ATR
        progress = min(1.0, max(0.0, (profit_atr - 0.3) / 2.7))
        trail_multiplier = initial - (initial - tight) * progress
        
        return trail_multiplier * atr

    def _check_momentum_against(self, df, is_buy: bool) -> tuple:
        """
        Check if momentum indicators confirm the move is against the position.
        Returns (should_cut: bool, reason: str).
        """
        try:
            last = df.iloc[-1]
            rsi = last.get('rsi', 50)
            macd_hist = last.get('macd_histogram', 0)
            
            rsi_threshold = getattr(settings, 'EARLY_CUT_RSI_THRESHOLD', 35.0)
            macd_bars = getattr(settings, 'EARLY_CUT_MACD_BARS', 3)
            
            reasons = []
            score = 0
            
            if is_buy:
                # For longs: RSI dropping below threshold = bearish
                if rsi < rsi_threshold:
                    reasons.append(f"RSI bearish ({rsi:.0f})")
                    score += 1
                # MACD histogram negative for N bars
                if 'macd_histogram' in df.columns and len(df) >= macd_bars:
                    recent_macd = df['macd_histogram'].iloc[-macd_bars:]
                    if all(m < 0 for m in recent_macd):
                        reasons.append(f"MACD bearish ({macd_bars} bars)")
                        score += 1
                # Price below SMA20
                if 'sma_20' in df.columns and last['close'] < last.get('sma_20', last['close']):
                    reasons.append("Below SMA20")
                    score += 1
            else:
                # For shorts: RSI rising above (100 - threshold) = bullish
                if rsi > (100 - rsi_threshold):
                    reasons.append(f"RSI bullish ({rsi:.0f})")
                    score += 1
                if 'macd_histogram' in df.columns and len(df) >= macd_bars:
                    recent_macd = df['macd_histogram'].iloc[-macd_bars:]
                    if all(m > 0 for m in recent_macd):
                        reasons.append(f"MACD bullish ({macd_bars} bars)")
                        score += 1
                if 'sma_20' in df.columns and last['close'] > last.get('sma_20', last['close']):
                    reasons.append("Above SMA20")
                    score += 1
            
            # Need at least 2 confirming signals to cut
            if score >= 2:
                return True, " + ".join(reasons)
            
        except Exception:
            pass
        
        return False, ""

    def _apply_simple_trail(self, actions, pos, current_price, current_sl, current_tp, atr, is_buy, symbol):
        """Fallback simple trailing stop when smart exit is disabled."""
        if is_buy:
            proposed_sl = current_price - (1.5 * atr)
            if proposed_sl > current_sl:
                threshold = 0.01 if 'XAU' in symbol else 0.0001
                if abs(proposed_sl - current_sl) > threshold:
                    actions.append({
                        'type': 'MODIFY', 'ticket': pos.ticket,
                        'sl': proposed_sl, 'tp': current_tp,
                        'reason': 'Simple Trail (1.5x ATR)'
                    })
        else:
            proposed_sl = current_price + (1.5 * atr)
            if proposed_sl < current_sl or current_sl == 0:
                threshold = 0.01 if 'XAU' in symbol else 0.0001
                if abs(proposed_sl - current_sl) > threshold:
                    actions.append({
                        'type': 'MODIFY', 'ticket': pos.ticket,
                        'sl': proposed_sl, 'tp': current_tp,
                        'reason': 'Simple Trail (1.5x ATR)'
                    })
