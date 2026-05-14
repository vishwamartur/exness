"""
Tests for optimization/parameter_tuner.py

Validates guardrails, parameter change logging, per-session optimization,
and tunable parameter range definitions.
"""

import os
import sys
import json
import tempfile
import shutil

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from optimization.parameter_tuner import ParameterTuner


class TestGuardrails:
    """Tests for the _apply_guardrails method."""

    def test_guardrails_clip_correctly(self):
        """Verify params are clipped to 20% of defaults when exceeding bounds."""
        tuner = ParameterTuner(data_dir=tempfile.mkdtemp())

        # Propose values far outside 20% of defaults
        proposed = {
            "ATR_SL_MULTIPLIER": 5.0,   # default 1.5, max allowed = 1.5 * 1.2 = 1.8
            "ATR_TP_MULTIPLIER": 1.0,   # default 3.0, min allowed = 3.0 * 0.8 = 2.4
            "ASIAN_RSI_OVERBOUGHT": 90,  # default 70, max allowed = 70 * 1.2 = 84 (but abs max is 80)
        }

        clipped = tuner._apply_guardrails(proposed)

        # ATR_SL_MULTIPLIER: capped at min(1.8, abs_max=2.5) = 1.8
        assert clipped["ATR_SL_MULTIPLIER"] <= 1.5 * 1.2
        # ATR_TP_MULTIPLIER: floored at max(2.4, abs_min=2.0) = 2.4
        assert clipped["ATR_TP_MULTIPLIER"] >= 3.0 * 0.8
        # ASIAN_RSI_OVERBOUGHT: capped at min(84, abs_max=80) = 80
        assert clipped["ASIAN_RSI_OVERBOUGHT"] <= 80

        # Clean up
        shutil.rmtree(tuner.data_dir, ignore_errors=True)

    def test_guardrails_no_change_within_range(self):
        """Verify params within 20% range are unchanged."""
        tuner = ParameterTuner(data_dir=tempfile.mkdtemp())

        # Values within 20% of defaults
        proposed = {
            "ATR_SL_MULTIPLIER": 1.5,       # exactly default
            "ATR_TP_MULTIPLIER": 3.2,       # within 20% of 3.0
            "BREAKOUT_ATR_MULTIPLIER": 0.55, # within 20% of 0.5
            "ASIAN_RSI_OVERBOUGHT": 72,      # within 20% of 70
            "ASIAN_RSI_OVERSOLD": 28,        # within 20% of 30
            "MIN_CONFLUENCE_SCORE": 7.5,     # within 20% of 7
            "VOLUME_CONFIRMATION_MULTIPLIER": 1.6,  # within 20% of 1.5
        }

        clipped = tuner._apply_guardrails(proposed)

        # All should remain unchanged (within bounds)
        assert clipped["ATR_SL_MULTIPLIER"] == 1.5
        assert clipped["ATR_TP_MULTIPLIER"] == 3.2
        assert clipped["BREAKOUT_ATR_MULTIPLIER"] == 0.55
        assert clipped["ASIAN_RSI_OVERBOUGHT"] == 72
        assert clipped["ASIAN_RSI_OVERSOLD"] == 28
        assert clipped["MIN_CONFLUENCE_SCORE"] == 7.5
        assert clipped["VOLUME_CONFIRMATION_MULTIPLIER"] == 1.6

        # Clean up
        shutil.rmtree(tuner.data_dir, ignore_errors=True)


class TestParameterChangeLogging:
    """Tests for the _log_parameter_change method."""

    def test_parameter_change_logging(self):
        """Verify JSONL format is correct with all required fields."""
        tmp_dir = tempfile.mkdtemp()
        tuner = ParameterTuner(data_dir=tmp_dir)

        old_params = {"ATR_SL_MULTIPLIER": 1.5, "ATR_TP_MULTIPLIER": 3.0}
        new_params = {"ATR_SL_MULTIPLIER": 1.6, "ATR_TP_MULTIPLIER": 3.2}

        tuner._log_parameter_change(old_params, new_params, "BREAKOUT", 1.25)

        # Read the log file
        log_path = os.path.join(tmp_dir, "param_change_log.jsonl")
        assert os.path.exists(log_path)

        with open(log_path, 'r') as f:
            lines = f.readlines()

        assert len(lines) == 1

        record = json.loads(lines[0])
        assert "timestamp" in record
        assert record["regime"] == "BREAKOUT"
        assert record["old"] == old_params
        assert record["new"] == new_params
        assert record["oos_sharpe"] == 1.25

        # Verify timestamp is ISO8601 format
        assert "T" in record["timestamp"]

        # Clean up
        shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_parameter_change_logging_appends(self):
        """Verify multiple log entries are appended correctly."""
        tmp_dir = tempfile.mkdtemp()
        tuner = ParameterTuner(data_dir=tmp_dir)

        tuner._log_parameter_change({"a": 1}, {"a": 2}, "MEAN_REVERT", 0.8)
        tuner._log_parameter_change({"b": 3}, {"b": 4}, "MOMENTUM", 1.1)

        log_path = os.path.join(tmp_dir, "param_change_log.jsonl")
        with open(log_path, 'r') as f:
            lines = f.readlines()

        assert len(lines) == 2

        record1 = json.loads(lines[0])
        record2 = json.loads(lines[1])
        assert record1["regime"] == "MEAN_REVERT"
        assert record2["regime"] == "MOMENTUM"

        # Clean up
        shutil.rmtree(tmp_dir, ignore_errors=True)


