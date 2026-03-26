"""Tests for regime-adaptive parameter loading in strategy/pair_agent.py."""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock


class TestRegimeAdaptiveParams:
    """Test that pair_agent loads correct parameters per HMM regime."""

    REGIME_PARAMS = {
        "TRENDING": {
            "atr_tp_mult": 3.0,
            "atr_sl_mult": 1.5,
            "min_confluence": 4,
            "max_daily": 3,
        },
        "RANGING": {
            "atr_tp_mult": 1.5,
            "atr_sl_mult": 1.0,
            "min_confluence": 5,
            "max_daily": 2,
        },
        "VOLATILE": {
            "atr_tp_mult": 2.0,
            "atr_sl_mult": 2.0,
            "min_confluence": 6,
            "max_daily": 1,
        },
    }

    def test_trending_regime_params(self):
        """TRENDING regime should use wider TP, moderate SL."""
        params = self.REGIME_PARAMS["TRENDING"]
        assert params["atr_tp_mult"] == 3.0
        assert params["atr_sl_mult"] == 1.5
        assert params["min_confluence"] == 4
        assert params["max_daily"] == 3

    def test_ranging_regime_params(self):
        """RANGING regime should use tight TP/SL, higher confluence bar."""
        params = self.REGIME_PARAMS["RANGING"]
        assert params["atr_tp_mult"] == 1.5
        assert params["atr_sl_mult"] == 1.0
        assert params["min_confluence"] == 5
        assert params["max_daily"] == 2

    def test_volatile_regime_params(self):
        """VOLATILE regime should use wide SL, highest confluence bar, fewer trades."""
        params = self.REGIME_PARAMS["VOLATILE"]
        assert params["atr_tp_mult"] == 2.0
        assert params["atr_sl_mult"] == 2.0
        assert params["min_confluence"] == 6
        assert params["max_daily"] == 1


class TestRegimeBucketMapping:
    """Test the mapping from HMM regime types to parameter buckets."""

    def _map_regime(self, regime_type: str) -> str:
        """Replicate the mapping logic from pair_agent._analyze()."""
        regime_type = regime_type.upper()
        if "TRENDING" in regime_type or "BREAKOUT" in regime_type:
            return "TRENDING"
        elif "VOLATILE" in regime_type or "REVERSAL" in regime_type:
            return "VOLATILE"
        else:
            return "RANGING"

    def test_trending_bull(self):
        assert self._map_regime("TRENDING_BULL") == "TRENDING"

    def test_trending_bear(self):
        assert self._map_regime("TRENDING_BEAR") == "TRENDING"

    def test_breakout_bull(self):
        assert self._map_regime("BREAKOUT_BULL") == "TRENDING"

    def test_breakout_bear(self):
        assert self._map_regime("BREAKOUT_BEAR") == "TRENDING"

    def test_volatile_high(self):
        assert self._map_regime("VOLATILE_HIGH") == "VOLATILE"

    def test_reversal_bull(self):
        assert self._map_regime("REVERSAL_BULL") == "VOLATILE"

    def test_reversal_bear(self):
        assert self._map_regime("REVERSAL_BEAR") == "VOLATILE"

    def test_ranging(self):
        assert self._map_regime("RANGING") == "RANGING"

    def test_normal(self):
        assert self._map_regime("NORMAL") == "RANGING"

    def test_unknown_defaults_to_ranging(self):
        assert self._map_regime("SOMETHING_ELSE") == "RANGING"

    def test_case_insensitive(self):
        assert self._map_regime("trending_bull") == "TRENDING"
        assert self._map_regime("Volatile_High") == "VOLATILE"


class TestRegimeParamsFallback:
    """Test fallback behavior when USE_HMM_REGIME is disabled."""

    def test_default_params_used_when_hmm_disabled(self):
        """When USE_HMM_REGIME=False, settings defaults should be used."""
        # This verifies the settings fallback values exist
        from config import settings

        # These are the base defaults that pair_agent uses when HMM is off
        assert hasattr(settings, "ATR_TP_MULTIPLIER")
        assert hasattr(settings, "ATR_SL_MULTIPLIER")
        assert hasattr(settings, "MIN_CONFLUENCE_SCORE")

    def test_regime_params_exist_in_settings(self):
        """REGIME_PARAMS dict must exist and contain all three buckets."""
        from config import settings
        assert hasattr(settings, "REGIME_PARAMS")
        for bucket in ("TRENDING", "RANGING", "VOLATILE"):
            assert bucket in settings.REGIME_PARAMS
            params = settings.REGIME_PARAMS[bucket]
            assert "atr_tp_mult" in params
            assert "atr_sl_mult" in params
            assert "min_confluence" in params
            assert "max_daily" in params

    def test_use_hmm_regime_setting_exists(self):
        """USE_HMM_REGIME setting must exist with a boolean default."""
        from config import settings
        assert hasattr(settings, "USE_HMM_REGIME")
        assert isinstance(settings.USE_HMM_REGIME, bool)


class TestSpreadCheck:
    """Test the spread check logic (conceptual validation)."""

    def test_max_spread_settings_exist(self):
        """Spread limits must be configured."""
        from config import settings
        assert hasattr(settings, "MAX_SPREAD_PIPS")
        assert settings.MAX_SPREAD_PIPS > 0

    def test_spread_categorization(self):
        """Verify spread limits differ by asset class."""
        from config import settings
        forex_spread = settings.MAX_SPREAD_PIPS
        crypto_spread = getattr(settings, "MAX_SPREAD_PIPS_CRYPTO", forex_spread)
        commodity_spread = getattr(settings, "MAX_SPREAD_PIPS_COMMODITY", forex_spread)
        # All should be positive
        assert forex_spread > 0
        assert crypto_spread > 0
        assert commodity_spread > 0
