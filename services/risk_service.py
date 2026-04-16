"""
RiskService — Wraps the existing RiskManager as an event-driven service.

Subscribes to: TRADE_CANDIDATE
Publishes: TRADE_APPROVED or TRADE_REJECTED

Replaces: Direct risk_manager.check_execution() and calculate_position_size()
calls from InstitutionalStrategy._execute_trade()
"""

import logging
from datetime import datetime, timezone

import MetaTrader5 as mt5

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from core.mt5_gateway import MT5Gateway
from utils.risk_manager import RiskManager
from utils.pre_trade_analyzer import PreTradeAnalyzer
from utils.correlation_filter import check_correlation_conflict
from config import settings

logger = logging.getLogger("RiskService")


class RiskService(BaseService):
    """
    Event-driven risk gatekeeper.
    Evaluates trade candidates and approves/rejects them with position sizing.
    """

    def __init__(self, event_bus: EventBus, gateway: MT5Gateway,
                 risk_manager: RiskManager = None):
        super().__init__(event_bus)
        self.gateway = gateway
        # Reuse existing RiskManager or create new
        self.risk_manager = risk_manager
        self._pre_trade_analyzer = None

    @property
    def name(self) -> str:
        return "RiskService"

    async def _setup(self):
        # Lazy init risk manager (needs MT5 client wrapper)
        if self.risk_manager is None:
            # Create a thin adapter so RiskManager can call client methods
            from execution.mt5_client import MT5Client
            client = MT5Client()
            self.risk_manager = RiskManager(client)

        # Pre-trade analyzer wraps quant + analyst agents
        try:
            from analysis.quant_agent import QuantAgent
            from analysis.market_analyst import MarketAnalyst
            quant = QuantAgent()
            analyst = MarketAnalyst()
            self._pre_trade_analyzer = PreTradeAnalyzer(quant, analyst)
        except Exception as e:
            logger.warning(f"PreTradeAnalyzer init failed: {e}")

        # Subscribe to Gemma-vetted candidates (post AI brain check)
        # Falls back to raw TRADE_CANDIDATE if GemmaBrainService is not running
        from services.gemma_brain_service import GEMMA_VETTED
        self.bus.subscribe(GEMMA_VETTED, self._on_trade_candidate)
        # Also listen to raw candidate as fallback (in case Gemma service not started)
        self.bus.subscribe(EventTypes.TRADE_CANDIDATE, self._on_raw_candidate)

        self._gemma_brain_active = False  # Set True once GemmaBrainService starts

    def set_gemma_brain_active(self, active: bool):
        """Called by GemmaBrainService to signal it is handling vetting."""
        self._gemma_brain_active = active

    async def _on_raw_candidate(self, event: Event):
        """
        Fallback: process raw TRADE_CANDIDATE only if GemmaBrainService is NOT active.
        If Gemma brain is active, it re-emits as TRADE_CANDIDATE_VETTED. Double processing
        would be wrong — so we skip here when Gemma is online.
        """
        if self._gemma_brain_active:
            return  # Gemma service will emit TRADE_CANDIDATE_VETTED instead
        # Fallback — pass directly to full risk evaluation
        await self._on_trade_candidate(event)

    async def _on_trade_candidate(self, event: Event):
        """Evaluate a trade candidate through all risk checks."""

        candidate = event.payload
        symbol = candidate.get("symbol")
        direction = candidate.get("direction")

        if not symbol or not direction:
            await self.emit(EventTypes.TRADE_REJECTED, {
                "symbol": symbol or "UNKNOWN",
                "reason": "Missing symbol or direction in candidate",
            })
            return

        try:
            # 1. Pre-trade analysis
            if self._pre_trade_analyzer:
                analysis = self._pre_trade_analyzer.analyze_entry_opportunity(
                    symbol, direction
                )
                if not analysis.get("should_enter", True):
                    await self.emit(EventTypes.TRADE_REJECTED, {
                        "symbol": symbol,
                        "direction": direction,
                        "reason": f"Pre-trade: {analysis.get('recommendation', 'Blocked')}",
                        "confidence": analysis.get("confidence_score", 0),
                    })
                    return

            # 2. Direction guard
            if direction not in ("BUY", "SELL"):
                await self.emit(EventTypes.TRADE_REJECTED, {
                    "symbol": symbol,
                    "reason": f"Invalid direction: {direction}",
                })
                return

            # 3. Correlation filter
            try:
                positions = await self.gateway.get_all_positions()
                has_conflict, conflict_reason = check_correlation_conflict(
                    symbol, direction, positions
                )
                if has_conflict:
                    await self.emit(EventTypes.TRADE_REJECTED, {
                        "symbol": symbol,
                        "direction": direction,
                        "reason": f"Correlation: {conflict_reason}",
                    })
                    return
            except Exception as e:
                logger.debug(f"Correlation check error (non-blocking): {e}")

            # 4. Symbol tradeable check
            sym_info = await self.gateway.get_symbol_info(symbol)
            if sym_info is None or sym_info.trade_mode == 0:
                await self.emit(EventTypes.TRADE_REJECTED, {
                    "symbol": symbol,
                    "reason": "Symbol disabled/not tradeable",
                })
                return

            # 5. R:R mandate
            sl_dist = candidate.get("sl_distance", 0)
            tp_dist = candidate.get("tp_distance", 0)
            if sl_dist <= 0:
                await self.emit(EventTypes.TRADE_REJECTED, {
                    "symbol": symbol,
                    "reason": "Invalid SL distance",
                })
                return

            if getattr(settings, "MANDATE_MIN_RR", False):
                rr_ratio = tp_dist / sl_dist
                if rr_ratio < settings.MIN_RISK_REWARD_RATIO:
                    await self.emit(EventTypes.TRADE_REJECTED, {
                        "symbol": symbol,
                        "direction": direction,
                        "reason": f"R:R {rr_ratio:.2f} < {settings.MIN_RISK_REWARD_RATIO}",
                    })
                    return

            # 6. Execution risk check
            tick = await self.gateway.get_tick(symbol)
            if not tick:
                await self.emit(EventTypes.TRADE_REJECTED, {
                    "symbol": symbol,
                    "reason": "No tick data",
                })
                return

            if direction == "BUY":
                sl = tick.ask - sl_dist
                tp = tick.ask + tp_dist
            else:
                sl = tick.bid + sl_dist
                tp = tick.bid - tp_dist

            positions = await self.gateway.get_all_positions()
            allowed, reason = self.risk_manager.check_execution(
                symbol, direction, sl, tp, positions
            )

            if not allowed:
                await self.emit(EventTypes.TRADE_REJECTED, {
                    "symbol": symbol,
                    "direction": direction,
                    "reason": f"Execution: {reason}",
                })
                return

            # 7. Position sizing
            score = candidate.get("score", 0)
            lot = self.risk_manager.calculate_position_size(
                symbol, sl_dist, score,
                scaling_factor=candidate.get("scaling_factor", 1.0),
                ml_prob=candidate.get("ml_prob"),
                emotion_state=candidate.get("emotion_state", "NEUTRAL"),
                emotion_score=candidate.get("emotion_score", 0.5),
            )

            # 8. APPROVED — build the approved trade
            approved = {
                **candidate,
                "lot": lot,
                "sl": sl,
                "tp": tp,
                "entry_price": tick.ask if direction == "BUY" else tick.bid,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            logger.info(f"[{symbol}] APPROVED {direction} | Lot: {lot} | SL: {sl:.5f} | TP: {tp:.5f}")
            await self.emit(EventTypes.TRADE_APPROVED, approved)

            # Record in risk manager
            self.risk_manager.record_trade(symbol)

        except Exception as e:
            logger.error(f"[{symbol}] Risk evaluation error: {e}")
            import traceback
            traceback.print_exc()
            await self.emit(EventTypes.TRADE_REJECTED, {
                "symbol": symbol,
                "direction": direction,
                "reason": f"Risk error: {str(e)}",
            })
