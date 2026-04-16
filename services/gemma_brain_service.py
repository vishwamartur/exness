"""
GemmaBrainService — Gemma 4 as the AI trader brain.

Sits between CoordinatorService and RiskService in the event pipeline:

  TRADE_CANDIDATE → [GemmaBrainService] → TRADE_CANDIDATE (enriched)
                                        → TRADE_REJECTED (if HOLD/low conf)

The service intercepts every TRADE_CANDIDATE, asks Gemma 4 for its verdict,
then either:
  - Approves & enriches the candidate (adds gemma_decision, gemma_confidence)
  - Blocks the trade (emits TRADE_REJECTED with Gemma's reasoning)

Architecture note: We re-emit TRADE_CANDIDATE on a new sub-topic "TRADE_CANDIDATE_VETTED"
so RiskService subscribes to that instead. Fallback: if Gemma is unavailable,
the candidate passes through unchanged.
"""

import asyncio
import logging
from datetime import datetime, timezone

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from core.mt5_gateway import MT5Gateway
from utils.async_utils import run_in_executor
from config import settings

logger = logging.getLogger("GemmaBrainService")

GEMMA_VETTED = "TRADE_CANDIDATE_VETTED"   # New event type after Gemma vetting


class GemmaBrainService(BaseService):
    """
    Gemma 4 acts as the AI trader brain.
    Validates every trade candidate before it reaches RiskService.
    """

    def __init__(self, event_bus: EventBus, gateway: MT5Gateway):
        super().__init__(event_bus)
        self.gateway = gateway
        self._brain = None
        self._enabled = False
        self._decisions: dict = {}   # symbol → last decision (for logging)

        # Config
        self._min_confidence = int(getattr(settings, 'GEMMA_MIN_CONFIDENCE', 55))
        self._boost_threshold = int(getattr(settings, 'GEMMA_BOOST_THRESHOLD', 75))

    @property
    def name(self) -> str:
        return "GemmaBrainService"

    async def _setup(self):
        """Initialize Gemma 4 brain."""
        try:
            from analysis.gemma4_brain import get_gemma4_brain
            self._brain = get_gemma4_brain()
            self._enabled = self._brain.is_available()
        except Exception as e:
            logger.error(f"Gemma 4 brain init failed: {e}")
            self._enabled = False

        if self._enabled:
            print(f"\n{'='*60}")
            print(f"  🧠 GEMMA 4 BRAIN ACTIVE")
            print(f"  Min confidence to trade: {self._min_confidence}%")
            print(f"  Score boost threshold:   {self._boost_threshold}%")
            print(f"{'='*60}\n")
        else:
            logger.warning("[GemmaBrainService] Gemma 4 unavailable — candidates pass-through")

        # Subscribe to TRADE_CANDIDATE events
        self.bus.subscribe(EventTypes.TRADE_CANDIDATE, self._on_trade_candidate)

    async def _on_trade_candidate(self, event: Event):
        """
        Intercept a trade candidate and run it through Gemma 4.
        Re-emit as TRADE_CANDIDATE_VETTED if approved, or TRADE_REJECTED if blocked.
        """
        candidate = event.payload
        symbol = candidate.get("symbol", "?")
        direction = candidate.get("direction", "NEUTRAL")
        score = candidate.get("score", 0)
        ml_prob = candidate.get("ml_prob", 0.5)
        regime_type = candidate.get("regime_type", "UNKNOWN")
        features = candidate.get("features", {})

        # If Gemma disabled — pass straight through as vetted
        if not self._enabled:
            await self.emit(GEMMA_VETTED, {**candidate, "gemma_decision": direction,
                                           "gemma_confidence": 60, "gemma_reason": "N/A"})
            return

        # Get open positions for context
        try:
            positions = await self.gateway.get_all_positions()
        except Exception:
            positions = []

        # Build news context string from candidate details
        news_ctx = candidate.get("details", {}).get("news", "")
        if not news_ctx:
            news_ctx = f"Regime: {regime_type}"

        print(f"\n🧠 [GEMMA 4] Analyzing {symbol} {direction} (score={score}, ml={ml_prob:.0%})...")

        try:
            # Run in thread — Gemma call is blocking (HTTP)
            decision, confidence, reasoning = await run_in_executor(
                self._brain.analyze_trade_candidate,
                symbol,
                direction,
                features,
                regime_type,
                score,
                ml_prob,
                positions,
                news_ctx,
                settings.TIMEFRAME,
            )
        except Exception as e:
            logger.error(f"[{symbol}] Gemma 4 analysis error: {e}")
            # On error — pass through (don't block trading due to AI error)
            await self.emit(GEMMA_VETTED, {**candidate,
                                           "gemma_decision": direction,
                                           "gemma_confidence": 60,
                                           "gemma_reason": f"Error: {e}"})
            return

        # Store for dashboard
        self._decisions[symbol] = {
            "decision": decision,
            "confidence": confidence,
            "reasoning": reasoning,
            "direction": direction,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # ── Decision Logic ─────────────────────────────────────────────

        # 1. Gemma says HOLD → reject
        if decision == "HOLD":
            print(f"🧠 [GEMMA 4] ❌ {symbol} REJECTED — HOLD ({confidence}%) | {reasoning}")
            await self.emit(EventTypes.TRADE_REJECTED, {
                "symbol": symbol,
                "direction": direction,
                "reason": f"Gemma 4: {reasoning} ({confidence}%)",
                "gemma_decision": "HOLD",
                "gemma_confidence": confidence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return

        # 2. Gemma says opposite direction → reject
        if decision != direction and decision in ("BUY", "SELL"):
            print(f"🧠 [GEMMA 4] ❌ {symbol} DIRECTION FLIP — Gemma says {decision}, quant says {direction} | {reasoning}")
            await self.emit(EventTypes.TRADE_REJECTED, {
                "symbol": symbol,
                "direction": direction,
                "reason": f"Gemma 4 direction conflict: suggests {decision} | {reasoning}",
                "gemma_decision": decision,
                "gemma_confidence": confidence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return

        # 3. Low confidence → reject
        if confidence < self._min_confidence:
            print(f"🧠 [GEMMA 4] ❌ {symbol} LOW CONFIDENCE ({confidence}% < {self._min_confidence}%) | {reasoning}")
            await self.emit(EventTypes.TRADE_REJECTED, {
                "symbol": symbol,
                "direction": direction,
                "reason": f"Gemma 4 low confidence: {confidence}% | {reasoning}",
                "gemma_decision": decision,
                "gemma_confidence": confidence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return

        # 4. High confidence → boost score
        score_boost = 0
        if confidence >= self._boost_threshold:
            score_boost = 2
            print(f"🧠 [GEMMA 4] ✅ {symbol} {decision} APPROVED + SCORE BOOST +{score_boost} ({confidence}%) | {reasoning}")
        else:
            print(f"🧠 [GEMMA 4] ✅ {symbol} {decision} APPROVED ({confidence}%) | {reasoning}")

        # Enrich candidate with Gemma data and forward as vetted
        enriched = {
            **candidate,
            "score": score + score_boost,
            "gemma_decision": decision,
            "gemma_confidence": confidence,
            "gemma_reason": reasoning,
            "gemma_boosted": score_boost > 0,
        }
        await self.emit(GEMMA_VETTED, enriched)

    def get_last_decision(self, symbol: str) -> dict:
        """Get the last Gemma 4 decision for a symbol."""
        return self._decisions.get(symbol, {})
