"""
RegimeService — Wraps MarketAnalyst + HMM Regime detection as an event-driven service.

Subscribes to: MARKET_DATA_READY
Publishes: REGIME_UPDATE

Replaces: Direct analyst.analyze_session() calls from PairAgent._analyze()
"""

import logging
from datetime import datetime, timezone

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from analysis.market_analyst import MarketAnalyst
from utils.async_utils import run_in_executor
from config import settings

logger = logging.getLogger("RegimeService")


class RegimeService(BaseService):
    """
    Event-driven wrapper around MarketAnalyst and HMM Regime Detector.
    Analyzes market regime when new data arrives.
    """

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        self.analyst = MarketAnalyst()
        self._regime_cache = {}  # {symbol: regime_data}

    @property
    def name(self) -> str:
        return "RegimeService"

    async def _setup(self):
        self.bus.subscribe(EventTypes.MARKET_DATA_READY, self._on_market_data)

    async def _on_market_data(self, event: Event):
        """Analyze regime when new data arrives."""
        symbol = event.payload.get("symbol")
        data_dict = event.payload.get("data_dict")

        if not symbol or not data_dict:
            return

        try:
            df = data_dict.get(settings.TIMEFRAME)
            if df is None or len(df) < 50:
                return

            # Session-level regime analysis
            analysis = await run_in_executor(
                self.analyst.analyze_session, symbol, df
            )
            regime = analysis.get("regime", "UNKNOWN")

            # Detailed HMM regime if enabled
            regime_type = regime
            regime_score = 5
            is_tradeable = True
            regime_details = {}

            if getattr(settings, 'USE_HMM_REGIME', False):
                try:
                    detector = self.analyst.get_regime_detector(symbol)
                    regime_type, regime_details = await run_in_executor(
                        detector.get_regime, df
                    )
                    is_tradeable = detector.is_tradeable_regime(regime_type)

                    # Score the regime (neutral direction for now; PairAgent refines)
                    regime_score, regime_reason = detector.get_regime_score(
                        regime_type, "NEUTRAL"
                    )
                except Exception as e:
                    logger.debug(f"[{symbol}] HMM regime error: {e}")

            # Cache
            self._regime_cache[symbol] = {
                "regime": regime,
                "regime_type": regime_type,
                "regime_score": regime_score,
                "is_tradeable": is_tradeable,
            }

            # Publish
            await self.emit(EventTypes.REGIME_UPDATE, {
                "symbol": symbol,
                "regime": regime,
                "regime_type": regime_type,
                "regime_score": regime_score,
                "is_tradeable": is_tradeable,
                "regime_details": str(regime_details),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        except Exception as e:
            logger.error(f"[{symbol}] Regime analysis failed: {e}")

    def get_regime(self, symbol: str) -> dict:
        """Get cached regime for a symbol."""
        return self._regime_cache.get(symbol, {
            "regime": "UNKNOWN",
            "regime_type": "UNKNOWN",
            "regime_score": 5,
            "is_tradeable": True,
        })
