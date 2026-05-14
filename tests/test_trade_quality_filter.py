"""
Unit tests for TradeQualityFilter service.
Tests each filter independently and the grading logic.
"""

import asyncio
import sys
import os
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

# Add project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.event_bus import EventBus, Event, EventTypes
from services.trade_quality_filter import TradeQualityFilter


@pytest.fixture
def event_bus():
    """Create a fresh EventBus for each test."""
    return EventBus()


@pytest.fixture
def quality_filter(event_bus):
    """Create a TradeQualityFilter instance."""
    with patch('services.trade_quality_filter.settings') as mock_settings:
        mock_settings.TRADE_QUALITY_FILTER_ENABLED = True
        mock_settings.NEWS_QUALITY_BUFFER_MINUTES = 15
        mock_settings.SPREAD_QUALITY_MULTIPLIER = 2.0
        mock_settings.VOLATILITY_SPIKE_MULTIPLIER = 3.0
        mock_settings.ROUND_NUMBER_BUFFER_PIPS = 50
        mock_settings.ROUND_NUMBER_INTERVAL = 50
        f = TradeQualityFilter(event_bus)
    return f


class TestNewsProximityFilter:
    """Tests for the news proximity filter."""

    def test_rejects_during_news_blackout(self, quality_filter):
        """Should reject when is_news_blackout returns True."""
        with patch('utils.news_filter.is_news_blackout', return_value=(True, "NFP")):
            with patch('utils.news_filter.get_upcoming_events', return_value=[]):
                passed, reason, marginal = quality_filter._check_news_proximity("XAUUSD")
                assert not passed
                assert "NFP" in reason

    def test_rejects_within_buffer_window(self, quality_filter):
        """Should reject when upcoming event is within 15 minutes."""
        now = datetime.now(timezone.utc)
        upcoming_event = {
            'name': 'CPI',
            'currency': 'USD',
            'dt_utc': now + timedelta(minutes=10),  # 10 min away
        }
        with patch('utils.news_filter.is_news_blackout', return_value=(False, "")):
            with patch('utils.news_filter.get_upcoming_events', return_value=[upcoming_event]):
                passed, reason, marginal = quality_filter._check_news_proximity("XAUUSD")
                assert not passed
                assert "CPI" in reason

    def test_passes_when_no_news(self, quality_filter):
        """Should pass when no upcoming news events."""
        with patch('utils.news_filter.is_news_blackout', return_value=(False, "")):
            with patch('utils.news_filter.get_upcoming_events', return_value=[]):
                passed, reason, marginal = quality_filter._check_news_proximity("XAUUSD")
                assert passed
                assert not marginal

    def test_marginal_when_news_within_double_buffer(self, quality_filter):
        """Should be marginal when news is between 15 and 30 minutes away."""
        now = datetime.now(timezone.utc)
        upcoming_event = {
            'name': 'GDP',
            'currency': 'USD',
            'dt_utc': now + timedelta(minutes=25),  # 25 min away (between 15 and 30)
        }
        with patch('utils.news_filter.is_news_blackout', return_value=(False, "")):
            with patch('utils.news_filter.get_upcoming_events', return_value=[upcoming_event]):
                passed, reason, marginal = quality_filter._check_news_proximity("XAUUSD")
                assert passed
                assert marginal


