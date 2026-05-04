"""
SessionStrategyService — Session-specific trade signal generation.

BIG TRADE MODE: Fewer entries, wider TP, only high-conviction setups.

Generates signals based on the active session regime:
  ASIAN:       RSI mean-reversion at range extremes
  LONDON:      Asian range breakout with ATR filter + H1 trend confirm
  NY:          Trend continuation (EMA20 + MACD + H1 align)
  NY_AFTERNOON: No signals (FLAT)

Subscribes to: SESSION_REGIME_UPDATE, MACRO_FILTER_UPDATE, MARKET_DATA_READY
Publishes: SESSION_TRADE_SIGNAL
"""

import logging
from datetime import datetime, timezone

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from utils.async_utils import run_in_executor
from config import settings

logger = logging.getLogger("SessionStrategyService")


class SessionStrategyService(BaseService):

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        # Cached state from other services
        self._session_data: dict = {}
        self._macro_data: dict = {}
        self._market_data: dict = {}  # {symbol: {tick, data_dict}}

    @property
    def name(self) -> str:
        return "SessionStrategyService"

    async def _setup(self):
        self.bus.subscribe(EventTypes.SESSION_REGIME_UPDATE, self._on_regime)
        self.bus.subscribe(EventTypes.MACRO_FILTER_UPDATE, self._on_macro)
        self.bus.subscribe(EventTypes.MARKET_DATA_READY, self._on_market_data)

    async def _on_regime(self, event: Event):
        self._session_data = event.payload

    async def _on_macro(self, event: Event):
        self._macro_data = event.payload

    async def _on_market_data(self, event: Event):
        """Generate session-specific signals when new data arrives."""
        symbol = event.payload.get("symbol", "")
        if "XAU" not in symbol.upper():
            return

        data_dict = event.payload.get("data_dict")
        tick = event.payload.get("tick")
        if not data_dict or not tick:
            return

        # Skip if spread is bad
        if not event.payload.get("spread_ok", True):
            return

        self._market_data[symbol] = {"tick": tick, "data_dict": data_dict}

        regime = self._session_data.get("regime", "FLAT")
        session = self._session_data.get("session", "NY_AFTERNOON")

        if regime == "FLAT":
            # NY Afternoon — emit flatten signal
            await self.emit(EventTypes.FLAT_ALL_POSITIONS, {
                "symbol": symbol,
                "reason": "NY Afternoon — declining liquidity",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return

        # Get M5 DataFrame
        df = data_dict.get(settings.TIMEFRAME)
        if df is None or len(df) < 50:
            return

        # Get H1 DataFrame for trend confirmation (big trade filter)
        h1_df = data_dict.get("H1")

        try:
            signal = await run_in_executor(
                self._generate_signal, symbol, df, tick, regime, session, h1_df
            )

            if signal and signal.get("direction") != "NEUTRAL":
                # Apply macro filter
                signal = self._apply_macro_filter(signal)
                if signal:
                    await self.emit(EventTypes.SESSION_TRADE_SIGNAL, signal)

        except Exception as e:
            logger.error(f"[{symbol}] Strategy error: {e}")

    def _generate_signal(self, symbol, df, tick, regime, session, h1_df=None) -> dict:
        """Generate high-conviction signal based on active regime.

        BIG TRADE philosophy:
        - Only trade when multiple confirmations align
        - Wide TP targets (5-8x ATR) to capture big moves
        - Moderate SL (1.5-2x ATR) to survive noise
        - H1 trend must confirm direction
        """
        import pandas as pd
        import numpy as np

        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        current_price = tick.get("bid", 0) if isinstance(tick, dict) else tick

        if current_price <= 0:
            return {}

        # Calculate M5 indicators
        rsi = self._calc_rsi(close, 14)
        atr = self._calc_atr(high, low, close, 14)
        ema20 = self._calc_ema(close, 20)
        ema50 = self._calc_ema(close, 50) if len(close) >= 50 else ema20
        macd, signal_line = self._calc_macd(close)
        volume = df['tick_volume'].values if 'tick_volume' in df.columns else None
        vol_median = float(np.median(volume[-50:])) if volume is not None else 0
        vol_current = float(volume[-1]) if volume is not None else 0

        if atr <= 0:
            return {}

        # H1 trend direction (big picture filter)
        h1_trend = self._get_h1_trend(h1_df) if h1_df is not None else "NEUTRAL"

        asian_high = self._session_data.get("asian_high")
        asian_low = self._session_data.get("asian_low")
        asian_mid = self._session_data.get("asian_range_midpoint")
        asian_range = (asian_high - asian_low) if (asian_high and asian_low and asian_high > asian_low) else 0

        base = {
            "symbol": symbol,
            "session": session,
            "regime": regime,
            "atr": atr,
            "rsi": rsi,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # ─── ASIAN: Mean Reversion (unchanged — already selective) ────
        if regime == "MEAN_REVERT":
            overbought = getattr(settings, 'ASIAN_RSI_OVERBOUGHT', 70)
            oversold = getattr(settings, 'ASIAN_RSI_OVERSOLD', 30)
            is_low_volume = (volume is not None and vol_current < vol_median)

            if (asian_high and current_price >= asian_high and
                    rsi > overbought and is_low_volume):
                return {**base, "direction": "SELL",
                        "sl_distance": atr * 1.5,
                        "tp_distance": abs(current_price - asian_mid) if asian_mid else atr * 3,
                        "score": 7, "reason": "Asian RSI overbought at range high"}

            if (asian_low and current_price <= asian_low and
                    rsi < oversold and is_low_volume):
                return {**base, "direction": "BUY",
                        "sl_distance": atr * 1.5,
                        "tp_distance": abs(asian_mid - current_price) if asian_mid else atr * 3,
                        "score": 7, "reason": "Asian RSI oversold at range low"}

        # ─── LONDON: Asian Range Breakout (HIGH CONVICTION ONLY) ──────
        elif regime == "BREAKOUT":
            breakout_mult = getattr(settings, 'BREAKOUT_ATR_MULTIPLIER', 0.3)

            if asian_high and asian_low and asian_high > asian_low:
                break_above = asian_high + (breakout_mult * atr)
                break_below = asian_low - (breakout_mult * atr)

                # Volume surge = institutional participation
                is_volume_surge = vol_current > vol_median * 1.2

                # Diagnostic logging
                logger.info(
                    f"[BREAKOUT] {symbol} price={current_price:.2f} | "
                    f"Asian={asian_low:.2f}-{asian_high:.2f} (range={asian_range:.2f}) | "
                    f"break_above={break_above:.2f} break_below={break_below:.2f} | "
                    f"MACD={macd:.4f} vs sig={signal_line:.4f} | RSI={rsi:.1f} | "
                    f"H1={h1_trend} | vol_surge={is_volume_surge}"
                )

                # ── PRIMARY: Strong breakout with MACD + H1 trend alignment ──
                if (current_price > break_above and macd > signal_line
                        and h1_trend in ("BUY", "NEUTRAL")
                        and rsi > 50 and rsi < 80):
                    score = 8 if is_volume_surge else 7
                    return {**base, "direction": "BUY",
                            "sl_distance": atr * 2.0,
                            "tp_distance": atr * 6.0,
                            "score": score,
                            "reason": f"London breakout ABOVE Asian High | H1={h1_trend} | vol={'HIGH' if is_volume_surge else 'OK'}"}

                if (current_price < break_below and macd < signal_line
                        and h1_trend in ("SELL", "NEUTRAL")
                        and rsi > 20 and rsi < 50):
                    score = 8 if is_volume_surge else 7
                    return {**base, "direction": "SELL",
                            "sl_distance": atr * 2.0,
                            "tp_distance": atr * 6.0,
                            "score": score,
                            "reason": f"London breakout BELOW Asian Low | H1={h1_trend} | vol={'HIGH' if is_volume_surge else 'OK'}"}

            else:
                logger.warning(f"[BREAKOUT] No valid Asian range: high={asian_high}, low={asian_low}")

        # ─── NY: Trend Continuation (EMA alignment + momentum) ────────
        elif regime == "MOMENTUM":
            # Stronger trend definition: EMA20 above EMA50 = uptrend
            ema_trend_up = ema20 > ema50 and current_price > ema20
            ema_trend_down = ema20 < ema50 and current_price < ema20
            macd_bullish = macd > signal_line
            macd_bearish = macd < signal_line
            ema_distance = abs(current_price - ema20) / atr

            # Diagnostic logging
            logger.info(
                f"[MOMENTUM] {symbol} price={current_price:.2f} | "
                f"EMA20={ema20:.2f} EMA50={ema50:.2f} dist={ema_distance:.1f}xATR | "
                f"MACD={macd:.4f} vs sig={signal_line:.4f} | RSI={rsi:.1f} | "
                f"H1={h1_trend} | trend_up={ema_trend_up} trend_down={ema_trend_down}"
            )

            # ── PRIMARY: Trend pullback to EMA20 with H1 confirmation ──
            # Price near EMA20 in trending market = pullback entry
            price_near_ema = ema_distance < 1.5

            if (ema_trend_up and macd_bullish and price_near_ema
                    and h1_trend in ("BUY", "NEUTRAL")
                    and rsi > 45 and rsi < 70):
                return {**base, "direction": "BUY",
                        "sl_distance": atr * 1.5,
                        "tp_distance": atr * 5.0,
                        "score": 8,
                        "reason": f"NY trend pullback BUY | EMA20>50 | H1={h1_trend} | dist={ema_distance:.1f}xATR"}

            if (ema_trend_down and macd_bearish and price_near_ema
                    and h1_trend in ("SELL", "NEUTRAL")
                    and rsi > 30 and rsi < 55):
                return {**base, "direction": "SELL",
                        "sl_distance": atr * 1.5,
                        "tp_distance": atr * 5.0,
                        "score": 8,
                        "reason": f"NY trend pullback SELL | EMA20<50 | H1={h1_trend} | dist={ema_distance:.1f}xATR"}

            # ── SECONDARY: Strong momentum with H1 alignment (no pullback needed) ──
            if (ema_trend_up and macd_bullish
                    and h1_trend == "BUY"
                    and rsi > 55 and rsi < 75):
                return {**base, "direction": "BUY",
                        "sl_distance": atr * 2.0,
                        "tp_distance": atr * 5.0,
                        "score": 7,
                        "reason": f"NY momentum continuation BUY | H1 CONFIRMS | RSI={rsi:.0f}"}

            if (ema_trend_down and macd_bearish
                    and h1_trend == "SELL"
                    and rsi > 25 and rsi < 45):
                return {**base, "direction": "SELL",
                        "sl_distance": atr * 2.0,
                        "tp_distance": atr * 5.0,
                        "score": 7,
                        "reason": f"NY momentum continuation SELL | H1 CONFIRMS | RSI={rsi:.0f}"}

        return {**base, "direction": "NEUTRAL", "score": 0, "reason": "No setup"}

    def _get_h1_trend(self, h1_df) -> str:
        """Determine H1 trend direction for big-picture confirmation."""
        import numpy as np
        try:
            close = h1_df['close'].values
            if len(close) < 20:
                return "NEUTRAL"

            ema20 = self._calc_ema(close, 20)
            macd, signal_line = self._calc_macd(close)
            rsi = self._calc_rsi(close, 14)

            current = float(close[-1])

            if current > ema20 and macd > signal_line and rsi > 50:
                return "BUY"
            elif current < ema20 and macd < signal_line and rsi < 50:
                return "SELL"
            return "NEUTRAL"
        except Exception:
            return "NEUTRAL"

    def _apply_macro_filter(self, signal: dict) -> dict:
        """Apply macro filter — block trades in CHOP regime entirely,
        block trades that conflict with macro direction."""
        if not self._macro_data:
            return signal  # No macro data yet, pass through

        macro_regime = self._macro_data.get("regime", "NEUTRAL")

        # CHOP regime = no edge, skip entirely (was causing small losing trades)
        if macro_regime == "CHOP":
            logger.info(f"[MACRO FILTER] Blocked — CHOP regime (no edge, skip)")
            return None

        allowed = self._macro_data.get("allowed_directions", ["BUY", "SELL"])
        direction = signal.get("direction", "NEUTRAL")

        if direction not in allowed:
            logger.info(f"[MACRO FILTER] Blocked {direction} — macro regime is {macro_regime}")
            return None

        # Apply size factor
        signal["macro_size_factor"] = self._macro_data.get("size_factor", 1.0)
        signal["macro_stop_mult"] = self._macro_data.get("stop_multiplier", 1.0)
        signal["macro_regime"] = macro_regime
        return signal

    # ─── Technical Indicator Calculations ─────────────────────────────

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

    @staticmethod
    def _calc_atr(high, low, close, period=14) -> float:
        import numpy as np
        tr = np.maximum(high[1:] - low[1:],
                        np.maximum(np.abs(high[1:] - close[:-1]),
                                   np.abs(low[1:] - close[:-1])))
        return float(np.mean(tr[-period:]))

    @staticmethod
    def _calc_ema(data, period) -> float:
        import numpy as np
        weights = np.exp(np.linspace(-1., 0., period))
        weights /= weights.sum()
        ema = np.convolve(data, weights, mode='valid')
        return float(ema[-1]) if len(ema) > 0 else float(data[-1])

    @staticmethod
    def _calc_macd(close, fast=12, slow=26, signal=9):
        import numpy as np
        def ema(data, n):
            a = 2 / (n + 1)
            result = np.zeros_like(data, dtype=float)
            result[0] = data[0]
            for i in range(1, len(data)):
                result[i] = a * data[i] + (1 - a) * result[i-1]
            return result
        ema_fast = ema(close, fast)
        ema_slow = ema(close, slow)
        macd_line = ema_fast - ema_slow
        signal_line = ema(macd_line, signal)
        return float(macd_line[-1]), float(signal_line[-1])
