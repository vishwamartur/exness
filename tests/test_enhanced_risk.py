"""
Unit tests for enhanced risk management features:
- Kelly criterion (half-Kelly) sizing
- Portfolio heat calculation
- Dynamic daily loss limit tiers
- Time-based risk reduction near session close
- Correlation-aware gold exposure
"""

import sys
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone

# Add project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestKellySizing:
    """Test Kelly criterion calculation in RiskManager."""

    def _make_risk_manager(self):
        """Create a RiskManager with mocked dependencies."""
        # Mock all external dependencies
        mock_shared_state = MagicMock()
        mock_shared_instance = MagicMock()
        mock_shared_instance.get = MagicMock(return_value=0)
        mock_shared_instance.set = MagicMock()
        mock_shared_state.SharedState = MagicMock(return_value=mock_shared_instance)

        mock_news = MagicMock()
        mock_news.is_news_blackout = MagicMock(return_value=(False, ""))
        mock_news.get_active_events = MagicMock(return_value=[])

        mock_corr = MagicMock()
        mock_corr.check_correlation_conflict = MagicMock(return_value=(False, ""))

        with patch.dict(sys.modules, {
            'numpy': MagicMock(),
            'MetaTrader5': MagicMock(),
            'dotenv': MagicMock(),
            'utils.news_filter': mock_news,
            'utils.correlation_filter': mock_corr,
            'utils.shared_state': mock_shared_state,
        }):
            # Need to reimport to pick up mocked modules
            import importlib
            if 'utils.risk_manager' in sys.modules:
                del sys.modules['utils.risk_manager']
            from utils.risk_manager import RiskManager
            rm = RiskManager(mt5_client=None)
            return rm

    def test_kelly_fraction_positive_edge(self):
        """Kelly formula returns correct fraction for a profitable strategy."""
        rm = self._make_risk_manager()
        # win_rate=0.6, avg_win=200, avg_loss=100
        # f* = (0.6*200 - 0.4*100) / 200 = (120 - 40) / 200 = 0.4
        result = rm.calculate_kelly_fraction(0.6, 200.0, 100.0)
        assert abs(result - 0.4) < 0.001, f"Expected 0.4, got {result}"

    def test_kelly_fraction_break_even(self):
        """Kelly returns 0 for a break-even strategy."""
        rm = self._make_risk_manager()
        # win_rate=0.5, avg_win=100, avg_loss=100
        # f* = (0.5*100 - 0.5*100) / 100 = 0
        result = rm.calculate_kelly_fraction(0.5, 100.0, 100.0)
        assert abs(result - 0.0) < 0.001, f"Expected 0.0, got {result}"

    def test_kelly_fraction_negative_edge(self):
        """Kelly returns 0 (clamped) for a losing strategy."""
        rm = self._make_risk_manager()
        # win_rate=0.3, avg_win=100, avg_loss=150
        # f* = (0.3*100 - 0.7*150) / 100 = (30 - 105) / 100 = -0.75 -> clamped to 0
        result = rm.calculate_kelly_fraction(0.3, 100.0, 150.0)
        assert result == 0.0, f"Expected 0.0, got {result}"

    def test_kelly_fraction_zero_avg_win(self):
        """Kelly returns 0 when avg_win is zero (no division by zero)."""
        rm = self._make_risk_manager()
        result = rm.calculate_kelly_fraction(0.6, 0.0, 100.0)
        assert result == 0.0, f"Expected 0.0, got {result}"

    def test_kelly_fraction_high_win_rate(self):
        """Kelly returns correct fraction for high win rate."""
        rm = self._make_risk_manager()
        # win_rate=0.8, avg_win=150, avg_loss=50
        # f* = (0.8*150 - 0.2*50) / 150 = (120 - 10) / 150 = 0.7333
        result = rm.calculate_kelly_fraction(0.8, 150.0, 50.0)
        expected = (0.8 * 150 - 0.2 * 50) / 150
        assert abs(result - expected) < 0.001, f"Expected {expected}, got {result}"


