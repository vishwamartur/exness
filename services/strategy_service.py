"""
StrategyService — Event-driven wrapper for deterministic trading strategies.

Subscribes to: MARKET_DATA_READY
Publishes: STRATEGY_SIGNAL

Replaces: Direct strategy initialization and analysis in PairAgent._analyze()
"""

import logging
from datetime import datetime, timezone

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from strategy.bos_strategy import BOSStrategy
from strategy.mean_reversion import MeanReversionStrategy
from utils.async_utils import run_in_executor
from config import settings

logger = logging.getLogger("StrategyService")


class StrategyService(BaseService):
    """
    Evaluates deterministic strategies (BOS, Mean Reversion) when market data arrives.
    Publishes combined strategy signals for the CoordinatorService to use.
    """

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        self.bos = BOSStrategy()
        self.mean_reversion = MeanReversionStrategy()

    @property
    def name(self) -> str:
        return "StrategyService"

    async def _setup(self):
        self.bus.subscribe(EventTypes.MARKET_DATA_READY, self._on_market_data)

    async def _on_market_data(self, event: Event):
        """Run deterministic strategies when new data arrives."""
        symbol = event.payload.get("symbol")
        data_dict = event.payload.get("data_dict")

        if not symbol or not data_dict:
            return

        # Skip if spread is bad
        if not event.payload.get("spread_ok", True):
            return

        try:
            df = data_dict.get(settings.TIMEFRAME)
            if df is None or len(df) < 50:
                return

            tick = event.payload.get("tick")
            current_price = tick["ask"] if tick else df["close"].iloc[-1]

            # 1. BOS Strategy
            bos_signal = await run_in_executor(
                self.bos.analyze, symbol, df, current_price
            )

            # 2. Mean Reversion Strategy
            mr_signal = await run_in_executor(
                self.mean_reversion.analyze, symbol, df, current_price
            )

            # Combine signals
            # Basic fallback: "NEUTRAL"
            direction = "NEUTRAL"
            score = 0
            signals = {}

            if bos_signal and bos_signal.get("signal") != "WAIT":
                direction = bos_signal.get("signal")
                # Scale BOS confidence (0-1) to an additive score (0-4)
                score += int(bos_signal.get("confidence", 0) * 4)
                signals["BOS"] = bos_signal

            if mr_signal:
                mr_dir = mr_signal.get("signal")
                if mr_dir != "WAIT":
                    # If it agrees, add score
                    if direction == "NEUTRAL":
                        direction = mr_dir
                    
                    if direction == mr_dir:
                        score += int(mr_signal.get("score", 0))
                    else:
                        # Conflict - zero out score
                        score = 0
                        direction = "NEUTRAL"
                signals["MR"] = mr_signal

            await self.emit("STRATEGY_SIGNAL", {
                "symbol": symbol,
                "direction": direction,
                "strategy_score": score,
                "signals": signals,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        except Exception as e:
            logger.error(f"[{symbol}] Strategy analysis failed: {e}")
