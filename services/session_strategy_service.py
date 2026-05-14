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
        # Signal decay: pending signals keyed by symbol
        self._pending_signals: dict = {}  # {symbol: {"signal": dict, "timestamp": datetime, "candles_elapsed": int}}

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

        # Signal decay: check and cancel stale pending signals
        self._check_signal_decay(symbol)

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

        # Get M15 DataFrame for multi-timeframe alignment
        m15_df = data_dict.get("M15")

        try:
            signal = await run_in_executor(
                self._generate_signal, symbol, df, tick, regime, session, h1_df, m15_df
            )

            if signal and signal.get("direction") != "NEUTRAL":
                # Apply macro filter
                signal = self._apply_macro_filter(signal)
                if signal:
                    # Track as pending signal for decay mechanism
                    self._pending_signals[symbol] = {
                        "signal": signal,
                        "timestamp": datetime.now(timezone.utc),
                        "candles_elapsed": 0,
                    }
                    await self.emit(EventTypes.SESSION_TRADE_SIGNAL, signal)

        except Exception as e:
            logger.error(f"[{symbol}] Strategy error: {e}")

    def _generate_signal(self, symbol, df, tick, regime, session, h1_df=None, m15_df=None) -> dict:
        """Generate high-conviction signal based on active regime.

        BIG TRADE philosophy:
        - Only trade when multiple confirmations align
        - Wide TP targets (5-8x ATR) to capture big moves
        - Moderate SL (1.5-2x ATR) to survive noise
        - H1 trend must confirm direction
        - Multi-timeframe alignment (M5 + M15 + H1) for max score
        - Volume confirmation for breakout entries
        - Time-of-day weighting for signal quality
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

        # Volume profile confirmation: 20-period SMA with configurable multiplier
        vol_confirmation_mult = getattr(settings, 'VOLUME_CONFIRMATION_MULTIPLIER', 1.5)
        vol_20_avg = float(np.mean(volume[-20:])) if volume is not None and len(volume) >= 20 else vol_median
        volume_confirmed = vol_current > vol_20_avg * vol_confirmation_mult if vol_20_avg > 0 else False

        if atr <= 0:
            return {}

        # H1 trend direction (big picture filter)
        h1_trend = self._get_h1_trend(h1_df) if h1_df is not None else "NEUTRAL"

        # M15 trend direction (intermediate timeframe filter)
        m15_trend = self._get_m15_trend(m15_df) if m15_df is not None else "NEUTRAL"

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
                score = 7
                score = self._apply_mtf_score(score, "SELL", m15_trend, h1_trend)
                score = self._apply_time_weight(score)
                return {**base, "direction": "SELL",
                        "sl_distance": atr * 1.5,
                        "tp_distance": abs(current_price - asian_mid) if asian_mid else atr * 3,
                        "score": score, "reason": "Asian RSI overbought at range high",
                        "volume_confirmed": volume_confirmed}

            if (asian_low and current_price <= asian_low and
                    rsi < oversold and is_low_volume):
                score = 7
                score = self._apply_mtf_score(score, "BUY", m15_trend, h1_trend)
                score = self._apply_time_weight(score)
                return {**base, "direction": "BUY",
                        "sl_distance": atr * 1.5,
                        "tp_distance": abs(asian_mid - current_price) if asian_mid else atr * 3,
                        "score": score, "reason": "Asian RSI oversold at range low",
                        "volume_confirmed": volume_confirmed}

        # ─── LONDON: Asian Range Breakout (HIGH CONVICTION ONLY) ──────
        elif regime == "BREAKOUT":
            breakout_mult = getattr(settings, 'BREAKOUT_ATR_MULTIPLIER', 0.3)

            if asian_high and asian_low and asian_high > asian_low:
                break_above = asian_high + (breakout_mult * atr)
                break_below = asian_low - (breakout_mult * atr)

                # Volume confirmation: require volume > 1.5x 20-period average for breakout
                is_volume_surge = volume_confirmed

                # Diagnostic logging
                logger.info(
                    f"[BREAKOUT] {symbol} price={current_price:.2f} | "
                    f"Asian={asian_low:.2f}-{asian_high:.2f} (range={asian_range:.2f}) | "
                    f"break_above={break_above:.2f} break_below={break_below:.2f} | "
                    f"MACD={macd:.4f} vs sig={signal_line:.4f} | RSI={rsi:.1f} | "
                    f"H1={h1_trend} | M15={m15_trend} | vol_confirmed={is_volume_surge}"
                )

                # ── PRIMARY: Strong breakout with MACD + H1 trend alignment ──
                if (current_price > break_above and macd > signal_line
                        and h1_trend in ("BUY", "NEUTRAL")
                        and rsi > 50 and rsi < 80):
                    score = 8 if is_volume_surge else 7
                    score = self._apply_mtf_score(score, "BUY", m15_trend, h1_trend)
                    score = self._apply_time_weight(score)
                    return {**base, "direction": "BUY",
                            "sl_distance": atr * 2.0,
                            "tp_distance": atr * 6.0,
                            "score": score,
                            "volume_confirmed": is_volume_surge,
                            "reason": f"London breakout ABOVE Asian High | H1={h1_trend} | M15={m15_trend} | vol={'CONFIRMED' if is_volume_surge else 'WEAK'}"}

                if (current_price < break_below and macd < signal_line
                        and h1_trend in ("SELL", "NEUTRAL")
                        and rsi > 20 and rsi < 50):
                    score = 8 if is_volume_surge else 7
                    score = self._apply_mtf_score(score, "SELL", m15_trend, h1_trend)
                    score = self._apply_time_weight(score)
                    return {**base, "direction": "SELL",
                            "sl_distance": atr * 2.0,
                            "tp_distance": atr * 6.0,
                            "score": score,
                            "volume_confirmed": is_volume_surge,
                            "reason": f"London breakout BELOW Asian Low | H1={h1_trend} | M15={m15_trend} | vol={'CONFIRMED' if is_volume_surge else 'WEAK'}"}

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
                f"H1={h1_trend} | M15={m15_trend} | trend_up={ema_trend_up} trend_down={ema_trend_down}"
            )

            # ── PRIMARY: Trend pullback to EMA20 with H1 confirmation ──
            # Price near EMA20 in trending market = pullback entry
            price_near_ema = ema_distance < 1.5

            if (ema_trend_up and macd_bullish and price_near_ema
                    and h1_trend in ("BUY", "NEUTRAL")
                    and rsi > 45 and rsi < 70):
                score = 8
                score = self._apply_mtf_score(score, "BUY", m15_trend, h1_trend)
                score = self._apply_time_weight(score)
                return {**base, "direction": "BUY",
                        "sl_distance": atr * 1.5,
                        "tp_distance": atr * 5.0,
                        "score": score,
                        "volume_confirmed": volume_confirmed,
                        "reason": f"NY trend pullback BUY | EMA20>50 | H1={h1_trend} | M15={m15_trend} | dist={ema_distance:.1f}xATR"}

            if (ema_trend_down and macd_bearish and price_near_ema
                    and h1_trend in ("SELL", "NEUTRAL")
                    and rsi > 30 and rsi < 55):
                score = 8
                score = self._apply_mtf_score(score, "SELL", m15_trend, h1_trend)
                score = self._apply_time_weight(score)
                return {**base, "direction": "SELL",
                        "sl_distance": atr * 1.5,
                        "tp_distance": atr * 5.0,
                        "score": score,
                        "volume_confirmed": volume_confirmed,
                        "reason": f"NY trend pullback SELL | EMA20<50 | H1={h1_trend} | M15={m15_trend} | dist={ema_distance:.1f}xATR"}

            # ── SECONDARY: Strong momentum with H1 alignment (no pullback needed) ──
            if (ema_trend_up and macd_bullish
                    and h1_trend == "BUY"
                    and rsi > 55 and rsi < 75):
                score = 7
                score = self._apply_mtf_score(score, "BUY", m15_trend, h1_trend)
                score = self._apply_time_weight(score)
                return {**base, "direction": "BUY",
                        "sl_distance": atr * 2.0,
                        "tp_distance": atr * 5.0,
                        "score": score,
                        "volume_confirmed": volume_confirmed,
                        "reason": f"NY momentum continuation BUY | H1 CONFIRMS | M15={m15_trend} | RSI={rsi:.0f}"}

            if (ema_trend_down and macd_bearish
                    and h1_trend == "SELL"
                    and rsi > 25 and rsi < 45):
                score = 7
                score = self._apply_mtf_score(score, "SELL", m15_trend, h1_trend)
                score = self._apply_time_weight(score)
                return {**base, "direction": "SELL",
                        "sl_distance": atr * 2.0,
                        "tp_distance": atr * 5.0,
                        "score": score,
                        "volume_confirmed": volume_confirmed,
                        "reason": f"NY momentum continuation SELL | H1 CONFIRMS | M15={m15_trend} | RSI={rsi:.0f}"}

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

    def _get_m15_trend(self, m15_df) -> str:
        """Determine M15 trend direction for intermediate timeframe confirmation."""
        import numpy as np
        try:
            close = m15_df['close'].values
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

    def _get_time_weight(self, hour_utc: int) -> float:
        """Return time-of-day multiplier (0.8-1.2) based on historically profitable hours.

        London 8-10 UTC = 1.2 (highest Gold volatility/liquidity)
        NY 13-15 UTC = 1.1 (strong momentum continuation)
        Asian 0-4 UTC = 0.9 (low liquidity, mean-reversion only)
        NY Afternoon 17-22 UTC = 0.8 (declining liquidity, choppy)
        All other hours = 1.0 (neutral)
        """
        if 8 <= hour_utc <= 10:
            return 1.2  # London open - peak Gold trading
        elif 13 <= hour_utc <= 15:
            return 1.1  # NY open - strong momentum
        elif 0 <= hour_utc <= 4:
            return 0.9  # Asian session - low liquidity
        elif 17 <= hour_utc <= 22:
            return 0.8  # NY afternoon - choppy/declining
        else:
            return 1.0  # Neutral hours

    def _apply_time_weight(self, score: int) -> int:
        """Apply time-of-day weight to signal score."""
        hour_utc = datetime.now(timezone.utc).hour
        weight = self._get_time_weight(hour_utc)
        weighted_score = round(score * weight)
        # Clamp to reasonable range (minimum 1, preserve original max if weight < 1)
        return max(1, weighted_score)

    def _apply_mtf_score(self, base_score: int, direction: str, m15_trend: str, h1_trend: str) -> int:
        """Apply multi-timeframe alignment scoring.

        If MTF_ALIGNMENT_REQUIRED is enabled:
        - M5 direction + M15 + H1 all agree: allow score 8+
        - Only 2 of 3 agree: cap at 7
        - Less than 2 agree: cap at 6
        """
        if not getattr(settings, 'MTF_ALIGNMENT_REQUIRED', True):
            return base_score

        # M5 direction is implicit (it generated the signal in that direction)
        m5_agrees = True  # Always true since signal direction comes from M5 analysis
        m15_agrees = (m15_trend == direction or m15_trend == "NEUTRAL")
        h1_agrees = (h1_trend == direction or h1_trend == "NEUTRAL")

        # Count strong agreements (exact match, not just neutral)
        strong_m15 = (m15_trend == direction)
        strong_h1 = (h1_trend == direction)

        # All three timeframes agree strongly
        if strong_m15 and strong_h1:
            return max(base_score, 8)  # Full alignment allows score 8+
        # Two of three agree (M5 always agrees since it generated the signal)
        elif strong_m15 or strong_h1:
            return min(base_score, 7)  # Cap at 7 if only 2 agree
        # Only M5 agrees, others neutral or opposing
        else:
            return min(base_score, 6)  # Cap at 6 if poor alignment

    def _check_signal_decay(self, symbol: str):
        """Check and cancel stale pending signals (signal decay mechanism).

        Signals not filled within SIGNAL_DECAY_CANDLES candles are cancelled.
        For M5 timeframe: 2 candles = 10 minutes.
        """
        if symbol not in self._pending_signals:
            return

        pending = self._pending_signals[symbol]
        decay_candles = getattr(settings, 'SIGNAL_DECAY_CANDLES', 2)
        candle_minutes = 5  # M5 timeframe

        now = datetime.now(timezone.utc)
        elapsed = (now - pending["timestamp"]).total_seconds()
        max_age_seconds = decay_candles * candle_minutes * 60  # 2 * 5 * 60 = 600 seconds (10 min)

        if elapsed > max_age_seconds:
            signal = pending["signal"]
            logger.info(
                f"[SIGNAL DECAY] Cancelling stale {signal.get('direction', '?')} signal for {symbol} | "
                f"Age: {elapsed:.0f}s > {max_age_seconds}s ({decay_candles} candles)"
            )
            del self._pending_signals[symbol]

    def _apply_macro_filter(self, signal: dict) -> dict:
        """Apply macro filter — block trades in CHOP regime entirely,
        block trades that conflict with macro direction.
        HEADWIND regime requires score >= 8 for confluence."""
        if not self._macro_data:
            return signal  # No macro data yet, pass through

        macro_regime = self._macro_data.get("regime", "NEUTRAL")

        # CHOP regime = no edge, skip entirely (was causing small losing trades)
        if macro_regime == "CHOP":
            logger.info(f"[MACRO FILTER] Blocked — CHOP regime (no edge, skip)")
            return None

        # HEADWIND regime = adverse conditions, require high conviction (score >= 8)
        if macro_regime == "HEADWIND":
            score = signal.get("score", 0)
            if score < 8:
                logger.info(
                    f"[MACRO FILTER] Blocked {signal.get('direction', '?')} score={score} — "
                    f"HEADWIND regime requires score >= 8 for sufficient confluence"
                )
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
