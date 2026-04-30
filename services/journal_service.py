"""
JournalService — Event-driven trade journaling.

Subscribes to: TRADE_EXECUTED
Publishes: Nothing (storage service)

Replaces: Direct journal.log_entry() calls from InstitutionalStrategy
"""

import logging
from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from utils.trade_journal import TradeJournal

logger = logging.getLogger("JournalService")


class JournalService(BaseService):

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        self.journal = TradeJournal()

    @property
    def name(self) -> str:
        return "JournalService"

    async def _setup(self):
        self.bus.subscribe(EventTypes.TRADE_EXECUTED, self._on_trade_executed)
        self.bus.subscribe(EventTypes.POSITION_CLOSED, self._on_position_closed)

    async def _on_position_closed(self, event: Event):
        p = event.payload
        try:
            ticket = p.get("ticket")
            exit_price = p.get("exit_price", 0.0)
            profit = p.get("profit", 0.0)
            
            if ticket:
                self.journal.log_exit(ticket, exit_price, profit)
                logger.info(f"[{p.get('symbol', 'UNKNOWN')}] Trade exit logged: #{ticket} (Profit: ${profit:.2f})")
        except Exception as e:
            logger.error(f"Journal exit log failed: {e}")

    async def _on_trade_executed(self, event: Event):
        p = event.payload
        try:
            symbol = p.get("symbol", "")
            from config import settings
            if symbol in getattr(settings, "SYMBOLS_CRYPTO", []):
                asset_class = "crypto"
            elif symbol in getattr(settings, "SYMBOLS_COMMODITIES", []):
                asset_class = "commodity"
            else:
                asset_class = "forex"

            self.journal.log_entry(
                ticket=p.get("ticket", 0),
                symbol=symbol,
                direction=p.get("direction", "UNKNOWN"),
                lot_size=p.get("lot", 0),
                entry_price=p.get("price", 0),
                sl_price=p.get("sl", 0),
                tp_price=p.get("tp", 0),
                confluence_score=p.get("score", 0),
                confluence_details=p.get("details", {}),
                rf_probability=p.get("ml_prob", 0.5),
                ai_signal=0,
                asset_class=asset_class,
                session=p.get("session", "unknown"),
                researcher_action="NONE",
                researcher_confidence=0,
                researcher_reason="N/A",
            )
            logger.info(f"[{symbol}] Trade logged: #{p.get('ticket', '?')}")
        except Exception as e:
            logger.error(f"Journal log failed: {e}")
