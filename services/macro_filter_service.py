"""
MacroFilterService — DXY + VIX regime filter for XAUUSD (Edge #2).

Use macro data to FILTER OUT bad trades, not to predict gold.
  - VIX > 25:              Safe-haven → favor longs, wider stops
  - DXY spiking + VIX < 20: Headwind  → only shorts or flat
  - DXY flat + VIX 15-20:   Chop      → reduce size 50%

Subscribes to: SCAN_START
Publishes: MACRO_FILTER_UPDATE
"""

import logging
import time
from datetime import datetime, timezone

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from utils.async_utils import run_in_executor
from config import settings

logger = logging.getLogger("MacroFilterService")


class MacroRegime:
    SAFE_HAVEN = "SAFE_HAVEN"
    HEADWIND = "HEADWIND"
    CHOP = "CHOP"
    NEUTRAL = "NEUTRAL"


class MacroFilterService(BaseService):

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        self._dxy_current: float = 0.0
        self._dxy_prev_hour: float = 0.0
        self._dxy_change_pct: float = 0.0
        self._vix_current: float = 0.0
        self._macro_regime: str = MacroRegime.NEUTRAL
        self._last_poll: float = 0
        self._poll_interval: int = getattr(settings, 'MACRO_POLL_MINUTES', 15) * 60
        self._yfinance_available: bool = True

    @property
    def name(self) -> str:
        return "MacroFilterService"

    async def _setup(self):
        self.bus.subscribe(EventTypes.SCAN_START, self._on_scan_start)
        await self._poll_macro_data()

    async def _on_scan_start(self, event: Event):
        if not getattr(settings, 'MACRO_FILTER_ENABLED', True):
            return
        now = time.time()
        if now - self._last_poll >= self._poll_interval:
            await self._poll_macro_data()
        await self._publish_macro_filter()

    async def _poll_macro_data(self):
        if not self._yfinance_available:
            return
        try:
            dxy, vix = await run_in_executor(self._fetch_yfinance_data)
            if dxy is not None:
                self._dxy_prev_hour = self._dxy_current if self._dxy_current > 0 else dxy
                self._dxy_current = dxy
                if self._dxy_prev_hour > 0:
                    self._dxy_change_pct = (dxy - self._dxy_prev_hour) / self._dxy_prev_hour
            if vix is not None:
                self._vix_current = vix
            self._macro_regime = self._classify_regime()
            self._last_poll = time.time()
            logger.info(f"[MACRO] DXY={self._dxy_current:.2f} (d{self._dxy_change_pct*100:.3f}%) | VIX={self._vix_current:.2f} | {self._macro_regime}")
        except Exception as e:
            logger.warning(f"Macro data fetch failed: {e}")

    def _fetch_yfinance_data(self) -> tuple:
        try:
            import yfinance as yf
            dxy_hist = yf.Ticker("DX-Y.NYB").history(period="2d", interval="1h")
            dxy_price = float(dxy_hist['Close'].iloc[-1]) if dxy_hist is not None and len(dxy_hist) > 0 else None
            vix_hist = yf.Ticker("^VIX").history(period="2d", interval="1h")
            vix_price = float(vix_hist['Close'].iloc[-1]) if vix_hist is not None and len(vix_hist) > 0 else None
            return dxy_price, vix_price
        except ImportError:
            logger.warning("yfinance not installed. pip install yfinance")
            self._yfinance_available = False
            return None, None
        except Exception as e:
            logger.warning(f"yfinance error: {e}")
            return None, None

    def _classify_regime(self) -> str:
        vix = self._vix_current
        dxy_change = abs(self._dxy_change_pct)
        vix_sh = getattr(settings, 'VIX_SAFE_HAVEN_THRESHOLD', 25)
        vix_calm = getattr(settings, 'VIX_CALM_THRESHOLD', 20)
        vix_low = getattr(settings, 'VIX_RANGE_LOW', 15)
        dxy_spike = getattr(settings, 'DXY_SPIKE_THRESHOLD', 0.003)

        if vix >= vix_sh:
            return MacroRegime.SAFE_HAVEN
        if dxy_change >= dxy_spike and vix < vix_calm:
            return MacroRegime.HEADWIND
        if dxy_change < dxy_spike and vix_low <= vix <= vix_calm:
            return MacroRegime.CHOP
        return MacroRegime.NEUTRAL

    async def _publish_macro_filter(self):
        if self._macro_regime == MacroRegime.SAFE_HAVEN:
            dirs, sz, st = ["BUY"], 1.0, 1.5
        elif self._macro_regime == MacroRegime.HEADWIND:
            dirs, sz, st = ["SELL"], 0.5, 1.0
        elif self._macro_regime == MacroRegime.CHOP:
            dirs, sz, st = ["BUY", "SELL"], 0.5, 0.8
        else:
            dirs, sz, st = ["BUY", "SELL"], 1.0, 1.0

        await self.emit(EventTypes.MACRO_FILTER_UPDATE, {
            "regime": self._macro_regime,
            "dxy": self._dxy_current,
            "dxy_change_pct": self._dxy_change_pct,
            "vix": self._vix_current,
            "allowed_directions": dirs,
            "size_factor": sz,
            "stop_multiplier": st,
            "data_fresh": time.time() - self._last_poll < self._poll_interval * 2,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def get_macro_regime(self) -> dict:
        return {"regime": self._macro_regime, "dxy": self._dxy_current,
                "vix": self._vix_current, "dxy_change_pct": self._dxy_change_pct}
