"""
PerformanceService — Event-driven wrapper for circuit breakers and self-correction.

Subscribes to: POSITION_CLOSED, SCAN_START
Publishes: CIRCUIT_BREAKER_UPDATE

Replaces: PairAgent.consecutive_losses state tracking & _load_state() checks.
"""

import logging
from datetime import datetime, timezone
from typing import Dict

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from utils.trade_journal import TradeJournal
from config import settings

logger = logging.getLogger("PerformanceService")


class PerformanceService(BaseService):
    """
    Monitors trading performance per symbol.
    If consecutive losses hit the limit, it trips the circuit breaker
    by publishing a CIRCUIT_BREAKER_UPDATE for the Coordinator to catch.
    """

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        self.journal = TradeJournal()
        self._consecutive_losses: Dict[str, int] = {}
        self._tripped: Dict[str, bool] = {}
        self.max_losses = int(getattr(settings, 'CONSECUTIVE_LOSS_LIMIT', 2))

    @property
    def name(self) -> str:
        return "PerformanceService"

    async def _setup(self):
        # Initial scan to load state
        self._load_initial_state()
        
        self.bus.subscribe(EventTypes.POSITION_CLOSED, self._on_position_closed)
        self.bus.subscribe(EventTypes.SCAN_START, self._on_scan_start)

    def _load_initial_state(self):
        """Loads state from the trade journal on startup."""
        for symbol in settings.SYMBOLS:
            trades = self.journal.get_recent_trades(symbol, limit=10)
            losses = 0
            for t in trades:
                if t.get('outcome') == 'LOSS':
                    losses += 1
                else:
                    break
            self._consecutive_losses[symbol] = losses
            
            if losses >= self.max_losses:
                self._tripped[symbol] = True
                logger.warning(f"[{symbol}] Circuit Breaker restored in TRIPPED state ({losses} losses)")
            else:
                self._tripped[symbol] = False

    async def _on_scan_start(self, event: Event):
        """Publish circuit breaker states at the start of a cycle."""
        for symbol, tripped in self._tripped.items():
            if tripped:
                await self.emit("CIRCUIT_BREAKER_UPDATE", {
                    "symbol": symbol,
                    "tripped": True,
                    "consecutive_losses": self._consecutive_losses.get(symbol, 0),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    async def _on_position_closed(self, event: Event):
        """Track outcome of closed positions."""
        symbol = event.payload.get("symbol")
        pnl = event.payload.get("pnl", 0)
        
        if not symbol:
            return

        outcome = "LOSS" if pnl < 0 else "WIN"
        
        # We also need to map Early Cut or Breakeven. Small losses might count.
        # But we align with TradeJournal's basic determination -> pnl < 0
        current_losses = self._consecutive_losses.get(symbol, 0)

        if outcome == "LOSS":
            current_losses += 1
        else:
            # Win or Breakeven resets the loss streak
            current_losses = 0
            
        self._consecutive_losses[symbol] = current_losses

        # Check threshold
        tripped_now = current_losses >= self.max_losses
        was_tripped = self._tripped.get(symbol, False)

        self._tripped[symbol] = tripped_now

        if tripped_now and not was_tripped:
            logger.error(f"[{symbol}] CIRCUIT BREAKER TRIPPED! ({current_losses} consecutive losses)")
            await self.emit("CIRCUIT_BREAKER_UPDATE", {
                "symbol": symbol,
                "tripped": True,
                "consecutive_losses": current_losses,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        elif not tripped_now and was_tripped:
            logger.info(f"[{symbol}] Circuit Breaker RESET.")
            await self.emit("CIRCUIT_BREAKER_UPDATE", {
                "symbol": symbol,
                "tripped": False,
                "consecutive_losses": current_losses,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