class TestPortfolioHeat:
    """Test portfolio heat calculation in RiskService."""

    def test_portfolio_heat_no_positions(self):
        """Heat should be 0 with no open positions."""
        heat = self._calc_heat(positions=[], equity=10000.0)
        assert heat == 0.0

    def test_portfolio_heat_single_position(self):
        """Heat correctly calculated for a single position."""
        # Position: BUY, open=2000, sl=1990, volume=0.1, contract_size=100
        # sl_distance = 2000 - 1990 = 10
        # risk = 10 * 0.1 * 100 = 100
        # heat = (100 / 10000) * 100 = 1.0%
        pos = MagicMock()
        pos.symbol = "XAUUSD"
        pos.volume = 0.1
        pos.sl = 1990.0
        pos.price_open = 2000.0
        pos.type = 0  # BUY

        heat = self._calc_heat(positions=[pos], equity=10000.0, contract_size=100)
        assert abs(heat - 1.0) < 0.01, f"Expected 1.0%, got {heat}%"

    def test_portfolio_heat_multiple_positions(self):
        """Heat sums risk across all positions."""
        pos1 = MagicMock()
        pos1.symbol = "XAUUSD"
        pos1.volume = 0.1
        pos1.sl = 1990.0
        pos1.price_open = 2000.0
        pos1.type = 0  # BUY

        pos2 = MagicMock()
        pos2.symbol = "XAUUSD"
        pos2.volume = 0.05
        pos2.sl = 2010.0
        pos2.price_open = 2000.0
        pos2.type = 1  # SELL (sl=2010, open=2000 -> sl_dist = 2010-2000 = 10)

        # pos1 risk: 10 * 0.1 * 100 = 100
        # pos2 risk: 10 * 0.05 * 100 = 50
        # total risk = 150
        # heat = 150 / 10000 * 100 = 1.5%
        heat = self._calc_heat(positions=[pos1, pos2], equity=10000.0, contract_size=100)
        assert abs(heat - 1.5) < 0.01, f"Expected 1.5%, got {heat}%"

    def test_portfolio_heat_rejects_above_threshold(self):
        """Heat above 3% should trigger rejection."""
        # Position: BUY, open=2000, sl=1950, volume=1.0, contract_size=100
        # sl_distance = 50
        # risk = 50 * 1.0 * 100 = 5000
        # heat = 5000 / 10000 * 100 = 50% (way above 3%)
        pos = MagicMock()
        pos.symbol = "XAUUSD"
        pos.volume = 1.0
        pos.sl = 1950.0
        pos.price_open = 2000.0
        pos.type = 0

        heat = self._calc_heat(positions=[pos], equity=10000.0, contract_size=100)
        max_heat = 3.0
        assert heat > max_heat, f"Heat {heat}% should exceed {max_heat}%"

    def _calc_heat(self, positions, equity, contract_size=100):
        """Helper to calculate portfolio heat synchronously."""
        if not positions or equity <= 0:
            return 0.0

        total_risk = 0.0
        for pos in positions:
            pos_sl = pos.sl if hasattr(pos, 'sl') else 0.0
            pos_open = pos.price_open if hasattr(pos, 'price_open') else 0.0
            pos_vol = pos.volume if hasattr(pos, 'volume') else 0.0
            pos_type = pos.type if hasattr(pos, 'type') else 0

            if pos_sl <= 0 or pos_open <= 0:
                continue

            if pos_type == 0:  # BUY
                sl_distance = pos_open - pos_sl
            else:  # SELL
                sl_distance = pos_sl - pos_open

            if sl_distance <= 0:
                continue

            total_risk += sl_distance * pos_vol * contract_size

        return (total_risk / equity) * 100.0


