"""
Risk Manager Layer
Centralizes all pre-trade risk checks and position sizing.
"""
import time
import MetaTrader5 as mt5
from config import settings
from utils.news_filter import is_news_blackout, get_active_events
from utils.correlation_filter import check_correlation_conflict
from utils.shared_state import SharedState

class RiskManager:
    def __init__(self, mt5_client=None):
        self.client = mt5_client
        self.state = SharedState()
        
        # Restore Daily Trades from Shared State
        cached_count = self.state.get("daily_trades", 0)
        # Reset if new day (simple check, ideally check date)
        self.daily_trades = cached_count
        self.last_trade_time = {}  # {symbol: timestamp}

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
        if self.daily_trades >= settings.MAX_DAILY_TRADES:
            return False, "Daily Limit Reached"

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
        
        spread_pips = (tick.ask - tick.bid) / (0.0001 if "JPY" not in symbol else 0.01)
        if spread_pips > settings.MAX_SPREAD_PIPS:
            # Check if market is even open/active by spread
            return False, f"Spread High ({spread_pips:.1f} > {settings.MAX_SPREAD_PIPS})"

        # 4. News Filter
        is_blocked, event_name = is_news_blackout(symbol)
        if is_blocked:
            return False, f"News Blackout: {event_name}"

        return True, "OK"

    def check_execution(self, symbol, direction, active_positions=[]):
        """
        Final checks run just BEFORE placing an order.
        Checks: Correlation.
        """
        # 5. Correlation Filter
        conflict, reason = check_correlation_conflict(symbol, direction, active_positions)
        if conflict:
            return False, f"Correlation Conflict: {reason}"

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
            return self.client.calculate_lot_size(symbol, sl_pips, risk_pct)
        return 0.01

    def monitor_positions(self, symbol, positions, current_tick):
        """
        Monitors open positions for exit conditions (Trailing Stop, BE, Partial).
        Returns a list of actions to execute.
        Action format: {'type': 'MODIFY'|'PARTIAL', 'ticket': int, ...}
        """
        actions = []
        if not positions or not current_tick:
            return actions

        # Initialize tracking sets if not present (handled in __init__ in future, but safe check here)
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

                # 1. Break-Even
                if (risk > 0 and profit >= risk * settings.BREAKEVEN_RR and
                    ticket not in self.breakeven_set):
                    be_sl = entry_price + (risk * 0.1)
                    if be_sl > current_sl:
                        actions.append({
                            'type': 'MODIFY', 'ticket': ticket, 
                            'sl': be_sl, 'tp': current_tp, 'reason': 'Break-Even'
                        })
                        self.breakeven_set.add(ticket)

                # 2. Partial Close
                if (current_tp > 0 and ticket not in self.partial_closed):
                    tp_dist = current_tp - entry_price
                    if profit >= tp_dist * 0.5:
                        actions.append({
                            'type': 'PARTIAL', 'ticket': ticket, 
                            'fraction': settings.PARTIAL_CLOSE_FRACTION, 'reason': 'Partial Profit'
                        })
                        self.partial_closed.add(ticket)

                # 3. Trailing Stop
                if entry_price > 0:
                    profit_pct = profit / entry_price
                    if profit_pct > settings.TRAILING_STOP_ACTIVATE_PERCENT:
                        new_sl = current_price - (settings.TRAILING_STOP_STEP_PERCENT * entry_price)
                        if new_sl > current_sl:
                            actions.append({
                                'type': 'MODIFY', 'ticket': ticket, 
                                'sl': new_sl, 'tp': current_tp, 'reason': 'Trailing Stop'
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
                    if profit >= tp_dist * 0.5:
                        actions.append({
                            'type': 'PARTIAL', 'ticket': ticket, 
                            'fraction': settings.PARTIAL_CLOSE_FRACTION, 'reason': 'Partial Profit'
                        })
                        self.partial_closed.add(ticket)

                # 3. Trailing Stop
                if entry_price > 0:
                    profit_pct = profit / entry_price
                    if profit_pct > settings.TRAILING_STOP_ACTIVATE_PERCENT:
                        new_sl = current_price + (settings.TRAILING_STOP_STEP_PERCENT * entry_price)
                        if new_sl < current_sl or current_sl == 0:
                            actions.append({
                                'type': 'MODIFY', 'ticket': ticket, 
                                'sl': new_sl, 'tp': current_tp, 'reason': 'Trailing Stop'
                            })

        return actions