class TestSpreadQualityFilter:
    """Tests for the spread quality filter."""

    def test_rejects_at_2x_threshold(self, quality_filter):
        """Should reject when spread > 2x session average."""
        # Set session average spread to 2.0
        quality_filter._session_avg_spread["london"] = 2.0
        # Current spread is 5.0 (2.5x average)
        passed, reason, marginal = quality_filter._check_spread_quality(5.0, "london")
        assert not passed
        assert "Spread" in reason

    def test_passes_normal_spread(self, quality_filter):
        """Should pass when spread is below threshold."""
        quality_filter._session_avg_spread["london"] = 2.0
        # Current spread is 1.5 (0.75x average - well below 2x)
        passed, reason, marginal = quality_filter._check_spread_quality(1.5, "london")
        assert passed
        assert not marginal

    def test_marginal_near_threshold(self, quality_filter):
        """Should be marginal when spread is between 75% and 100% of rejection threshold."""
        quality_filter._session_avg_spread["london"] = 2.0
        # Marginal threshold = 2.0 * 0.75 = 1.5x average
        # Spread 3.2 = 1.6x avg (above 1.5x marginal, below 2.0x reject)
        passed, reason, marginal = quality_filter._check_spread_quality(3.2, "london")
        assert passed
        assert marginal

    def test_passes_when_no_session_data(self, quality_filter):
        """Should pass (marginal) when no session average data exists."""
        passed, reason, marginal = quality_filter._check_spread_quality(5.0, "unknown_session")
        assert passed
        assert marginal

    def test_spread_ema_updates(self, quality_filter):
        """EMA should update correctly with new spread values."""
        quality_filter.update_spread("london", 2.0)
        assert quality_filter._session_avg_spread["london"] == 2.0

        # EMA with alpha=0.1: new = 0.1*4.0 + 0.9*2.0 = 2.2
        quality_filter.update_spread("london", 4.0)
        expected = 0.1 * 4.0 + 0.9 * 2.0
        assert abs(quality_filter._session_avg_spread["london"] - expected) < 0.001


class TestVolatilitySpikeFilter:
    """Tests for the volatility spike filter."""

    def test_rejects_at_3x_atr(self, quality_filter):
        """Should reject when 5-bar ATR > 3x session average ATR."""
        quality_filter._session_avg_atr["ny"] = 1.0
        # Current ATR is 3.5 (3.5x average - above 3x threshold)
        passed, reason, marginal = quality_filter._check_volatility_spike(3.5, "ny")
        assert not passed
        assert "ATR spike" in reason

    def test_passes_normal_atr(self, quality_filter):
        """Should pass when ATR is normal relative to session average."""
        quality_filter._session_avg_atr["ny"] = 1.0
        # Current ATR is 1.5 (1.5x average - well below 3x)
        passed, reason, marginal = quality_filter._check_volatility_spike(1.5, "ny")
        assert passed
        assert not marginal

    def test_marginal_near_spike_threshold(self, quality_filter):
        """Should be marginal when ATR ratio is between 75% of threshold and threshold."""
        quality_filter._session_avg_atr["ny"] = 1.0
        # Marginal threshold = 3.0 * 0.75 = 2.25x
        # ATR 2.5 = 2.5x avg (above 2.25x marginal, below 3.0x reject)
        passed, reason, marginal = quality_filter._check_volatility_spike(2.5, "ny")
        assert passed
        assert marginal

    def test_passes_when_no_baseline(self, quality_filter):
        """Should pass (marginal) when no session ATR baseline exists."""
        passed, reason, marginal = quality_filter._check_volatility_spike(5.0, "unknown")
        assert passed
        assert marginal

    def test_atr_tracking_updates(self, quality_filter):
        """ATR tracking should maintain rolling average."""
        for i in range(10):
            quality_filter.update_atr("london", 1.0)
        assert abs(quality_filter._session_avg_atr["london"] - 1.0) < 0.001

        # Add a spike value
        quality_filter.update_atr("london", 5.0)
        # Average of 10 * 1.0 + 1 * 5.0 = 15.0 / 11 = 1.3636...
        expected = (10 * 1.0 + 5.0) / 11
        assert abs(quality_filter._session_avg_atr["london"] - expected) < 0.001


class TestRoundNumberFilter:
    """Tests for the round number filter."""

    def test_rejects_near_round_number(self, quality_filter):
        """Should reject when price is within 50 pips ($5) of a round level."""
        # Price 2398 is $2 from 2400 (within $5 buffer)
        passed, reason, marginal = quality_filter._check_round_number(2398.0)
        assert not passed
        assert "2400" in reason

    def test_rejects_near_2350(self, quality_filter):
        """Should reject near 2350 (interval=50)."""
        # Price 2347 is $3 from 2350 (within $5 buffer)
        passed, reason, marginal = quality_filter._check_round_number(2347.0)
        assert not passed
        assert "2350" in reason

    def test_passes_between_round_numbers(self, quality_filter):
        """Should pass when price is well between round numbers."""
        # Price 2325 is $25 from both 2300 and 2350 (well outside $5 buffer)
        passed, reason, marginal = quality_filter._check_round_number(2325.0)
        assert passed
        assert not marginal

    def test_marginal_near_buffer_edge(self, quality_filter):
        """Should be marginal within 2x buffer but outside buffer."""
        # Buffer = $5, 2x buffer = $10
        # Price 2392 is $8 from 2400 (between $5 and $10)
        passed, reason, marginal = quality_filter._check_round_number(2392.0)
        assert passed
        assert marginal

    def test_handles_zero_price(self, quality_filter):
        """Should pass gracefully with zero price."""
        passed, reason, marginal = quality_filter._check_round_number(0)
        assert passed
        assert not marginal


