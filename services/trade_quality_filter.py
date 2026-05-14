"""
TradeQualityFilter — Pre-trade quality gate that grades and filters trade signals.

Subscribes to: SESSION_TRADE_SIGNAL
Publishes: TRADE_QUALITY_GRADED (with grade + pass/fail info)
           TRADE_REJECTED (when a signal fails quality checks)

Filters:
  1. News proximity - reject trades within N minutes of major news
  2. Spread quality - reject when spread > Nx session average
  3. Volatility spike - reject when 5-bar ATR > Nx session average ATR
  4. Round number - reject when price near major round levels (manipulation zones)

Trade Grading:
  A = all filters pass with margin
  B = passes but one filter is marginal
  C = passes but multiple filters near threshold
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from config import settings

logger = logging.getLogger("TradeQualityFilter")


class TradeQualityFilter(BaseService):
    """
    Evaluates trade signal quality and assigns grades.

    Subscribes to SESSION_TRADE_SIGNAL, evaluates quality filters, and emits
    TRADE_QUALITY_GRADED with the grade. Emits TRADE_REJECTED for signals
    that fail quality checks when TRADE_QUALITY_FILTER_ENABLED is True.
    """

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)

        # Session average spread tracking (EMA per session)
        self._session_avg_spread: Dict[str, float] = {}
        # Session average ATR tracking (rolling 50-bar average per session)
        self._session_avg_atr: Dict[str, float] = {}
        # Recent ATR values for 5-bar calculation
        self._recent_atr_bars: Dict[str, list] = {}
        # EMA alpha for spread tracking
        self._spread_ema_alpha = 0.1
        # ATR history length for session average
        self._atr_history_len = 50

        # Settings
        self._enabled = getattr(settings, 'TRADE_QUALITY_FILTER_ENABLED', True)
        self._news_buffer_minutes = getattr(settings, 'NEWS_QUALITY_BUFFER_MINUTES', 15)
        self._spread_multiplier = getattr(settings, 'SPREAD_QUALITY_MULTIPLIER', 2.0)
        self._volatility_multiplier = getattr(settings, 'VOLATILITY_SPIKE_MULTIPLIER', 3.0)
        self._round_number_buffer_pips = getattr(settings, 'ROUND_NUMBER_BUFFER_PIPS', 50)
        self._round_number_interval = getattr(settings, 'ROUND_NUMBER_INTERVAL', 50)

    @property
    def name(self) -> str:
        return "TradeQualityFilter"

    async def _setup(self):
        """Subscribe to trade signals and market data."""
        self.bus.subscribe(EventTypes.SESSION_TRADE_SIGNAL, self._on_session_trade_signal)
        self.bus.subscribe(EventTypes.MARKET_DATA_READY, self._on_market_data)
        self.logger.info(
            f"[{self.name}] Quality filter {'ENABLED' if self._enabled else 'DISABLED'}"
        )

    async def _on_market_data(self, event: Event):
        """
        Update spread and ATR tracking from market data events.
        Expects payload with 'spread', 'atr', and 'session' fields.
        """
        payload = event.payload
        session = payload.get("session", "unknown")
        spread = payload.get("spread")
        atr = payload.get("atr")

        # Update spread EMA
        if spread is not None and spread > 0:
            if session in self._session_avg_spread:
                prev = self._session_avg_spread[session]
                self._session_avg_spread[session] = (
                    self._spread_ema_alpha * spread
                    + (1 - self._spread_ema_alpha) * prev
                )
            else:
                self._session_avg_spread[session] = spread

        # Update ATR tracking
        if atr is not None and atr > 0:
            if session not in self._recent_atr_bars:
                self._recent_atr_bars[session] = []

            self._recent_atr_bars[session].append(atr)
            # Keep only last 50 bars for session average
            if len(self._recent_atr_bars[session]) > self._atr_history_len:
                self._recent_atr_bars[session] = self._recent_atr_bars[session][-self._atr_history_len:]

            # Update session average ATR
            bars = self._recent_atr_bars[session]
            self._session_avg_atr[session] = sum(bars) / len(bars)

    async def _on_session_trade_signal(self, event: Event):
        """Evaluate trade signal quality and emit grading/rejection events."""
        if not self._enabled:
            return

        # Skip if already quality-checked (prevent re-processing)
        if event.payload.get("quality_checked"):
            return

        payload = event.payload
        symbol = payload.get("symbol", "XAUUSD")
        price = payload.get("price", 0)
        spread = payload.get("spread", 0)
        session = payload.get("session", "unknown")
        atr = payload.get("atr", 0)

        # Run all filters
        filter_results = self._evaluate_filters(
            symbol=symbol,
            price=price,
            spread=spread,
            session=session,
            atr=atr,
        )

        # Check if any filter rejected the signal
        rejected_filters = [
            (name, reason) for name, (passed, reason, _marginal) in filter_results.items()
            if not passed
        ]

        if rejected_filters:
            # Signal rejected - emit TRADE_REJECTED
            reject_reasons = [f"{name}: {reason}" for name, reason in rejected_filters]
            await self.emit(EventTypes.TRADE_REJECTED, {
                "symbol": symbol,
                "reason": "quality_filter",
                "filter_reasons": reject_reasons,
                "session": session,
                "price": price,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            self.logger.info(
                f"[{self.name}] REJECTED {symbol} signal: {', '.join(reject_reasons)}"
            )
            return

        # Signal passed - assign quality grade
        grade = self._calculate_grade(filter_results)

        # Emit TRADE_QUALITY_GRADED
        graded_payload = {
            **payload,
            "quality_grade": grade,
            "quality_checked": True,
            "filter_details": {
                name: {"passed": passed, "marginal": marginal}
                for name, (passed, _reason, marginal) in filter_results.items()
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.emit(EventTypes.TRADE_QUALITY_GRADED, graded_payload)
        self.logger.info(
            f"[{self.name}] GRADED {symbol} signal: Grade {grade} (session={session})"
        )

    def _evaluate_filters(
        self,
        symbol: str,
        price: float,
        spread: float,
        session: str,
        atr: float,
    ) -> Dict[str, Tuple[bool, str, bool]]:
        """
        Run all quality filters on the signal.

        Returns dict of filter_name -> (passed: bool, reason: str, marginal: bool)
        where marginal means the filter passed but is near threshold.
        """
        results = {}

        # 1. News proximity filter
        results["news_proximity"] = self._check_news_proximity(symbol)

        # 2. Spread quality filter
        results["spread_quality"] = self._check_spread_quality(spread, session)

        # 3. Volatility spike filter
        results["volatility_spike"] = self._check_volatility_spike(atr, session)

        # 4. Round number filter
        results["round_number"] = self._check_round_number(price)

        return results

    def _check_news_proximity(self, symbol: str) -> Tuple[bool, str, bool]:
        """
        Check if we are within NEWS_QUALITY_BUFFER_MINUTES of major news.

        Returns (passed, reason, marginal).
        """
        try:
            from utils.news_filter import is_news_blackout, get_upcoming_events

            now_utc = datetime.now(timezone.utc)

            # Direct blackout check
            is_blackout, event_name = is_news_blackout(symbol, now_utc)
            if is_blackout:
                return (False, f"News blackout active: {event_name}", False)

            # Check upcoming events within buffer window
            upcoming = get_upcoming_events(symbol, now_utc, lookahead_hours=1)
            buffer = timedelta(minutes=self._news_buffer_minutes)

            for ev in upcoming:
                time_to_event = ev['dt_utc'] - now_utc
                if time_to_event <= buffer:
                    return (False, f"News within {self._news_buffer_minutes}min: {ev['name']}", False)

                # Marginal: within 30 minutes (double the buffer)
                double_buffer = timedelta(minutes=self._news_buffer_minutes * 2)
                if time_to_event <= double_buffer:
                    return (True, "", True)

            return (True, "", False)

        except Exception as e:
            # If news filter fails, pass but mark as marginal
            self.logger.debug(f"News filter check failed: {e}")
            return (True, "", True)

    def _check_spread_quality(self, spread: float, session: str) -> Tuple[bool, str, bool]:
        """
        Check if current spread is within acceptable bounds relative to session average.

        Returns (passed, reason, marginal).
        """
        if spread <= 0:
            return (True, "", False)

        avg_spread = self._session_avg_spread.get(session)
        if avg_spread is None or avg_spread <= 0:
            # No data yet - pass but marginal (cannot verify quality)
            return (True, "", True)

        ratio = spread / avg_spread

        # Reject: spread > multiplier * average
        if ratio > self._spread_multiplier:
            return (
                False,
                f"Spread {spread:.2f} > {self._spread_multiplier}x avg {avg_spread:.2f}",
                False,
            )

        # Marginal: spread between 1.0x and threshold
        # Consider marginal if ratio > 75% of the rejection threshold
        marginal_threshold = self._spread_multiplier * 0.75
        if ratio > marginal_threshold:
            return (True, "", True)

        return (True, "", False)

    def _check_volatility_spike(self, current_atr: float, session: str) -> Tuple[bool, str, bool]:
        """
        Check for volatility spikes (5-bar ATR vs session average ATR).

        Returns (passed, reason, marginal).
        """
        if current_atr <= 0:
            return (True, "", False)

        session_avg = self._session_avg_atr.get(session)
        if session_avg is None or session_avg <= 0:
            # No baseline yet - pass but marginal
            return (True, "", True)

        ratio = current_atr / session_avg

        # Reject: ATR > multiplier * session average
        if ratio > self._volatility_multiplier:
            return (
                False,
                f"ATR spike {current_atr:.4f} > {self._volatility_multiplier}x avg {session_avg:.4f}",
                False,
            )

        # Marginal: ratio > 75% of rejection threshold
        marginal_threshold = self._volatility_multiplier * 0.75
        if ratio > marginal_threshold:
            return (True, "", True)

        return (True, "", False)

    def _check_round_number(self, price: float) -> Tuple[bool, str, bool]:
        """
        Check if price is near a major round number (manipulation zone).

        For XAUUSD: 1 pip = $0.10, so 50 pips = $5.00.
        Round number interval of 50 means multiples of $50 (2300, 2350, 2400...).

        Returns (passed, reason, marginal).
        """
        if price <= 0:
            return (True, "", False)

        interval = float(self._round_number_interval)
        # For gold, buffer in price terms: 50 pips * $0.10/pip = $5.00
        buffer_price = self._round_number_buffer_pips * 0.10

        # Find nearest round number
        nearest_round = round(price / interval) * interval
        distance = abs(price - nearest_round)

        # Reject: within buffer of round number
        if distance <= buffer_price:
            return (
                False,
                f"Price {price:.2f} within {buffer_price:.2f} of round level {nearest_round:.0f}",
                False,
            )

        # Marginal: within 2x buffer (close to danger zone)
        if distance <= buffer_price * 2:
            return (True, "", True)

        return (True, "", False)

    def _calculate_grade(self, filter_results: Dict[str, Tuple[bool, str, bool]]) -> str:
        """
        Calculate quality grade based on filter results.

        A = all filters pass with margin (no marginal flags)
        B = passes but one filter is marginal
        C = passes but multiple filters are near threshold
        """
        marginal_count = sum(
            1 for _name, (_passed, _reason, marginal) in filter_results.items()
            if marginal
        )

        if marginal_count == 0:
            return "A"
        elif marginal_count == 1:
            return "B"
        else:
            return "C"

    def update_spread(self, session: str, spread: float):
        """
        Manually update session spread tracking.
        Useful for testing or direct integration.
        """
        if spread <= 0:
            return

        if session in self._session_avg_spread:
            prev = self._session_avg_spread[session]
            self._session_avg_spread[session] = (
                self._spread_ema_alpha * spread
                + (1 - self._spread_ema_alpha) * prev
            )
        else:
            self._session_avg_spread[session] = spread

    def update_atr(self, session: str, atr: float):
        """
        Manually update session ATR tracking.
        Useful for testing or direct integration.
        """
        if atr <= 0:
            return

        if session not in self._recent_atr_bars:
            self._recent_atr_bars[session] = []

        self._recent_atr_bars[session].append(atr)
        if len(self._recent_atr_bars[session]) > self._atr_history_len:
            self._recent_atr_bars[session] = self._recent_atr_bars[session][-self._atr_history_len:]

        bars = self._recent_atr_bars[session]
        self._session_avg_atr[session] = sum(bars) / len(bars)
