"""
TelegramService — Event-driven Telegram notification service.

Subscribes to: TRADE_EXECUTED, TRADE_FAILED, POSITION_CLOSED, SERVICE_ERROR
Publishes: Nothing (terminal output service)

Replaces: Direct _tg().trade_executed() calls from InstitutionalStrategy
"""

import logging
from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService

logger = logging.getLogger("TelegramService")


class TelegramService(BaseService):
    """
    Sends Telegram notifications on trade events.
    Wraps the existing TelegramNotifier.
    """

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        self._notifier = None

    @property
    def name(self) -> str:
        return "TelegramService"

    async def _setup(self):
        try:
            from utils.telegram_notifier import get_notifier
            self._notifier = get_notifier()
        except Exception as e:
            logger.warning(f"Telegram notifier init failed: {e}")

        self.bus.subscribe(EventTypes.TRADE_EXECUTED, self._on_trade_executed)
        self.bus.subscribe(EventTypes.TRADE_FAILED, self._on_trade_failed)
        self.bus.subscribe(EventTypes.POSITION_CLOSED, self._on_position_closed)
        self.bus.subscribe(EventTypes.SERVICE_ERROR, self._on_service_error)

    async def _on_trade_executed(self, event: Event):
        """Notify on successful trade execution."""
        if not self._notifier:
            return

        p = event.payload
        try:
            self._notifier.trade_executed(
                p.get("symbol", "UNKNOWN"),
                p.get("direction", "UNKNOWN"),
                p.get("lot", 0),
                p.get("price", 0),
                p.get("sl", 0),
                p.get("tp", 0),
            )
        except Exception as e:
            logger.debug(f"Telegram send failed: {e}")

    async def _on_trade_failed(self, event: Event):
        """Notify on trade failure."""
        if not self._notifier:
            return

        p = event.payload
        try:
            self._notifier.error(
                f"❌ Trade Failed: {p.get('symbol', 'UNKNOWN')} "
                f"{p.get('direction', '')} — {p.get('reason', 'Unknown')}"
            )
        except Exception:
            pass

    async def _on_position_closed(self, event: Event):
        """Notify on position close."""
        if not self._notifier:
            return

        p = event.payload
        try:
            pnl = p.get("pnl", 0)
            emoji = "✅" if pnl >= 0 else "🔴"
            self._notifier.info(
                f"{emoji} Position Closed: #{p.get('ticket', '?')} "
                f"{p.get('symbol', 'UNKNOWN')} | P&L: ${pnl:.2f} | {p.get('reason', '')}"
            )
        except Exception:
            pass

    async def _on_service_error(self, event: Event):
        """Notify on critical service errors."""
        if not self._notifier:
            return

        p = event.payload
        try:
            self._notifier.error(
                f"⚠️ Service Error: {p.get('service', 'Unknown')} — {p.get('error', '')}"
            )
        except Exception:
            pass
