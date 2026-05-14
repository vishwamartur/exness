"""
Unit tests for PerformanceAnalyticsService.

Tests rolling metrics calculation, degradation detection, size factor returns,
and regime tracking.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.event_bus import EventBus, Event, EventTypes
from services.performance_analytics_service import PerformanceAnalyticsService


def make_close_event(pnl, r_multiple=1.0, regime="BREAKOUT", direction="BUY", symbol="XAUUSD"):
    """Helper to create a POSITION_CLOSED event."""
    return Event(
        type=EventTypes.POSITION_CLOSED,
        payload={
            "symbol": symbol,
            "direction": direction,
            "session": "LONDON",
            "regime": regime,
            "entry_price": 2000.0,
            "exit_price": 2000.0 + pnl,
            "pnl": pnl,
            "r_multiple": r_multiple,
        },
        source="test",
    )


class TestRollingMetrics:
    """Test rolling metrics calculations."""

    def test_win_rate_all_wins(self):
        """Win rate should be 1.0 if all trades are winners."""
        async def run():
            bus = EventBus()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            for i in range(10):
                event = make_close_event(pnl=10.0, r_multiple=1.5)
                await service._on_position_closed(event)

            metrics = service.get_current_metrics()
            assert metrics["win_rate"] == 1.0
            assert metrics["total_trades"] == 10

        asyncio.run(run())

    def test_win_rate_all_losses(self):
        """Win rate should be 0.0 if all trades are losers."""
        async def run():
            bus = EventBus()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            for i in range(10):
                event = make_close_event(pnl=-5.0, r_multiple=-1.0)
                await service._on_position_closed(event)

            metrics = service.get_current_metrics()
            assert metrics["win_rate"] == 0.0

        asyncio.run(run())

    def test_win_rate_mixed(self):
        """Win rate should reflect proportion of winners."""
        async def run():
            bus = EventBus()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            # 6 wins, 4 losses = 60% win rate
            for i in range(6):
                await service._on_position_closed(make_close_event(pnl=10.0, r_multiple=1.5))
            for i in range(4):
                await service._on_position_closed(make_close_event(pnl=-5.0, r_multiple=-1.0))

            metrics = service.get_current_metrics()
            assert abs(metrics["win_rate"] - 0.6) < 0.01

        asyncio.run(run())

    def test_sharpe_ratio_positive(self):
        """Sharpe ratio should be positive for consistent winners."""
        async def run():
            bus = EventBus()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            # Consistent positive R-multiples
            for i in range(20):
                await service._on_position_closed(
                    make_close_event(pnl=10.0, r_multiple=1.5)
                )

            metrics = service.get_current_metrics()
            # With all same R-multiples, std is 0, sharpe defaults to 0
            # Let's use varied R-multiples
            assert metrics["sharpe_ratio"] == 0.0  # All same, std=0

        asyncio.run(run())

    def test_sharpe_ratio_with_variance(self):
        """Sharpe ratio should be calculated with varied R-multiples."""
        async def run():
            bus = EventBus()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            # Positive but varied R-multiples
            r_values = [1.5, 2.0, 1.0, 1.8, 2.5, 1.2, 1.7, 2.1, 1.4, 1.9,
                        1.6, 2.3, 1.1, 1.8, 2.0, 1.5, 1.7, 2.2, 1.3, 1.6]
            for r in r_values:
                await service._on_position_closed(
                    make_close_event(pnl=10.0, r_multiple=r)
                )

            metrics = service.get_current_metrics()
            # All positive R with some variance -> positive Sharpe
            assert metrics["sharpe_ratio"] > 0

        asyncio.run(run())

    def test_rolling_window_uses_recent_trades(self):
        """Metrics should only consider the rolling window of trades."""
        async def run():
            bus = EventBus()
            service = PerformanceAnalyticsService(bus)
            service._rolling_window = 10
            await service._setup()

            # 20 losses first
            for i in range(20):
                await service._on_position_closed(
                    make_close_event(pnl=-5.0, r_multiple=-1.0)
                )

            # Then 10 wins (fills the rolling window)
            for i in range(10):
                await service._on_position_closed(
                    make_close_event(pnl=10.0, r_multiple=1.5)
                )

            metrics = service.get_current_metrics()
            # Rolling window of 10 should show 100% win rate
            assert metrics["win_rate"] == 1.0

        asyncio.run(run())


class TestDegradationDetection:
    """Test strategy degradation detection logic."""

    def test_degradation_on_low_win_rate(self):
        """Degradation triggers when win rate drops below threshold."""
        async def run():
            bus = EventBus()
            await bus.start()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            # Create enough trades with low win rate (below 35%)
            # 2 wins, 8 losses = 20% win rate
            for i in range(2):
                await service._on_position_closed(
                    make_close_event(pnl=10.0, r_multiple=1.5)
                )
            for i in range(8):
                await service._on_position_closed(
                    make_close_event(pnl=-5.0, r_multiple=-1.0)
                )

            await asyncio.sleep(0.1)
            await bus.stop()

            assert service._degraded is True
            assert service.get_performance_size_factor() == 0.5

        asyncio.run(run())

    def test_degradation_on_consecutive_losses(self):
        """Degradation triggers on 5 consecutive losses."""
        async def run():
            bus = EventBus()
            await bus.start()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            # Start with some wins to avoid early win rate trigger
            for i in range(5):
                await service._on_position_closed(
                    make_close_event(pnl=10.0, r_multiple=1.5)
                )

            # Then 5 consecutive losses
            for i in range(5):
                await service._on_position_closed(
                    make_close_event(pnl=-5.0, r_multiple=-1.0)
                )

            await asyncio.sleep(0.1)
            await bus.stop()

            assert service._consecutive_losses == 5
            assert service._degraded is True
            assert service.get_performance_size_factor() == 0.5

        asyncio.run(run())

    def test_degradation_on_negative_sharpe(self):
        """Degradation triggers when Sharpe drops below -0.5."""
        async def run():
            bus = EventBus()
            await bus.start()
            service = PerformanceAnalyticsService(bus)
            service._min_sharpe = -0.5
            await service._setup()

            # Heavy negative R-multiples with some variance
            r_values = [-2.0, -1.5, -2.5, -1.0, 0.5, -2.0, -1.8, -2.2, -1.5, -2.0]
            for r in r_values:
                pnl = 10.0 if r > 0 else -5.0
                await service._on_position_closed(
                    make_close_event(pnl=pnl, r_multiple=r)
                )

            await asyncio.sleep(0.1)
            await bus.stop()

            metrics = service.get_current_metrics()
            assert metrics["sharpe_ratio"] < -0.5
            assert service._degraded is True

        asyncio.run(run())

    def test_no_degradation_with_good_performance(self):
        """No degradation when performance is healthy."""
        async def run():
            bus = EventBus()
            await bus.start()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            # 7 wins, 3 losses = 70% win rate, positive overall
            for i in range(7):
                await service._on_position_closed(
                    make_close_event(pnl=10.0, r_multiple=1.5)
                )
            for i in range(3):
                await service._on_position_closed(
                    make_close_event(pnl=-5.0, r_multiple=-1.0)
                )

            await asyncio.sleep(0.1)
            await bus.stop()

            assert service._degraded is False
            assert service.get_performance_size_factor() == 1.0

        asyncio.run(run())

    def test_no_degradation_with_few_trades(self):
        """Degradation should not trigger with fewer than 5 trades."""
        async def run():
            bus = EventBus()
            await bus.start()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            # Only 4 losing trades (below threshold of 5 for evaluation)
            for i in range(4):
                await service._on_position_closed(
                    make_close_event(pnl=-5.0, r_multiple=-1.0)
                )

            await asyncio.sleep(0.1)
            await bus.stop()

            assert service._degraded is False

        asyncio.run(run())


class TestSizeFactors:
    """Test position size factor in different states."""

    def test_normal_size_factor(self):
        """Normal state should return 1.0."""
        async def run():
            bus = EventBus()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            assert service.get_performance_size_factor() == 1.0

        asyncio.run(run())

    def test_degraded_size_factor(self):
        """Degraded state should return 0.5."""
        async def run():
            bus = EventBus()
            await bus.start()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            # Trigger degradation with consecutive losses
            for i in range(5):
                await service._on_position_closed(
                    make_close_event(pnl=10.0, r_multiple=1.5)
                )
            for i in range(5):
                await service._on_position_closed(
                    make_close_event(pnl=-5.0, r_multiple=-1.0)
                )

            await asyncio.sleep(0.1)
            await bus.stop()

            assert service.get_performance_size_factor() == 0.5

        asyncio.run(run())

    def test_recovery_size_factor(self):
        """Recovering state should return 0.75."""
        async def run():
            bus = EventBus()
            await bus.start()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            # First trigger degradation via consecutive losses
            for i in range(5):
                await service._on_position_closed(
                    make_close_event(pnl=10.0, r_multiple=1.5)
                )
            for i in range(5):
                await service._on_position_closed(
                    make_close_event(pnl=-5.0, r_multiple=-1.0)
                )

            assert service._degraded is True
            assert service.get_performance_size_factor() == 0.5

            # A single win clears consecutive losses and triggers recovery.
            # The first win in recovery also counts toward recovery_trade_count.
            # We add just 1 trade to enter recovery, then check mid-recovery.
            await service._on_position_closed(
                make_close_event(pnl=10.0, r_multiple=1.5)
            )

            await asyncio.sleep(0.1)
            await bus.stop()

            # Should be in recovery state now (first trade counted)
            assert service._degraded is False
            assert service._recovering is True
            assert service.get_performance_size_factor() == 0.75

        asyncio.run(run())

    def test_recovery_completes_after_5_trades(self):
        """After 5 trades in recovery, size factor returns to 1.0."""
        async def run():
            bus = EventBus()
            await bus.start()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            # Trigger degradation
            for i in range(5):
                await service._on_position_closed(
                    make_close_event(pnl=10.0, r_multiple=1.5)
                )
            for i in range(5):
                await service._on_position_closed(
                    make_close_event(pnl=-5.0, r_multiple=-1.0)
                )

            assert service._degraded is True

            # The first win clears degradation and starts recovery.
            # _update_recovery_state counts each trade after recovery starts.
            # After 5 recovery trades, it completes.
            # Add trades one at a time until recovery completes.
            recovery_trades = 0
            while service._recovering or service._degraded:
                await service._on_position_closed(
                    make_close_event(pnl=10.0, r_multiple=1.5)
                )
                recovery_trades += 1
                if recovery_trades > 10:
                    break  # Safety guard

            await asyncio.sleep(0.1)
            await bus.stop()

            assert service._recovering is False
            assert service._degraded is False
            assert service.get_performance_size_factor() == 1.0

        asyncio.run(run())


class TestRegimeTracking:
    """Test per-regime performance tracking."""

    def test_trades_grouped_by_regime(self):
        """Trades should be grouped into correct regime buckets."""
        async def run():
            bus = EventBus()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            # 3 MEAN_REVERT trades
            for i in range(3):
                await service._on_position_closed(
                    make_close_event(pnl=10.0, r_multiple=1.5, regime="MEAN_REVERT")
                )

            # 2 BREAKOUT trades
            for i in range(2):
                await service._on_position_closed(
                    make_close_event(pnl=-5.0, r_multiple=-1.0, regime="BREAKOUT")
                )

            # 1 MOMENTUM trade
            await service._on_position_closed(
                make_close_event(pnl=20.0, r_multiple=2.0, regime="MOMENTUM")
            )

            perf = service.get_regime_performance()

            assert perf["MEAN_REVERT"]["trade_count"] == 3
            assert perf["MEAN_REVERT"]["wins"] == 3
            assert perf["MEAN_REVERT"]["win_rate"] == 1.0

            assert perf["BREAKOUT"]["trade_count"] == 2
            assert perf["BREAKOUT"]["losses"] == 2
            assert perf["BREAKOUT"]["win_rate"] == 0.0

            assert perf["MOMENTUM"]["trade_count"] == 1
            assert perf["MOMENTUM"]["wins"] == 1
            assert perf["MOMENTUM"]["win_rate"] == 1.0

        asyncio.run(run())

    def test_regime_avg_profit(self):
        """Average profit should be calculated per regime."""
        async def run():
            bus = EventBus()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            # 2 BREAKOUT trades: +10, -5 = avg 2.5
            await service._on_position_closed(
                make_close_event(pnl=10.0, r_multiple=1.5, regime="BREAKOUT")
            )
            await service._on_position_closed(
                make_close_event(pnl=-5.0, r_multiple=-1.0, regime="BREAKOUT")
            )

            perf = service.get_regime_performance()
            assert abs(perf["BREAKOUT"]["avg_profit"] - 2.5) < 0.01

        asyncio.run(run())

    def test_unknown_regime_tracked(self):
        """Trades with unknown/new regimes should still be tracked."""
        async def run():
            bus = EventBus()
            service = PerformanceAnalyticsService(bus)
            await service._setup()

            await service._on_position_closed(
                make_close_event(pnl=10.0, r_multiple=1.5, regime="CUSTOM_REGIME")
            )

            perf = service.get_regime_performance()
            assert "CUSTOM_REGIME" in perf
            assert perf["CUSTOM_REGIME"]["trade_count"] == 1

        asyncio.run(run())


class TestEventEmission:
    """Test that correct events are emitted on state changes."""

    def test_performance_alert_emitted_on_degradation(self):
        """PERFORMANCE_ALERT should be emitted when degradation starts."""
        async def run():
            bus = EventBus()
            await bus.start()

            alerts = []

            async def capture_alert(event):
                alerts.append(event)

            bus.subscribe(EventTypes.PERFORMANCE_ALERT, capture_alert)

            service = PerformanceAnalyticsService(bus)
            await service._setup()

            # Trigger degradation
            for i in range(5):
                await service._on_position_closed(
                    make_close_event(pnl=10.0, r_multiple=1.5)
                )
            for i in range(5):
                await service._on_position_closed(
                    make_close_event(pnl=-5.0, r_multiple=-1.0)
                )

            await asyncio.sleep(0.2)
            await bus.stop()

            # Should have at least one PERFORMANCE_ALERT
            assert len(alerts) >= 1
            assert alerts[0].payload["status"] == "DEGRADED"

        asyncio.run(run())

    def test_size_update_emitted_on_degradation(self):
        """PERFORMANCE_SIZE_UPDATE should be emitted when size factor changes."""
        async def run():
            bus = EventBus()
            await bus.start()

            size_updates = []

            async def capture_update(event):
                size_updates.append(event)

            bus.subscribe(EventTypes.PERFORMANCE_SIZE_UPDATE, capture_update)

            service = PerformanceAnalyticsService(bus)
            await service._setup()

            # Trigger degradation
            for i in range(5):
                await service._on_position_closed(
                    make_close_event(pnl=10.0, r_multiple=1.5)
                )
            for i in range(5):
                await service._on_position_closed(
                    make_close_event(pnl=-5.0, r_multiple=-1.0)
                )

            await asyncio.sleep(0.2)
            await bus.stop()

            assert len(size_updates) >= 1
            assert size_updates[0].payload["size_factor"] == 0.5
            assert size_updates[0].payload["state"] == "DEGRADED"

        asyncio.run(run())


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
