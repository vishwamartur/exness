"""
QuantService — Wraps the existing QuantAgent to provide ML analysis
as an event-driven service.

Subscribes to: MARKET_DATA_READY
Publishes: QUANT_SIGNAL

Replaces: Direct QuantAgent.analyze() calls from PairAgent._analyze()
"""

import logging
from datetime import datetime, timezone

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from analysis.quant_agent import QuantAgent
from utils.async_utils import run_in_executor

logger = logging.getLogger("QuantService")


class QuantService(BaseService):
    """
    Event-driven wrapper around QuantAgent.
    Performs ML inference and technical analysis when market data arrives.
    """

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        self.quant = QuantAgent()

    @property
    def name(self) -> str:
        return "QuantService"

    async def _setup(self):
        self.bus.subscribe(EventTypes.MARKET_DATA_READY, self._on_market_data)

    async def _on_market_data(self, event: Event):
        """Run quant analysis when new data arrives for a symbol."""
        symbol = event.payload.get("symbol")
        data_dict = event.payload.get("data_dict")

        if not symbol or not data_dict:
            return

        # Skip if spread is bad (analysis would be wasted)
        if not event.payload.get("spread_ok", True):
            return

        try:
            # Run analysis in executor (it's CPU-bound)
            q_res = await run_in_executor(self.quant.analyze, symbol, data_dict)

            if not q_res:
                return

            # Extract safe-to-serialize features
            features_dict = {}
            if 'features' in q_res:
                feat = q_res['features']
                # Convert pandas Series to dict if needed
                if hasattr(feat, 'to_dict'):
                    features_dict = {
                        k: float(v) if hasattr(v, 'item') else v
                        for k, v in feat.to_dict().items()
                        if isinstance(v, (int, float)) or (hasattr(v, 'item'))
                    }
                elif isinstance(feat, dict):
                    features_dict = feat

            await self.emit(EventTypes.QUANT_SIGNAL, {
                "symbol": symbol,
                "direction": q_res.get("direction", "NEUTRAL"),
                "score": q_res.get("score", 0),
                "ml_prob": q_res.get("ml_prob", 0.5),
                "ensemble_score": q_res.get("ensemble_score", 0),
                "agreement_count": q_res.get("agreement_count", 0),
                "model_votes": q_res.get("model_votes", {}),
                "h4_trend": q_res.get("h4_trend", 0),
                "m5_trend": q_res.get("m5_trend", 0),
                "details": q_res.get("details", {}),
                "features": features_dict,
                "data_dict": data_dict,  # Pass through for downstream
                "quant_result": q_res,   # Full result for PairAgent
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        except Exception as e:
            logger.error(f"[{symbol}] Quant analysis failed: {e}")
            import traceback
            traceback.print_exc()
