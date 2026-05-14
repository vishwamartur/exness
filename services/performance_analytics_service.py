"""
PerformanceAnalyticsService — Rolling performance metrics, strategy degradation detection,
and adaptive position sizing.

Subscribes to: POSITION_CLOSED
Publishes: PERFORMANCE_ALERT, PERFORMANCE_SIZE_UPDATE

Tracks per-regime performance (MEAN_REVERT, BREAKOUT, MOMENTUM), detects degradation
via win rate, Sharpe ratio, and consecutive loss thresholds, and auto-reduces position
size during underperformance periods.
"""

import logging
import math
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from config import settings

logger = logging.getLogger("PerformanceAnalyticsService")


class PerformanceAnalyticsService(BaseService):
    """
    Monitors rolling trading performance and adjusts position sizing
    when strategy degradation is detected.
    """

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)

        # Configuration from settings
        self._rolling_window = getattr(settings, 'PERF_ROLLING_WINDOW', 20)
        self._min_win_rate = getattr(settings, 'PERF_MIN_WIN_RATE', 0.35)
        self._min_sharpe = getattr(settings, 'PERF_MIN_SHARPE', -0.5)
        self._degraded_size_factor = getattr(settings, 'PERF_DEGRADED_SIZE_FACTOR', 0.5)
        self._recovery_size_factor = getattr(settings, 'PERF_RECOVERY_SIZE_FACTOR', 0.75)
        self._consecutive_loss_threshold = getattr(settings, 'PERF_CONSECUTIVE_LOSS_THRESHOLD', 5)

        # Trade history (last 100 trades)
        self._trade_history: deque = deque(maxlen=100)

        # Current rolling metrics
        self._current_metrics: Dict = {
            "sharpe_ratio": 0.0,
            "win_rate": 0.0,
            "avg_rr_achieved": 0.0,
            "avg_rr_planned": 0.0,
            "total_trades": 0,
        }

        # Per-regime performance tracking
        self._regime_performance: Dict[str, Dict] = {
            "MEAN_REVERT": {"wins": 0, "losses": 0, "total_profit": 0.0, "trade_count": 0},
            "BREAKOUT": {"wins": 0, "losses": 0, "total_profit": 0.0, "trade_count": 0},
            "MOMENTUM": {"wins": 0, "losses": 0, "total_profit": 0.0, "trade_count": 0},
        }

        # Degradation state
        self._degraded = False
        self._recovering = False
        self._recovery_trade_count = 0
        self._consecutive_losses = 0
        self._current_size_factor = 1.0

    @property
    def name(self) -> str:
        return "PerformanceAnalyticsService"

    async def _setup(self):
        self.bus.subscribe(EventTypes.POSITION_CLOSED, self._on_position_closed)

    async def _on_position_closed(self, event: Event):
        """Process a closed position and update analytics."""
        payload = event.payload
        if not payload:
            return

        # Build trade record from position close event
        trade = {
            "symbol": payload.get("symbol", ""),
            "direction": payload.get("direction", ""),
            "session": payload.get("session", ""),
            "regime": payload.get("regime", "UNKNOWN"),
            "entry_price": payload.get("entry_price", 0.0),
            "exit_price": payload.get("exit_price", 0.0),
            "profit": payload.get("pnl", 0.0),
            "r_multiple": payload.get("r_multiple", 0.0),
            "timestamp": payload.get("timestamp", datetime.now(timezone.utc).isoformat()),
        }

        self._trade_history.append(trade)

        # Update consecutive losses
        if trade["profit"] < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        # Update regime stats
        self._update_regime_stats(trade)

        # Recalculate rolling metrics
        self._calculate_rolling_metrics()

        # Check for degradation
        await self._check_degradation()

        # Update recovery tracking
        await self._update_recovery_state(trade)

    def _calculate_rolling_metrics(self, window: Optional[int] = None):
        """
        Calculate rolling metrics over the specified window of trades.
        Computes Sharpe ratio, win rate, and average R:R achieved vs planned.
        """
        win = window if window is not None else self._rolling_window
        trades = list(self._trade_history)
        recent = trades[-win:] if len(trades) >= win else trades

        if not recent:
            return

        total = len(recent)
        wins = sum(1 for t in recent if t["profit"] > 0)
        win_rate = wins / total if total > 0 else 0.0

        # R-multiples for Sharpe calculation
        r_multiples = [t["r_multiple"] for t in recent]
        mean_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0

        # Standard deviation of R-multiples
        if len(r_multiples) > 1:
            variance = sum((r - mean_r) ** 2 for r in r_multiples) / (len(r_multiples) - 1)
            std_r = math.sqrt(variance)
        else:
            std_r = 0.0

        # Sharpe ratio (annualized with sqrt(252))
        sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0

        # Average R:R achieved vs planned
        rr_achieved_list = [t["r_multiple"] for t in recent if t["r_multiple"] != 0]
        avg_rr_achieved = sum(rr_achieved_list) / len(rr_achieved_list) if rr_achieved_list else 0.0

        # Planned R:R from trade data (if available)
        rr_planned_list = [t.get("planned_rr", 0.0) for t in recent if t.get("planned_rr", 0.0) != 0]
        avg_rr_planned = sum(rr_planned_list) / len(rr_planned_list) if rr_planned_list else 0.0

        self._current_metrics = {
            "sharpe_ratio": sharpe,
            "win_rate": win_rate,
            "avg_rr_achieved": avg_rr_achieved,
            "avg_rr_planned": avg_rr_planned,
            "total_trades": total,
        }

    def _update_regime_stats(self, trade: Dict):
        """Group trade by regime and update per-regime statistics."""
        regime = trade.get("regime", "UNKNOWN")
        if regime not in self._regime_performance:
            self._regime_performance[regime] = {
                "wins": 0, "losses": 0, "total_profit": 0.0, "trade_count": 0
            }

        stats = self._regime_performance[regime]
        stats["trade_count"] += 1
        stats["total_profit"] += trade["profit"]

        if trade["profit"] > 0:
            stats["wins"] += 1
        else:
            stats["losses"] += 1

    async def _check_degradation(self):
        """
        Check if strategy performance has degraded.
        Triggers on:
          - Rolling win rate < PERF_MIN_WIN_RATE (35%)
          - Rolling Sharpe < PERF_MIN_SHARPE (-0.5)
          - Consecutive losses >= PERF_CONSECUTIVE_LOSS_THRESHOLD (5)
        """
        total_trades = self._current_metrics.get("total_trades", 0)
        if total_trades < 5:
            # Not enough data to assess degradation
            return

        win_rate = self._current_metrics.get("win_rate", 1.0)
        sharpe = self._current_metrics.get("sharpe_ratio", 0.0)

        degraded_now = (
            win_rate < self._min_win_rate
            or sharpe < self._min_sharpe
            or self._consecutive_losses >= self._consecutive_loss_threshold
        )

        was_degraded = self._degraded

        if degraded_now and not was_degraded:
            # Entering degraded state
            self._degraded = True
            self._recovering = False
            self._recovery_trade_count = 0

            old_factor = self._current_size_factor
            self._current_size_factor = self._degraded_size_factor

            logger.warning(
                f"Strategy DEGRADED: win_rate={win_rate:.2f}, "
                f"sharpe={sharpe:.2f}, consecutive_losses={self._consecutive_losses}"
            )

            await self.emit(EventTypes.PERFORMANCE_ALERT, {
                "status": "DEGRADED",
                "win_rate": win_rate,
                "sharpe_ratio": sharpe,
                "consecutive_losses": self._consecutive_losses,
                "metrics": self._current_metrics.copy(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            if old_factor != self._current_size_factor:
                await self._emit_size_update()

        elif not degraded_now and was_degraded:
            # Transitioning from degraded to recovering
            self._degraded = False
            self._recovering = True
            self._recovery_trade_count = 0

            old_factor = self._current_size_factor
            self._current_size_factor = self._recovery_size_factor

            logger.info(
                f"Strategy RECOVERING: win_rate={win_rate:.2f}, "
                f"sharpe={sharpe:.2f}"
            )

            await self.emit(EventTypes.PERFORMANCE_ALERT, {
                "status": "RECOVERING",
                "win_rate": win_rate,
                "sharpe_ratio": sharpe,
                "consecutive_losses": self._consecutive_losses,
                "metrics": self._current_metrics.copy(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            if old_factor != self._current_size_factor:
                await self._emit_size_update()

    async def _update_recovery_state(self, trade: Dict):
        """Track recovery progress after degradation clears."""
        if self._recovering:
            self._recovery_trade_count += 1
            if self._recovery_trade_count >= 5:
                # Recovery complete - return to normal
                self._recovering = False
                self._recovery_trade_count = 0
                old_factor = self._current_size_factor
                self._current_size_factor = 1.0

                if old_factor != self._current_size_factor:
                    logger.info("Strategy NORMAL: recovery complete, full size restored")
                    await self.emit(EventTypes.PERFORMANCE_ALERT, {
                        "status": "NORMAL",
                        "metrics": self._current_metrics.copy(),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    await self.emit(EventTypes.PERFORMANCE_SIZE_UPDATE, {
                        "size_factor": self._current_size_factor,
                        "state": "NORMAL",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

    async def _emit_size_update(self):
        """Emit a PERFORMANCE_SIZE_UPDATE event with the current factor."""
        state = "NORMAL"
        if self._degraded:
            state = "DEGRADED"
        elif self._recovering:
            state = "RECOVERING"

        await self.emit(EventTypes.PERFORMANCE_SIZE_UPDATE, {
            "size_factor": self._current_size_factor,
            "state": state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def get_performance_size_factor(self) -> float:
        """
        Returns the current position size multiplier based on performance state.
          - 1.0: Normal performance
          - 0.75: Recovering from degradation (first 5 trades after clear)
          - 0.5: Degraded (underperforming)
        """
        return self._current_size_factor

    def get_current_metrics(self) -> Dict:
        """Return the current rolling metrics snapshot."""
        return self._current_metrics.copy()

    def get_regime_performance(self) -> Dict[str, Dict]:
        """Return per-regime performance statistics."""
        result = {}
        for regime, stats in self._regime_performance.items():
            total = stats["trade_count"]
            win_rate = stats["wins"] / total if total > 0 else 0.0
            avg_profit = stats["total_profit"] / total if total > 0 else 0.0
            result[regime] = {
                "win_rate": win_rate,
                "avg_profit": avg_profit,
                "trade_count": total,
                "wins": stats["wins"],
                "losses": stats["losses"],
            }
        return result
