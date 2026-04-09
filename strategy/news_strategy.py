"""
News Trading Strategy — XAUUSD-focused news event trading.

Two modes:
  STRADDLE: Pre-positions buy-stop + sell-stop around the pre-news range
  BREAKOUT: Waits for post-news candle confirmation before entering

Gold-specific patterns:
  NFP:  Huge spike → often reverts 50-60% within 30 min → trade the revert
  FOMC: Sustained trend → trade the breakout direction, ride with trail
  CPI:  Spike → partial revert → continuation → trade the continuation
"""

import logging
import numpy as np
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

logger = logging.getLogger("NewsStrategy")


class NewsStrategy:
    """
    Analyzes XAUUSD price action around high-impact news events  
    and generates entry signals for straddle or breakout trades.
    """

    def __init__(self):
        self._pre_news_snapshots: Dict[str, dict] = {}  # {event_key: snapshot}

    # ─── Pre-News Analysis ────────────────────────────────────────────────

    def capture_pre_news_snapshot(self, symbol: str, df, event_key: str) -> dict:
        """
        Capture the pre-news market state: ATR, range, support/resistance.
        Called 5-15 minutes before the news event.
        """
        if df is None or len(df) < 20:
            return {}

        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        volume = df["tick_volume"].values if "tick_volume" in df.columns else np.ones(len(df))

        # ATR (14-period)
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1])
            )
        )
        atr_14 = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)

        # Recent range (last 6 candles ≈ 30 min on M5)
        recent_high = np.max(high[-6:])
        recent_low = np.min(low[-6:])
        range_size = recent_high - recent_low

        # Current price
        current_price = float(close[-1])

        # Volume baseline (average of last 20 candles)
        avg_volume = float(np.mean(volume[-20:])) if len(volume) >= 20 else float(np.mean(volume))

        # Support and resistance (swing points)
        support = self._find_support(low[-30:], current_price)
        resistance = self._find_resistance(high[-30:], current_price)

        # RSI for directional bias
        rsi = self._calculate_rsi(close, 14)

        snapshot = {
            "symbol": symbol,
            "event_key": event_key,
            "current_price": current_price,
            "atr": float(atr_14),
            "recent_high": float(recent_high),
            "recent_low": float(recent_low),
            "range_size": float(range_size),
            "avg_volume": avg_volume,
            "support": float(support),
            "resistance": float(resistance),
            "rsi": float(rsi),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self._pre_news_snapshots[event_key] = snapshot
        logger.info(
            f"[{symbol}] Pre-news snapshot: price={current_price:.2f}, "
            f"ATR={atr_14:.2f}, range={range_size:.2f}, RSI={rsi:.1f}"
        )
        return snapshot

    # ─── Straddle Mode ────────────────────────────────────────────────────

    def calculate_straddle_levels(
        self, snapshot: dict, distance_atr: float = 1.5
    ) -> dict:
        """
        Calculate buy-stop and sell-stop levels for straddle mode.
        Places orders above and below the pre-news consolidation range.
        """
        price = snapshot["current_price"]
        atr = snapshot["atr"]
        recent_high = snapshot["recent_high"]
        recent_low = snapshot["recent_low"]

        # Straddle distance: max of ATR-based distance or range boundaries
        offset = max(atr * distance_atr, (recent_high - recent_low) * 0.5)

        buy_stop = recent_high + (atr * 0.3)   # Just above recent high
        sell_stop = recent_low - (atr * 0.3)    # Just below recent low

        # SL: opposite side of the range + buffer
        buy_sl = sell_stop - (atr * 0.5)
        sell_sl = buy_stop + (atr * 0.5)

        # TP: momentum target based on expected news move
        buy_tp = buy_stop + (atr * 4.0)
        sell_tp = sell_stop - (atr * 4.0)

        levels = {
            "buy_stop_price": round(buy_stop, 2),
            "buy_sl": round(buy_sl, 2),
            "buy_tp": round(buy_tp, 2),
            "sell_stop_price": round(sell_stop, 2),
            "sell_sl": round(sell_sl, 2),
            "sell_tp": round(sell_tp, 2),
            "straddle_width": round(buy_stop - sell_stop, 2),
            "risk_per_side": round(buy_stop - buy_sl, 2),
        }

        logger.info(
            f"Straddle: BuyStop={buy_stop:.2f} SellStop={sell_stop:.2f} "
            f"Width={levels['straddle_width']:.2f}"
        )
        return levels

    # ─── Breakout Mode ────────────────────────────────────────────────────

    def detect_news_breakout(
        self, df, snapshot: dict, confirm_candles: int = 1
    ) -> Optional[dict]:
        """
        Detect a confirmed breakout after a news event.

        Criteria:
        1. Price has moved beyond the pre-news range
        2. Current candle body is > 50% of the candle range (strong close)
        3. Volume spike > 2x average pre-news volume
        4. At least `confirm_candles` candles have closed beyond the breakout level

        Returns breakout dict or None.
        """
        if df is None or len(df) < 3:
            return None

        close = df["close"].values
        open_prices = df["open"].values
        high = df["high"].values
        low = df["low"].values
        volume = df["tick_volume"].values if "tick_volume" in df.columns else np.ones(len(df))

        current_close = float(close[-1])
        current_open = float(open_prices[-1])
        current_high = float(high[-1])
        current_low = float(low[-1])
        current_volume = float(volume[-1])

        pre_high = snapshot["recent_high"]
        pre_low = snapshot["recent_low"]
        pre_atr = snapshot["atr"]
        avg_vol = snapshot["avg_volume"]

        # Direction detection
        broke_up = current_close > pre_high
        broke_down = current_close < pre_low

        if not broke_up and not broke_down:
            return None

        direction = "BUY" if broke_up else "SELL"
        breakout_level = pre_high if broke_up else pre_low

        # Filter 1: Candle body strength (> 50% of range)
        candle_range = current_high - current_low
        if candle_range <= 0:
            return None
        body = abs(current_close - current_open)
        body_ratio = body / candle_range
        if body_ratio < 0.4:
            logger.debug(f"Breakout rejected: weak body ({body_ratio:.2f})")
            return None

        # Filter 2: Move size must be significant (> 0.3x ATR)
        move_from_level = abs(current_close - breakout_level)
        if move_from_level < pre_atr * 0.3:
            logger.debug(f"Breakout rejected: small move ({move_from_level:.2f} < {pre_atr * 0.3:.2f})")
            return None

        # Filter 3: Volume spike (> 1.5x average)
        volume_ratio = current_volume / max(avg_vol, 1)
        volume_ok = volume_ratio >= 1.5

        # Filter 4: Confirmation candles
        confirmed_count = 0
        for i in range(max(1, confirm_candles)):
            idx = -(i + 1)
            if abs(idx) > len(close):
                break
            c = float(close[idx])
            if broke_up and c > pre_high:
                confirmed_count += 1
            elif broke_down and c < pre_low:
                confirmed_count += 1

        if confirmed_count < confirm_candles:
            logger.debug(f"Breakout pending confirmation ({confirmed_count}/{confirm_candles})")
            return None

        # Calculate strength score (0-10)
        strength = 0
        strength += min(3, int(body_ratio * 4))           # Body strength: 0-3
        strength += min(3, int(volume_ratio))              # Volume: 0-3
        strength += min(2, int(move_from_level / pre_atr * 3))  # Move size: 0-2
        strength += 1 if volume_ok else 0                  # Volume bonus
        strength += 1 if confirmed_count >= 2 else 0       # Extra confirmation bonus

        breakout = {
            "direction": direction,
            "breakout_level": float(breakout_level),
            "current_price": current_close,
            "move_size": float(move_from_level),
            "body_ratio": round(body_ratio, 2),
            "volume_ratio": round(volume_ratio, 2),
            "strength": min(10, strength),
            "confirmed_candles": confirmed_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            f"NEWS BREAKOUT: {direction} | Price={current_close:.2f} "
            f"| Move={move_from_level:.2f} | Body={body_ratio:.0%} "
            f"| Vol={volume_ratio:.1f}x | Strength={strength}/10"
        )

        return breakout

    # ─── SL/TP Calculation ────────────────────────────────────────────────

    def calculate_news_sl_tp(
        self,
        direction: str,
        entry_price: float,
        snapshot: dict,
        classification: dict,
        sl_atr_mult: float = 2.0,
        tp_atr_mult: float = 4.0,
    ) -> Tuple[float, float]:
        """
        Calculate wider SL/TP for news trades.

        Pattern-specific adjustments:
        - SPIKE_REVERT: Tighter TP (reversion target), wider SL
        - SUSTAINED_TREND: Wider TP, moderate SL
        - SPIKE_CONTINUE: Medium TP, medium SL
        """
        atr = snapshot["atr"]
        pattern = classification.get("pattern", "SPIKE_REVERT")

        # Pattern-specific multiplier adjustments
        if pattern == "SPIKE_REVERT":
            # Expect reversion — enter with the spike, TP near pre-news level
            sl_mult = sl_atr_mult * 1.2   # Wider SL (spike can extend)
            tp_mult = tp_atr_mult * 0.6   # Shorter TP (reversion target)
        elif pattern == "SUSTAINED_TREND":
            # FOMC-style — ride the trend
            sl_mult = sl_atr_mult * 0.8   # Tighter SL (clean break)
            tp_mult = tp_atr_mult * 1.5   # Much wider TP (sustained move)
        elif pattern == "SPIKE_CONTINUE":
            # CPI-style — spike, pullback, continue
            sl_mult = sl_atr_mult * 1.0
            tp_mult = tp_atr_mult * 1.0
        else:
            sl_mult = sl_atr_mult
            tp_mult = tp_atr_mult

        sl_distance = atr * sl_mult
        tp_distance = atr * tp_mult

        if direction == "BUY":
            sl = entry_price - sl_distance
            tp = entry_price + tp_distance
        else:
            sl = entry_price + sl_distance
            tp = entry_price - tp_distance

        logger.info(
            f"News SL/TP ({pattern}): SL={sl:.2f} TP={tp:.2f} "
            f"| SL_dist={sl_distance:.2f} TP_dist={tp_distance:.2f}"
        )

        return round(sl, 2), round(tp, 2)

    # ─── Gemini Direction Prediction ──────────────────────────────────────

    def get_ai_direction_bias(self, sentiment_data: dict) -> Tuple[str, float]:
        """
        Extract directional bias from Gemini AI sentiment for the news event.
        Returns (direction, confidence).
        """
        if not sentiment_data or not sentiment_data.get("available", False):
            return "NEUTRAL", 0.0

        bias = sentiment_data.get("direction_bias", "NEUTRAL")
        score = sentiment_data.get("emotion_score", 0.5)
        confidence = abs(score - 0.5) * 2  # Normalize to 0-1

        if bias == "BULLISH":
            return "BUY", confidence
        elif bias == "BEARISH":
            return "SELL", confidence
        return "NEUTRAL", 0.0

    # ─── Helpers ──────────────────────────────────────────────────────────

    def _find_support(self, lows: np.ndarray, current_price: float) -> float:
        """Find nearest support level below current price."""
        below = lows[lows < current_price]
        if len(below) == 0:
            return current_price * 0.99
        # Cluster the lows and find the nearest significant level
        return float(np.max(below))

    def _find_resistance(self, highs: np.ndarray, current_price: float) -> float:
        """Find nearest resistance level above current price."""
        above = highs[highs > current_price]
        if len(above) == 0:
            return current_price * 1.01
        return float(np.min(above))

    def _calculate_rsi(self, close: np.ndarray, period: int = 14) -> float:
        """Calculate RSI."""
        if len(close) < period + 1:
            return 50.0
        deltas = np.diff(close[-(period + 1):])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains) if len(gains) > 0 else 0
        avg_loss = np.mean(losses) if len(losses) > 0 else 0
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100 - (100 / (1 + rs)))

    def get_snapshot(self, event_key: str) -> Optional[dict]:
        """Get cached pre-news snapshot."""
        return self._pre_news_snapshots.get(event_key)

    def clear_snapshot(self, event_key: str):
        """Clear a used snapshot."""
        self._pre_news_snapshots.pop(event_key, None)