class TestTradeGrading:
    """Tests for the quality grade assignment."""

    def test_grade_a_all_clear(self, quality_filter):
        """Grade A when all filters pass with no marginal flags."""
        results = {
            "news_proximity": (True, "", False),
            "spread_quality": (True, "", False),
            "volatility_spike": (True, "", False),
            "round_number": (True, "", False),
        }
        grade = quality_filter._calculate_grade(results)
        assert grade == "A"

    def test_grade_b_one_marginal(self, quality_filter):
        """Grade B when one filter is marginal."""
        results = {
            "news_proximity": (True, "", True),  # marginal
            "spread_quality": (True, "", False),
            "volatility_spike": (True, "", False),
            "round_number": (True, "", False),
        }
        grade = quality_filter._calculate_grade(results)
        assert grade == "B"

    def test_grade_c_multiple_marginal(self, quality_filter):
        """Grade C when multiple filters are marginal."""
        results = {
            "news_proximity": (True, "", True),   # marginal
            "spread_quality": (True, "", True),    # marginal
            "volatility_spike": (True, "", False),
            "round_number": (True, "", False),
        }
        grade = quality_filter._calculate_grade(results)
        assert grade == "C"

    def test_grade_c_all_marginal(self, quality_filter):
        """Grade C when all filters are marginal."""
        results = {
            "news_proximity": (True, "", True),
            "spread_quality": (True, "", True),
            "volatility_spike": (True, "", True),
            "round_number": (True, "", True),
        }
        grade = quality_filter._calculate_grade(results)
        assert grade == "C"


