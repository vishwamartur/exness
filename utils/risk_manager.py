"""
Risk Manager Layer
Centralizes all pre-trade risk checks and position sizing.
"""
import time
import MetaTrader5 as mt5
from config import settings
from utils.news_filter import is_news_blackout, get_active_events
from utils.correlation_filter import check_correlation_conflict

class RiskManager:
    def __init__(self, mt5_client=None):
        self.client = mt5_client
        self.daily_trades = 0
        self.last_trade_time = {}  # {symbol: timestamp}

    def check_pre_scan(self, symbol):
        """
        Fast checks run BEFORE heavy analysis.
        Checks: Daily Limit, Cooldown, Spread, News.
        """
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
        conflict, reason = check_correlation_conflict(symbol, active_positions)
        if conflict:
            return False, f"Correlation Conflict: {reason}"

        return True, "OK"

    def record_trade(self, symbol):
        """Updates internal counters after a successful trade."""
        self.daily_trades += 1
        self.last_trade_time[symbol] = time.time()

    def calculate_position_size(self, symbol, sl_pips, confluence_score):
        """
        Calculates dynamic lot size based on risk percent and confluence.
        High Confluence (6+) -> Max Risk
        Medium (5) -> Avg Risk
        Low (3-4) -> Min Risk
        """
        base_risk = settings.RISK_PERCENT 
        max_risk = settings.MAX_RISK_PERCENT

        if confluence_score >= 6:
            risk_pct = max_risk
        elif confluence_score >= 5:
            risk_pct = (base_risk + max_risk) / 2
        else:
            risk_pct = base_risk
            
        # Ensure we have client reference to calculate
        if self.client:
            return self.client.calculate_lot_size(symbol, sl_pips, risk_pct)
        
        # Fallback if no client (should not happen in live)
        return 0.01
