"""
SessionRegimeService — Time-of-day session regime detection for XAUUSD.

This is Edge #1: The most robust edge for retail XAUUSD algos.
Institutions avoid the Asian session, leaving predictable behavioral patterns.

Session Map (UTC):
  Asian     22:00–08:00  → Low vol, mean-reverting   → Range-bound reversal
  London    08:00–13:00  → High vol, breakout         → Asian range breakout
  NY        13:00–17:00  → High vol, momentum         → Trend continuation
  NY Aftn   17:00–22:00  → Declining liquidity         → FLAT (no new entries)

Subscribes to: MARKET_DATA_READY, SCAN_START
Publishes: SESSION_REGIME_UPDATE
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple

import pandas as pd

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from core.mt5_gateway import MT5Gateway
from config import settings
from utils.async_utils import run_in_executor

logger = logging.getLogger("SessionRegimeService")


# ─── Session Constants ────────────────────────────────────────────────────────

class SessionType:
    ASIAN = "ASIAN"
    LONDON = "LONDON"
    NY = "NY"
    NY_AFTERNOON = "NY_AFTERNOON"


class RegimeType:
    MEAN_REVERT = "MEAN_REVERT"    # Asian session: fade extremes
    BREAKOUT = "BREAKOUT"          # London open: break Asian range
    MOMENTUM = "MOMENTUM"          # NY: trend continuation
    FLAT = "FLAT"                  # NY Afternoon: no new trades


# ─── Session Regime Service ───────────────────────────────────────────────────

class SessionRegimeService(BaseService):
    """
    Detects which trading session is active and tracks the Asian session
    range (high/low) which is the foundation for the London breakout edge.
    """

    def __init__(self, event_bus: EventBus, gateway: MT5Gateway):
        super().__init__(event_bus)
        self.gateway = gateway

        # Asian range tracking
        self._asian_high: float = 0.0
        self._asian_low: float = float('inf')
        self._asian_range_date: Optional[str] = None  # Date of current Asian range
        self._asian_range_locked: bool = False  # Lock after Asian session ends

        # Previous day high/low (for liquidity sweep)
        self._prev_day_high: float = 0.0
        self._prev_day_low: float = float('inf')
        self._current_day_high: float = 0.0
        self._current_day_low: float = float('inf')
        self._pdh_date: Optional[str] = None

        # Current regime
        self._current_session: str = SessionType.ASIAN
        self._current_regime: str = RegimeType.MEAN_REVERT
        self._last_regime_publish: float = 0

        # ATR tracking
        self._current_atr: float = 0.0

    @property
    def name(self) -> str:
        return "SessionRegimeService"

    async def _setup(self):
        self.bus.subscribe(EventTypes.MARKET_DATA_READY, self._on_market_data)
        self.bus.subscribe(EventTypes.SCAN_START, self._on_scan_start)

        # Backfill Asian range from historical data if bot starts mid-session
        await self._backfill_asian_range()

    async def _on_scan_start(self, event: Event):
        """On each scan cycle, detect session and publish regime."""
        session, regime = self._detect_session_regime()

        # Only publish if enough time has passed (avoid flooding)
        now = time.time()
        if now - self._last_regime_publish < 5:
            return

        self._current_session = session
        self._current_regime = regime
        self._last_regime_publish = now

        await self.emit(EventTypes.SESSION_REGIME_UPDATE, {
            "session": session,
            "regime": regime,
            "asian_high": self._asian_high if self._asian_high > 0 else None,
            "asian_low": self._asian_low if self._asian_low < float('inf') else None,
            "asian_range_midpoint": self._get_asian_midpoint(),
            "asian_range_size": self._get_asian_range_size(),
            "prev_day_high": self._prev_day_high if self._prev_day_high > 0 else None,
            "prev_day_low": self._prev_day_low if self._prev_day_low < float('inf') else None,
            "atr_14": self._current_atr,
            "asian_range_locked": self._asian_range_locked,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        asian_lo_str = f"{self._asian_low:.2f}" if self._asian_low < float('inf') else "N/A"
        asian_hi_str = f"{self._asian_high:.2f}" if self._asian_high > 0 else "N/A"
        logger.info(
            f"[SESSION] {session} → {regime} | "
            f"Asian Range: {asian_lo_str}-{asian_hi_str} | "
            f"ATR: {self._current_atr:.2f}"
        )

    async def _on_market_data(self, event: Event):
        """Update Asian range and PDH/PDL from incoming price data."""
        symbol = event.payload.get("symbol", "")
        data_dict = event.payload.get("data_dict")
        tick = event.payload.get("tick")

        # Only track XAUUSD
        if "XAU" not in symbol.upper():
            return

        if not data_dict or not tick:
            return

        try:
            # Update ATR from M5 data
            df = data_dict.get(settings.TIMEFRAME)
            if df is not None and len(df) >= 20:
                self._update_atr(df)

            # Update Asian range with current tick
            current_price_high = tick.get("ask", 0) if isinstance(tick, dict) else getattr(tick, 'ask', 0)
            current_price_low = tick.get("bid", 0) if isinstance(tick, dict) else getattr(tick, 'bid', 0)

            if current_price_high <= 0 or current_price_low <= 0:
                return

            session, _ = self._detect_session_regime()

            # Track Asian range during Asian session
            if session == SessionType.ASIAN:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

                # Reset if new day
                if self._asian_range_date != today:
                    self._asian_high = 0.0
                    self._asian_low = float('inf')
                    self._asian_range_date = today
                    self._asian_range_locked = False

                if not self._asian_range_locked:
                    self._asian_high = max(self._asian_high, current_price_high)
                    self._asian_low = min(self._asian_low, current_price_low)

            elif session == SessionType.LONDON and not self._asian_range_locked:
                # Lock Asian range when London opens
                self._asian_range_locked = True
                if self._asian_high > 0 and self._asian_low < float('inf'):
                    logger.info(
                        f"[SESSION] Asian Range LOCKED: "
                        f"{self._asian_low:.2f} - {self._asian_high:.2f} "
                        f"(range: {self._asian_high - self._asian_low:.2f})"
                    )

            # Track daily high/low for PDH/PDL
            self._update_daily_levels(current_price_high, current_price_low)

            # Update PDH/PDL from historical data
            if df is not None and len(df) >= 100:
                self._update_pdh_pdl(df)

        except Exception as e:
            logger.error(f"Session regime data update error: {e}")

    # ─── Session Detection ────────────────────────────────────────────────

    def _detect_session_regime(self) -> Tuple[str, str]:
        """Determine current session and trading regime based on UTC time."""
        now = datetime.now(timezone.utc)
        hour = now.hour + now.minute / 60.0

        # Session boundaries from settings
        asian_start = getattr(settings, 'ASIAN_SESSION_START', 22.0)
        asian_end = getattr(settings, 'ASIAN_SESSION_END', 8.0)
        london_start = getattr(settings, 'LONDON_SESSION_START', 8.0)
        london_end = getattr(settings, 'LONDON_SESSION_END', 13.0)
        ny_start = getattr(settings, 'NY_SESSION_START', 13.0)
        ny_end = getattr(settings, 'NY_SESSION_END', 17.0)
        ny_aftn_start = getattr(settings, 'NY_AFTERNOON_START', 17.0)
        ny_aftn_end = getattr(settings, 'NY_AFTERNOON_END', 22.0)

        # Asian wraps midnight: 22:00–08:00
        if hour >= asian_start or hour < asian_end:
            return SessionType.ASIAN, RegimeType.MEAN_REVERT
        elif london_start <= hour < london_end:
            return SessionType.LONDON, RegimeType.BREAKOUT
        elif ny_start <= hour < ny_end:
            return SessionType.NY, RegimeType.MOMENTUM
        elif ny_aftn_start <= hour < ny_aftn_end:
            return SessionType.NY_AFTERNOON, RegimeType.FLAT
        else:
            # Shouldn't happen with 22:00-22:00 coverage, but default to FLAT
            return SessionType.NY_AFTERNOON, RegimeType.FLAT

    # ─── Helper Methods ───────────────────────────────────────────────────

    def _get_asian_midpoint(self) -> Optional[float]:
        """Calculate Asian range midpoint."""
        if self._asian_high > 0 and self._asian_low < float('inf'):
            return (self._asian_high + self._asian_low) / 2.0
        return None

    def _get_asian_range_size(self) -> float:
        """Calculate Asian range size in price units."""
        if self._asian_high > 0 and self._asian_low < float('inf'):
            return self._asian_high - self._asian_low
        return 0.0

    def _update_atr(self, df):
        """Calculate ATR(14) from DataFrame."""
        try:
            import pandas as pd
            high = df['high']
            low = df['low']
            close = df['close']

            tr1 = high - low
            tr2 = (high - close.shift(1)).abs()
            tr3 = (low - close.shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(window=14).mean()
            self._current_atr = float(atr.iloc[-1])
        except Exception:
            pass

    def _update_daily_levels(self, price_high: float, price_low: float):
        """Track current day high/low for PDH/PDL calculation."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if self._pdh_date != today:
            # New day — roll over yesterday's levels to PDH/PDL
            if self._current_day_high > 0:
                self._prev_day_high = self._current_day_high
                self._prev_day_low = self._current_day_low
            self._current_day_high = price_high
            self._current_day_low = price_low
            self._pdh_date = today
        else:
            self._current_day_high = max(self._current_day_high, price_high)
            self._current_day_low = min(self._current_day_low, price_low)

    def _update_pdh_pdl(self, df):
        """Extract PDH/PDL from historical data if not yet set."""
        if self._prev_day_high > 0:
            return  # Already set from live tracking

        try:
            import pandas as pd
            df = df.copy()
            if 'time' in df.columns:
                df['date'] = pd.to_datetime(df['time']).dt.date
                daily = df.groupby('date').agg({'high': 'max', 'low': 'min'})
                if len(daily) >= 2:
                    self._prev_day_high = float(daily['high'].iloc[-2])
                    self._prev_day_low = float(daily['low'].iloc[-2])
        except Exception:
            pass

    # ─── Asian Range Backfill ─────────────────────────────────────────────

    async def _backfill_asian_range(self):
        """Backfill Asian session high/low from historical M5 data.

        This is critical when the bot starts after the Asian session has ended
        (e.g. during London or NY). Without this, the London BREAKOUT strategy
        has no Asian range to break out of and will never generate signals.
        """
        session, _ = self._detect_session_regime()
        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.strftime("%Y-%m-%d")

        # Already have a valid range for today — skip
        if (self._asian_range_date == today_str and
                self._asian_high > 0 and self._asian_low < float('inf')):
            logger.info("[BACKFILL] Asian range already populated — skipping")
            return

        # Determine the Asian session window to scan:
        #   Asian session = 22:00 UTC (previous day) to 08:00 UTC (today)
        asian_start_h = getattr(settings, 'ASIAN_SESSION_START', 22.0)
        asian_end_h = getattr(settings, 'ASIAN_SESSION_END', 8.0)

        # The Asian session that feeds today's London starts the evening before
        asian_start_dt = (now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
                          - timedelta(days=1)
                          + timedelta(hours=asian_start_h))
        asian_end_dt = now_utc.replace(hour=int(asian_end_h), minute=0,
                                       second=0, microsecond=0)

        # If we're still in Asian session, end at current time (partial fill)
        if session == "ASIAN":
            asian_end_dt = now_utc

        # Find the XAUUSD symbol name (may have suffix like XAUUSDm)
        xau_symbol = None
        for sym in settings.SYMBOLS:
            if "XAU" in sym.upper():
                xau_symbol = sym
                break

        if not xau_symbol:
            logger.warning("[BACKFILL] No XAUUSD symbol found in SYMBOLS list")
            return

        try:
            # Fetch enough M5 bars to cover ~10 hours (22:00-08:00 = 10h = 120 bars)
            rates = await self.gateway.get_rates(xau_symbol, "M5", 300)
            if rates is None or len(rates) == 0:
                logger.warning("[BACKFILL] No M5 data returned from MT5")
                return

            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)

            # Filter to Asian session window
            mask = (df['time'] >= asian_start_dt) & (df['time'] <= asian_end_dt)
            asian_df = df[mask]

            if asian_df.empty:
                logger.warning(
                    f"[BACKFILL] No M5 bars found in Asian window "
                    f"{asian_start_dt.strftime('%H:%M')}-{asian_end_dt.strftime('%H:%M')} UTC"
                )
                return

            backfill_high = float(asian_df['high'].max())
            backfill_low = float(asian_df['low'].min())

            if backfill_high <= 0 or backfill_low <= 0:
                logger.warning("[BACKFILL] Invalid price data in Asian window")
                return

            self._asian_high = backfill_high
            self._asian_low = backfill_low
            self._asian_range_date = today_str

            # Lock the range if Asian session is over
            if session != "ASIAN":
                self._asian_range_locked = True

            range_size = backfill_high - backfill_low
            logger.info(
                f"[BACKFILL] Asian Range RECOVERED from {len(asian_df)} M5 bars: "
                f"{backfill_low:.2f} - {backfill_high:.2f} "
                f"(range: {range_size:.2f}) | locked={self._asian_range_locked}"
            )

        except Exception as e:
            logger.error(f"[BACKFILL] Failed to backfill Asian range: {e}")

    # ─── Public Accessors ─────────────────────────────────────────────────

    def get_current_regime(self) -> dict:
        """Get current session regime data (for other services)."""
        return {
            "session": self._current_session,
            "regime": self._current_regime,
            "asian_high": self._asian_high,
            "asian_low": self._asian_low,
            "asian_midpoint": self._get_asian_midpoint(),
            "prev_day_high": self._prev_day_high,
            "prev_day_low": self._prev_day_low,
            "atr_14": self._current_atr,
        }