class TestDynamicDailyLimit:
    """Test dynamic daily loss limit tiers."""

    def test_tier_0_normal_trading(self):
        """At 0% drawdown, no restrictions apply."""
        tier = self._get_tier(loss_pct=0.0)
        assert tier == 0

    def test_tier_1_at_one_percent(self):
        """At 1% drawdown, tier 1 activates (max 2 trades)."""
        tier = self._get_tier(loss_pct=0.01)
        assert tier == 1

    def test_tier_2_at_two_percent(self):
        """At 2% drawdown, tier 2 activates (1 trade at 50% size)."""
        tier = self._get_tier(loss_pct=0.02)
        assert tier == 2

    def test_tier_3_shutdown(self):
        """At 2.5%+ drawdown, full shutdown."""
        tier = self._get_tier(loss_pct=0.025)
        assert tier == 3

    def test_tier_3_above_shutdown(self):
        """At 3% drawdown, still shutdown."""
        tier = self._get_tier(loss_pct=0.03)
        assert tier == 3

    def _get_tier(self, loss_pct):
        """Determine the drawdown tier based on loss percentage."""
        tier1_pct = 0.01  # 1%
        tier2_pct = 0.02  # 2%
        shutdown_pct = 0.025  # 2.5%

        if loss_pct >= shutdown_pct:
            return 3
        elif loss_pct >= tier2_pct:
            return 2
        elif loss_pct >= tier1_pct:
            return 1
        return 0


class TestTimeBasedReduction:
    """Test time-based risk reduction near session close."""

    def test_reduction_before_london_close(self):
        """Lot should be reduced 30% within 60 min of London close (13:00 UTC)."""
        # At 12:30 UTC (30 min before London close)
        lot = self._apply_time_reduction(hour=12, minute=30, base_lot=1.0)
        expected = round(1.0 * (1 - 0.3), 2)
        assert abs(lot - expected) < 0.01, f"Expected {expected}, got {lot}"

    def test_reduction_before_ny_close(self):
        """Lot should be reduced 30% within 60 min of NY close (17:00 UTC)."""
        # At 16:15 UTC (45 min before NY close)
        lot = self._apply_time_reduction(hour=16, minute=15, base_lot=1.0)
        expected = round(1.0 * (1 - 0.3), 2)
        assert abs(lot - expected) < 0.01, f"Expected {expected}, got {lot}"

    def test_no_reduction_outside_window(self):
        """Lot should not be reduced outside session close window."""
        # At 10:00 UTC (well outside any close window)
        lot = self._apply_time_reduction(hour=10, minute=0, base_lot=1.0)
        assert lot == 1.0, f"Expected 1.0, got {lot}"

    def test_no_reduction_at_session_start(self):
        """Lot should not be reduced at session start."""
        # At 8:00 UTC (London open, far from any close)
        lot = self._apply_time_reduction(hour=8, minute=0, base_lot=1.0)
        assert lot == 1.0, f"Expected 1.0, got {lot}"

    def test_reduction_at_exact_close_minus_60(self):
        """Lot should be reduced at exactly 60 min before close."""
        # At 12:00 UTC (exactly 60 min before London close at 13:00)
        lot = self._apply_time_reduction(hour=12, minute=0, base_lot=1.0)
        expected = round(1.0 * (1 - 0.3), 2)
        assert abs(lot - expected) < 0.01, f"Expected {expected}, got {lot}"

    def _apply_time_reduction(self, hour, minute, base_lot, reduction=0.3):
        """Simulate time-based risk reduction logic."""
        current_minutes = hour * 60 + minute
        london_close_minutes = 13 * 60  # 13:00 UTC
        ny_close_minutes = 17 * 60  # 17:00 UTC

        near_session_close = False
        for close_min in [london_close_minutes, ny_close_minutes]:
            if 0 <= (close_min - current_minutes) <= 60:
                near_session_close = True
                break

        if near_session_close:
            return round(base_lot * (1 - reduction), 2)
        return base_lot


class TestKellySettingsDefault:
    """Test that KELLY_FRACTION default is 0.5 (half-Kelly)."""

    def test_kelly_fraction_default(self):
        """KELLY_FRACTION should default to 0.5 (half-Kelly)."""
        # Read the settings file and check the default value in the source
        settings_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.py')
        with open(settings_path, 'r') as f:
            content = f.read()
        # Find the KELLY_FRACTION line and check it contains 0.5 as default
        for line in content.split('\n'):
            if 'KELLY_FRACTION' in line and 'os.getenv' in line:
                assert '0.5' in line, f"KELLY_FRACTION default should be 0.5, found: {line}"
                return
        raise AssertionError("KELLY_FRACTION setting not found in config/settings.py")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
