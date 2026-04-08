"""
MT5 Trading Bot — Event-Driven Architecture (v3.0)

All services communicate through an async EventBus.
No service calls another service directly.

Event Flow:
  SCAN_START → MARKET_DATA_READY → QUANT_SIGNAL → TRADE_CANDIDATE
  → TRADE_APPROVED → TRADE_EXECUTED → (Journal, Telegram, Dashboard)
"""

import asyncio
import os
import sys
import logging
import traceback
from datetime import datetime

# Windows stdout fix
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Add project root to path
sys.path.append(os.path.dirname(__file__))

# ─── Logging Setup ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-20s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("Main")

# ─── Imports ──────────────────────────────────────────────────────────────
from config import settings
from core.event_bus import EventBus
from core.mt5_gateway import MT5Gateway

from services.market_data_service import MarketDataService
from services.quant_service import QuantService
from services.regime_service import RegimeService
from services.sentiment_service import SentimentService
from services.risk_service import RiskService
from services.execution_service import ExecutionService
from services.trade_manager_service import TradeManagerService
from services.broadcast_service import BroadcastService
from services.telegram_service import TelegramService
from services.journal_service import JournalService
from services.coordinator import CoordinatorService

from services.strategy_service import StrategyService
from services.flow_service import FlowService
from services.performance_service import PerformanceService


async def main():
    print("=" * 60)
    print("  MT5 EVENT-DRIVEN ARCHITECTURE v3.0")
    print(f"  Start Time: {datetime.now()}")
    print("=" * 60)

    # ── 1. Core Infrastructure ────────────────────────────────────────
    event_log_dir = os.path.join(os.path.dirname(__file__), "event_logs")
    bus = EventBus(log_dir=event_log_dir)
    gateway = MT5Gateway()

    # ── 2. Connect to MT5 ─────────────────────────────────────────────
    if not await gateway.connect():
        print("Failed to connect to MT5. Exiting.")
        return

    if not await gateway.detect_available_symbols():
        print("Failed to detect symbols. Exiting.")
        return

    print(f"\n[SYSTEM] Trading {len(settings.SYMBOLS)} symbols: "
          f"{', '.join(settings.SYMBOLS[:5])}{'...' if len(settings.SYMBOLS) > 5 else ''}")

    # ── 3. Launch Dashboard ───────────────────────────────────────────
    import subprocess, pathlib, webbrowser
    dashboard_dir = pathlib.Path(__file__).parent / "dashboard"
    if dashboard_dir.exists():
        try:
            subprocess.Popen(
                "npm run dev",
                cwd=str(dashboard_dir),
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print("[DASHBOARD] Vite dev server starting at http://localhost:5173")
            await asyncio.sleep(3)
            webbrowser.open("http://localhost:5173")
        except Exception as e:
            print(f"[DASHBOARD] Could not launch: {e}")

    # ── 4. Create Services ────────────────────────────────────────────
    # Shared RiskManager instance (services that need it share state)
    from execution.mt5_client import MT5Client
    from utils.risk_manager import RiskManager
    mt5_client = MT5Client()
    risk_manager = RiskManager(mt5_client)

    services = [
        # Data layer
        MarketDataService(bus, gateway),

        # Analysis layer
        QuantService(bus),
        RegimeService(bus),
        SentimentService(bus),
        StrategyService(bus),
        FlowService(bus),

        # Decision layer
        RiskService(bus, gateway, risk_manager=risk_manager),
        PerformanceService(bus),

        # Execution layer
        ExecutionService(bus, gateway),
        TradeManagerService(bus, gateway, risk_manager=risk_manager),

        # Output layer
        BroadcastService(bus),
        TelegramService(bus),
        JournalService(bus),

        # Orchestrator (must be last — triggers SCAN_START)
        CoordinatorService(bus, gateway),
    ]

    # ── 5. Start EventBus ─────────────────────────────────────────────
    await bus.start()

    # ── 6. Start All Services ─────────────────────────────────────────
    print(f"\n[SYSTEM] Starting {len(services)} services...")
    for svc in services:
        try:
            await svc.start()
        except Exception as e:
            logger.error(f"Failed to start {svc.name}: {e}")
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"  ALL SERVICES RUNNING — {len(services)} active")
    print(f"  EventBus: subscribers={bus.subscriber_count}")
    print(f"  Interval: {settings.COOLDOWN_SECONDS}s")
    print(f"{'='*60}\n")

    # ── 7. Run Until Interrupted ──────────────────────────────────────
    try:
        # The CoordinatorService._run_loop() drives the scan cycles
        # We just need to keep the main coroutine alive
        while True:
            await asyncio.sleep(60)

            # Periodic health check
            for svc in services:
                health = await svc.health()
                if not health.get("loop_alive") and health.get("running"):
                    logger.warning(f"[HEALTH] {svc.name} loop died!")

    except KeyboardInterrupt:
        print("\n[SYSTEM] Shutting down...")

    # ── 8. Graceful Shutdown ──────────────────────────────────────────
    print("[SYSTEM] Stopping services...")
    for svc in reversed(services):
        try:
            await svc.stop()
        except Exception as e:
            logger.error(f"Error stopping {svc.name}: {e}")

    await bus.stop()
    await gateway.shutdown()

    print(f"[SYSTEM] Shutdown complete. Events: {bus.event_count}, "
          f"Errors: {bus.error_count}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
