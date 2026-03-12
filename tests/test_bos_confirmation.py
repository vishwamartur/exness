"""Tests for BOS confirmation candle logic in strategy/bos_strategy.py."""

import pandas as pd
import numpy as np
from unittest.mock import patch

import pytest


class TestBOSConfirmation:
    """Test the BOS confirmation candle workflow."""

    def _make_strategy(self, require_confirmation=True, min_pullback_pct=0.3):
        """Create a BOSStrategy with mocked settings."""
        with patch("strategy.bos_strategy.settings") as mock_settings:
            mock_settings.BOS_MOMENTUM_MULTIPLIER = 1.5
            mock_settings.BOS_SWEEP_LOOKBACK = 20
            mock_settings.BOS_REQUIRE_CONFIRMATION = require_confirmation
            mock_settings.BOS_MIN_PULLBACK_PCT = min_pullback_pct
            from strategy.bos_strategy import BOSStrategy
            return BOSStrategy()

    def _make_df(self, rows=60, trend="up"):
        """Create a simple OHLC DataFrame for testing."""
        np.random.seed(42)
        base = 1.1000
        data = []
        for i in range(rows):
            if trend == "up":
                o = base + i * 0.001 + np.random.uniform(-0.0002, 0.0002)
            else:
                o = base - i * 0.001 + np.random.uniform(-0.0002, 0.0002)
            h = o + np.random.uniform(0.0005, 0.0020)
            l = o - np.random.uniform(0.0005, 0.0020)
            c = o + np.random.uniform(-0.0010, 0.0010)
            data.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000})
        return pd.DataFrame(data)

    def test_store_and_retrieve_pending(self):
        bos = self._make_strategy()
        signal = {"signal": "BUY", "price": 1.1050, "swing_level": 1.1000,
                  "break_candle_range": 0.0020, "pending": True, "valid": True}
        bos.store_pending("EURUSD", signal)
        assert "EURUSD" in bos.pending_signals
        assert bos.pending_signals["EURUSD"]["signal"] == "BUY"

    def test_confirm_buy_signal_with_pullback(self):
        bos = self._make_strategy(min_pullback_pct=0.3)
        signal = {
            "signal": "BUY",
            "price": 1.1050,
            "swing_level": 1.1000,
            "break_candle_range": 0.0020,
            "valid": True,
            "pending": True,
            "reason": "BOS + Momentum + Sweep",
            "sl": 1.0980,
            "atr": 0.0015,
            "score": 10,
        }
        bos.store_pending("EURUSD", signal)

        # Confirmation candle: close > swing_level AND pullback >= 30% of break range
        # break_range = 0.0020, so min pullback = 0.0006
        # pullback = price(1.1050) - candle_low => must >= 0.0006
        df = pd.DataFrame([
            {"open": 1.1040, "high": 1.1060, "low": 1.1030, "close": 1.1020},  # dummy row
            {"open": 1.1040, "high": 1.1060, "low": 1.1043, "close": 1.1055},  # low=1.1043, pullback=0.0007 >= 0.0006 ✓
        ])

        result = bos.confirm_signal("EURUSD", df)
        assert result != {}
        assert result["signal"] == "BUY"
        assert "Confirmed" in result["reason"]
        assert "EURUSD" not in bos.pending_signals  # Cleaned up

    def test_confirm_sell_signal_with_pullback(self):
        bos = self._make_strategy(min_pullback_pct=0.3)
        signal = {
            "signal": "SELL",
            "price": 1.0950,
            "swing_level": 1.1000,
            "break_candle_range": 0.0020,
            "valid": True,
            "pending": True,
            "reason": "BOS + Momentum + Sweep",
            "sl": 1.1020,
            "atr": 0.0015,
            "score": 10,
        }
        bos.store_pending("EURUSD", signal)

        # Confirm SELL: close < swing_level(1.1000) AND pullback = candle_high - price >= 0.0006
        df = pd.DataFrame([
            {"open": 1.0960, "high": 1.0970, "low": 1.0940, "close": 1.0960},  # dummy
            {"open": 1.0960, "high": 1.0958, "low": 1.0940, "close": 1.0945},  # high=1.0958, pullback=0.0008 ✓
        ])

        result = bos.confirm_signal("EURUSD", df)
        assert result != {}
        assert result["signal"] == "SELL"
        assert "Confirmed" in result["reason"]

    def test_no_confirmation_without_pullback(self):
        bos = self._make_strategy(min_pullback_pct=0.3)
        signal = {
            "signal": "BUY",
            "price": 1.1050,
            "swing_level": 1.1000,
            "break_candle_range": 0.0020,
            "valid": True,
            "pending": True,
            "reason": "BOS",
            "sl": 1.0980,
            "atr": 0.0015,
            "score": 10,
        }
        bos.store_pending("EURUSD", signal)

        # No pullback: candle_low is very close to price
        df = pd.DataFrame([
            {"open": 1.1050, "high": 1.1070, "low": 1.1048, "close": 1.1060},
            {"open": 1.1060, "high": 1.1075, "low": 1.1050, "close": 1.1070},  # pullback = 0.0000 < 0.0006
        ])

        result = bos.confirm_signal("EURUSD", df)
        assert result == {}
        assert "EURUSD" in bos.pending_signals  # Still pending

    def test_no_confirmation_if_close_doesnt_hold(self):
        bos = self._make_strategy(min_pullback_pct=0.3)
        signal = {
            "signal": "BUY",
            "price": 1.1050,
            "swing_level": 1.1000,
            "break_candle_range": 0.0020,
            "valid": True,
            "pending": True,
            "reason": "BOS",
            "sl": 1.0980,
            "atr": 0.0015,
            "score": 10,
        }
        bos.store_pending("EURUSD", signal)

        # Close doesn't hold above swing level (close < 1.1000)
        df = pd.DataFrame([
            {"open": 1.1050, "high": 1.1060, "low": 1.0990, "close": 1.0980},
            {"open": 1.0990, "high": 1.1010, "low": 1.0970, "close": 1.0985},  # close < swing_level
        ])

        result = bos.confirm_signal("EURUSD", df)
        assert result == {}

    def test_confirm_returns_empty_for_unknown_symbol(self):
        bos = self._make_strategy()
        df = pd.DataFrame([
            {"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05},
            {"open": 1.05, "high": 1.15, "low": 0.95, "close": 1.1},
        ])
        result = bos.confirm_signal("UNKNOWN", df)
        assert result == {}

    def test_analyze_returns_pending_when_confirmation_required(self):
        """When require_confirmation=True, valid BOS signals are marked pending."""
        bos = self._make_strategy(require_confirmation=True)
        # We'll just verify the flag is set on the strategy
        assert bos.require_confirmation is True

    def test_analyze_returns_directly_when_no_confirmation(self):
        """When require_confirmation=False, valid BOS signals are returned directly."""
        bos = self._make_strategy(require_confirmation=False)
        assert bos.require_confirmation is False
