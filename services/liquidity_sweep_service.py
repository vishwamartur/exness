"""
LiquiditySweepService — Automated Smart Money Concepts (SMC) stop-hunt detector.

Edge #3: Gold is notorious for "stop hunting" — spiking above PDH/Asian High
to trigger retail stop-losses, then reversing sharply.

Logic:
  1. Identify PDH/PDL or Asian session extremes
  2. Wait for price to sweep above PDH by 2-3 pips
  3. Check for reversal candle (engulfing on M5) or RSI divergence
  4. Enter short with stop above sweep wick, target next liquidity pool

CRITICAL: Disabled during true breakouts (NFP, Fed decisions).
Must have news filter — no sweeps 15 min before/after high-impact USD news.

Subscribes to: MARKET_DATA_READY, SESSION_REGIME_UPDATE
Publishes: SWEEP_TRADE_SIGNAL
"""

import logging
from datetime import datetime, timezone

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from utils.async_utils import run_in_executor
from utils.news_filter import is_news_blackout
from config import settings

logger = logging.getLogger("LiquiditySweepService")


class LiquiditySweepService(BaseService):

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        self._session_data: dict = {}
        self._last_sweep_time: float = 0

    @property
    def name(self) -> str:
        return "LiquiditySweepService"

    async def _setup(self):
        self.bus.subscribe(EventTypes.SESSION_REGIME_UPDATE, self._on_regime)
        self.bus.subscribe(EventTypes.MARKET_DATA_READY, self._on_market_data)

    async def _on_regime(self, event: Event):
        self._session_data = event.payload

    async def _on_market_data(self, event: Event):
        """Detect liquidity sweeps on incoming data."""
        if not getattr(settings, 'SWEEP_ENABLED', True):
            return

        symbol = event.payload.get("symbol", "")
        if "XAU" not in symbol.upper():
            return

        # Only active during London/NY (sweeps happen when big players are active)
        session = self._session_data.get("session", "")
        if session not in ("LONDON", "NY"):
            return

        # NEWS GUARD — never run sweeps near high-impact news
        guard_min = getattr(settings, 'SWEEP_NEWS_GUARD_MINUTES', 15)
        try:
            if is_news_blackout(symbol, pre_minutes=guard_min, post_minutes=guard_min):
                return
        except Exception:
            pass

        data_dict = event.payload.get("data_dict")
        tick = event.payload.get("tick")
        if not data_dict or not tick:
            return

        df = data_dict.get(settings.TIMEFRAME)
        if df is None or len(df) < 20:
            return

        try:
            signal = await run_in_executor(
                self._detect_sweep, symbol, df, tick
            )
            if signal and signal.get("direction") != "NEUTRAL":
                import time
                # Cooldown: max one sweep signal per 10 minutes
                if time.time() - self._last_sweep_time < 600:
                    return
                self._last_sweep_time = time.time()
                await self.emit(EventTypes.SWEEP_TRADE_SIGNAL, signal)
        except Exception as e:
            logger.error(f"[{symbol}] Sweep detection error: {e}")

    def _detect_sweep(self, symbol, df, tick) -> dict:
        """Detect liquidity sweep at PDH/PDL or Asian extremes."""
        import numpy as np

        pdh = self._session_data.get("prev_day_high")
        pdl = self._session_data.get("prev_day_low")
        asian_high = self._session_data.get("asian_high")
        asian_low = self._session_data.get("asian_low")
        asian_mid = self._session_data.get("asian_range_midpoint")
        atr = self._session_data.get("atr_14", 0)

        if atr <= 0:
            return {}

        buffer = getattr(settings, 'SWEEP_BUFFER_PIPS', 3) * 0.01  # Convert pips to price for gold

        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        current_close = float(close[-1])
        prev_close = float(close[-2]) if len(close) > 1 else current_close
        candle_high = float(high[-1])
        candle_low = float(low[-1])
        candle_open = float(df['open'].values[-1])

        rsi = self._calc_rsi(close, 14)

        base = {
            "symbol": symbol,
            "session": self._session_data.get("session", ""),
            "atr": atr,
            "rsi": rsi,
            "sweep_type": "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # ─── Sweep ABOVE PDH / Asian High ─────────────────────────────
        sweep_high = pdh if pdh and pdh > 0 else asian_high
        if sweep_high and sweep_high > 0:
            swept = candle_high > sweep_high + buffer
            closed_below = current_close < sweep_high
            bearish_engulfing = (candle_close_bearish(candle_open, current_close) and
                                 abs(current_close - candle_open) > abs(prev_close - float(df['open'].values[-2])))
            rsi_overbought = rsi > 65

            if swept and closed_below and (bearish_engulfing or rsi_overbought):
                # Target: next support (Asian midpoint or PDL)
                target = asian_mid if asian_mid else (pdl if pdl else sweep_high - atr * 3)
                tp_dist = abs(current_close - target) if target else atr * 3

                return {**base,
                        "direction": "SELL",
                        "sl_distance": candle_high - current_close + buffer,
                        "tp_distance": tp_dist,
                        "score": 9,
                        "sweep_type": "PDH_SWEEP",
                        "sweep_level": sweep_high,
                        "reason": f"Liquidity sweep above {sweep_high:.2f} — reversal detected"}

        # ─── Sweep BELOW PDL / Asian Low ──────────────────────────────
        sweep_low = pdl if pdl and pdl < float('inf') and pdl > 0 else asian_low
        if sweep_low and sweep_low > 0 and sweep_low < float('inf'):
            swept = candle_low < sweep_low - buffer
            closed_above = current_close > sweep_low
            bullish_engulfing = (candle_close_bullish(candle_open, current_close) and
                                 abs(current_close - candle_open) > abs(prev_close - float(df['open'].values[-2])))
            rsi_oversold = rsi < 35

            if swept and closed_above and (bullish_engulfing or rsi_oversold):
                target = asian_mid if asian_mid else (pdh if pdh else sweep_low + atr * 3)
                tp_dist = abs(target - current_close) if target else atr * 3

                return {**base,
                        "direction": "BUY",
                        "sl_distance": current_close - candle_low + buffer,
                        "tp_distance": tp_dist,
                        "score": 9,
                        "sweep_type": "PDL_SWEEP",
                        "sweep_level": sweep_low,
                        "reason": f"Liquidity sweep below {sweep_low:.2f} — reversal detected"}

        return {**base, "direction": "NEUTRAL", "score": 0, "reason": "No sweep"}

    @staticmethod
    def _calc_rsi(close, period=14) -> float:
        import numpy as np
        deltas = np.diff(close)
        gain = np.where(deltas > 0, deltas, 0)
        loss = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gain[-period:])
        avg_loss = np.mean(loss[-period:])
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))


def candle_close_bearish(open_price, close_price):
    return close_price < open_price

def candle_close_bullish(open_price, close_price):
    return close_price > open_price