class TestServiceIntegration:
    """Integration tests for the full service flow."""

    def test_disabled_filter_skips_processing(self, event_bus):
        """When disabled, should not process signals."""
        with patch('services.trade_quality_filter.settings') as mock_settings:
            mock_settings.TRADE_QUALITY_FILTER_ENABLED = False
            mock_settings.NEWS_QUALITY_BUFFER_MINUTES = 15
            mock_settings.SPREAD_QUALITY_MULTIPLIER = 2.0
            mock_settings.VOLATILITY_SPIKE_MULTIPLIER = 3.0
            mock_settings.ROUND_NUMBER_BUFFER_PIPS = 50
            mock_settings.ROUND_NUMBER_INTERVAL = 50
            f = TradeQualityFilter(event_bus)

        # When disabled, _on_session_trade_signal should return early
        async def run():
            emitted = []
            original_emit = f.emit

            async def mock_emit(event_type, payload=None):
                emitted.append(event_type)

            f.emit = mock_emit
            event = Event(
                type=EventTypes.SESSION_TRADE_SIGNAL,
                payload={"symbol": "XAUUSD", "price": 2300, "session": "london"},
            )
            await f._on_session_trade_signal(event)
            return emitted

        result = asyncio.run(run())
        assert len(result) == 0  # No events emitted when disabled

    def test_quality_checked_signals_are_skipped(self, quality_filter):
        """Signals already marked quality_checked should be skipped."""
        async def run():
            emitted = []
            original_emit = quality_filter.emit

            async def mock_emit(event_type, payload=None):
                emitted.append(event_type)

            quality_filter.emit = mock_emit
            event = Event(
                type=EventTypes.SESSION_TRADE_SIGNAL,
                payload={
                    "symbol": "XAUUSD",
                    "price": 2325,
                    "session": "london",
                    "quality_checked": True,
                },
            )
            await quality_filter._on_session_trade_signal(event)
            return emitted

        result = asyncio.run(run())
        assert len(result) == 0

    def test_signal_passing_emits_graded_event(self, quality_filter):
        """A signal passing all filters should emit TRADE_QUALITY_GRADED."""
        async def run():
            emitted = []

            async def mock_emit(event_type, payload=None):
                emitted.append((event_type, payload))

            quality_filter.emit = mock_emit
            # Set up session data so filters have baselines
            quality_filter._session_avg_spread["london"] = 2.0
            quality_filter._session_avg_atr["london"] = 1.0

            with patch('utils.news_filter.is_news_blackout', return_value=(False, "")):
                with patch('utils.news_filter.get_upcoming_events', return_value=[]):
                    event = Event(
                        type=EventTypes.SESSION_TRADE_SIGNAL,
                        payload={
                            "symbol": "XAUUSD",
                            "price": 2325.0,  # Not near round number
                            "spread": 1.5,     # Below 2x avg of 2.0
                            "atr": 1.2,        # Below 3x avg of 1.0
                            "session": "london",
                        },
                    )
                    await quality_filter._on_session_trade_signal(event)

            return emitted

        result = asyncio.run(run())
        assert len(result) == 1
        event_type, payload = result[0]
        assert event_type == EventTypes.TRADE_QUALITY_GRADED
        assert payload["quality_grade"] in ("A", "B", "C")
        assert payload["quality_checked"] is True

    def test_signal_rejected_emits_rejected_event(self, quality_filter):
        """A signal failing a filter should emit TRADE_REJECTED."""
        async def run():
            emitted = []

            async def mock_emit(event_type, payload=None):
                emitted.append((event_type, payload))

            quality_filter.emit = mock_emit
            quality_filter._session_avg_spread["london"] = 2.0
            quality_filter._session_avg_atr["london"] = 1.0

            with patch('utils.news_filter.is_news_blackout', return_value=(True, "FOMC")):
                with patch('utils.news_filter.get_upcoming_events', return_value=[]):
                    event = Event(
                        type=EventTypes.SESSION_TRADE_SIGNAL,
                        payload={
                            "symbol": "XAUUSD",
                            "price": 2325.0,
                            "spread": 1.5,
                            "atr": 1.0,
                            "session": "london",
                        },
                    )
                    await quality_filter._on_session_trade_signal(event)

            return emitted

        result = asyncio.run(run())
        assert len(result) == 1
        event_type, payload = result[0]
        assert event_type == EventTypes.TRADE_REJECTED
        assert payload["reason"] == "quality_filter"
        assert any("FOMC" in r for r in payload["filter_reasons"])


class TestMarketDataHandler:
    """Tests for market data event processing."""

    def test_spread_ema_on_market_data(self, quality_filter):
        """Market data events should update spread EMA."""
        async def run():
            event = Event(
                type=EventTypes.MARKET_DATA_READY,
                payload={"session": "ny", "spread": 3.0, "atr": 1.5},
            )
            await quality_filter._on_market_data(event)
            assert quality_filter._session_avg_spread["ny"] == 3.0

            # Second update: EMA = 0.1 * 5.0 + 0.9 * 3.0 = 3.2
            event2 = Event(
                type=EventTypes.MARKET_DATA_READY,
                payload={"session": "ny", "spread": 5.0, "atr": 2.0},
            )
            await quality_filter._on_market_data(event2)
            expected = 0.1 * 5.0 + 0.9 * 3.0
            assert abs(quality_filter._session_avg_spread["ny"] - expected) < 0.001

        asyncio.run(run())

    def test_atr_tracking_on_market_data(self, quality_filter):
        """Market data events should update ATR tracking."""
        async def run():
            for i in range(5):
                event = Event(
                    type=EventTypes.MARKET_DATA_READY,
                    payload={"session": "london", "spread": 2.0, "atr": 1.0},
                )
                await quality_filter._on_market_data(event)

            assert len(quality_filter._recent_atr_bars["london"]) == 5
            assert abs(quality_filter._session_avg_atr["london"] - 1.0) < 0.001

        asyncio.run(run())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
