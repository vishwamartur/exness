"""
FlowService — Event-driven wrapper for institutional flow detection.

Subscribes to: MARKET_DATA_READY
Publishes: FLOW_UPDATE

Replaces: Direct InstitutionalFlowDetector calls in PairAgent
"""

import logging
from datetime import datetime, timezone

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from analysis.institutional_flow_detector import get_institutional_flow_detector
from utils.async_utils import run_in_executor
from config import settings

logger = logging.getLogger("FlowService")


class FlowService(BaseService):
    """
    Evaluates Smart Money / Institutional Order Flow footprint.
    Publishes FLOW_UPDATE for the CoordinatorService.
    """

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        self.flow_detector = None

    @property
    def name(self) -> str:
        return "FlowService"

    async def _setup(self):
        try:
            self.flow_detector = get_institutional_flow_detector()
            self.bus.subscribe(EventTypes.MARKET_DATA_READY, self._on_market_data)
        except Exception as e:
            logger.warning(f"Flow detector failed to initialize: {e}")

    async def _on_market_data(self, event: Event):
        """Run flow detection when new data arrives."""
        if not self.flow_detector:
            return

        symbol = event.payload.get("symbol")
        data_dict = event.payload.get("data_dict")

        if not symbol or not data_dict:
            return

        try:
            df = data_dict.get(settings.TIMEFRAME)
            if df is None or len(df) < 50:
                return

            tick = event.payload.get("tick")
            current_price = tick["ask"] if tick else df["close"].iloc[-1]

            # Analyze Flow
            flow_analysis = await run_in_executor(
                self.flow_detector.analyze, symbol, data_dict
            )

            # Extract specifics
            direction = flow_analysis.get("direction", "NEUTRAL")
            strength_score = flow_analysis.get("score", 0.0)

            # Base format: 0 to 1 scaling, adapt to system's integer scoring if needed.
            # Convert 0-100 score to roughly 0-5 scale
            scale_score = min(max(int(strength_score / 20), -5), 5)

            await self.emit("FLOW_UPDATE", {
                "symbol": symbol,
                "flow_direction": direction,
                "flow_score": scale_score,
                "details": flow_analysis,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        except Exception as e:
            logger.error(f"[{symbol}] Flow analysis failed: {e}")
