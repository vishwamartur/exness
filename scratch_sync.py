import asyncio
from core.event_bus import EventBus, Event, EventTypes
from core.mt5_gateway import MT5Gateway
from services.trade_manager_service import TradeManagerService
from services.journal_service import JournalService

async def main():
    bus = EventBus()
    gw = MT5Gateway()
    await gw.connect()
    js = JournalService(bus)
    tms = TradeManagerService(bus, gw)
    await js._setup()
    await tms._setup()
    print("Running sync...")
    await tms._on_scan_start(Event(EventTypes.SCAN_START, {}))
    await asyncio.sleep(2)
    print("Sync complete.")
    await gw.shutdown()

asyncio.run(main())