class TestPerSessionOptimization:
    """Tests for optimize_per_session method."""

    def test_per_session_returns_all_regimes(self):
        """Verify keys MEAN_REVERT, BREAKOUT, MOMENTUM exist."""
        import numpy as np
        import pandas as pd

        tmp_dir = tempfile.mkdtemp()
        tuner = ParameterTuner(data_dir=tmp_dir)

        # Create synthetic data spanning multiple months with various hours
        np.random.seed(42)
        n_rows = 5000
        base_price = 2000.0
        dates = pd.date_range(start="2024-01-01", periods=n_rows, freq="5min")
        closes = base_price + np.cumsum(np.random.randn(n_rows) * 0.5)
        highs = closes + np.abs(np.random.randn(n_rows) * 0.3)
        lows = closes - np.abs(np.random.randn(n_rows) * 0.3)
        opens = closes + np.random.randn(n_rows) * 0.1
        atrs = np.abs(np.random.randn(n_rows) * 0.5) + 0.5

        data_df = pd.DataFrame({
            'time': dates,
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'atr': atrs,
        })

        results = tuner.optimize_per_session(data_df)

        # Verify all three regimes are present
        assert "MEAN_REVERT" in results
        assert "BREAKOUT" in results
        assert "MOMENTUM" in results

        # Each regime should have parameter dicts
        for regime in ["MEAN_REVERT", "BREAKOUT", "MOMENTUM"]:
            assert isinstance(results[regime], dict)
            assert len(results[regime]) > 0

        # Clean up
        shutil.rmtree(tmp_dir, ignore_errors=True)


class TestTunableParams:
    """Tests for TUNABLE_PARAMS definition."""

    def test_tunable_params_have_valid_ranges(self):
        """Verify all params have min < default < max."""
        for param_name, config in ParameterTuner.TUNABLE_PARAMS.items():
            assert "default" in config, f"{param_name} missing 'default'"
            assert "min" in config, f"{param_name} missing 'min'"
            assert "max" in config, f"{param_name} missing 'max'"

            assert config["min"] < config["default"], (
                f"{param_name}: min ({config['min']}) should be < "
                f"default ({config['default']})"
            )
            assert config["default"] < config["max"], (
                f"{param_name}: default ({config['default']}) should be < "
                f"max ({config['max']})"
            )

    def test_tunable_params_all_expected_present(self):
        """Verify all expected tunable parameters are defined."""
        expected = [
            "ATR_SL_MULTIPLIER",
            "ATR_TP_MULTIPLIER",
            "BREAKOUT_ATR_MULTIPLIER",
            "ASIAN_RSI_OVERBOUGHT",
            "ASIAN_RSI_OVERSOLD",
            "MIN_CONFLUENCE_SCORE",
            "VOLUME_CONFIRMATION_MULTIPLIER",
        ]
        for param in expected:
            assert param in ParameterTuner.TUNABLE_PARAMS, (
                f"{param} not found in TUNABLE_PARAMS"
            )


if __name__ == "__main__":
    # Run tests manually without pytest
    import traceback

    test_classes = [
        TestGuardrails,
        TestParameterChangeLogging,
        TestPerSessionOptimization,
        TestTunableParams,
    ]

    passed = 0
    failed = 0

    for cls in test_classes:
        instance = cls()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                try:
                    getattr(instance, method_name)()
                    print(f"  PASS: {cls.__name__}.{method_name}")
                    passed += 1
                except Exception as e:
                    print(f"  FAIL: {cls.__name__}.{method_name}: {e}")
                    traceback.print_exc()
                    failed += 1

    print(f"\n{passed} passed, {failed} failed")
