"""
RiskService — Hardened risk gatekeeper for XAUUSD session regime engine.

Hard rules coded into the algo:
  - Max effective leverage: 1:20 (code this yourself; ignore broker setting)
  - Risk per trade: 1% of equity maximum
  - Daily loss limit: 3% — algo shuts down until next day
  - Spread filter: If XAUUSD spread > 30 pips (0.30), do not enter
  - Kill switch: After 3 consecutive losses, reduce position size by 50% for 24h

Subscribes to: TRADE_CANDIDATE, TRADE_EXECUTED, POSITION_CLOSED
Publishes: TRADE_APPROVED, TRADE_REJECTED, DAILY_LOSS_LIMIT_HIT, KILL_SWITCH_ACTIVATED
"""

import logging
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from core.mt5_gateway import MT5Gateway
from utils.risk_manager import RiskManager
from utils.correlation_filter import check_correlation_conflict
from config import settings

logger = logging.getLogger("RiskService")


class RiskService(BaseService):
    """
    Hardened risk gatekeeper with leverage caps, daily loss limits,
    spread filters, and kill switch for consecutive losses.
    """

    def __init__(self, event_bus: EventBus, gateway: MT5Gateway,
                 risk_manager: RiskManager = None):
        super().__init__(event_bus)
        self.gateway = gateway
        self.risk_manager = risk_manager

        # Daily P&L tracking
        self._daily_start_equity: float = 0.0
        self._daily_loss_pct: float = 0.0
        self._daily_shutdown: bool = False
        self._daily_date: str = ""

        # Kill switch: consecutive loss tracking
        self._consecutive_losses: int = 0
        self._kill_switch_active: bool = False
        self._kill_switch_until: float = 0  # Unix timestamp

    @property
    def name(self) -> str:
        return "RiskService"

    async def _setup(self):
        if self.risk_manager is None:
            from execution.mt5_client import MT5Client
            client = MT5Client()
            self.risk_manager = RiskManager(client)

        # Subscribe to candidates
        try:
            from services.gemma_brain_service import GEMMA_VETTED
            self.bus.subscribe(GEMMA_VETTED, self._on_trade_candidate)
        except ImportError:
            pass
        self.bus.subscribe(EventTypes.TRADE_CANDIDATE, self._on_raw_candidate)
        self.bus.subscribe(EventTypes.POSITION_CLOSED, self._on_position_closed)

        self._gemma_brain_active = False

        # Initialize daily equity tracking
        await self._init_daily_tracking()

    def set_gemma_brain_active(self, active: bool):
        self._gemma_brain_active = active

    async def _init_daily_tracking(self):
        """Snapshot starting equity for daily loss limit."""
        try:
            acct = await self.gateway.get_account_info()
            if acct:
                self._daily_start_equity = acct["equity"]
                self._daily_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                logger.info(f"[RISK] Daily equity baseline: ${self._daily_start_equity:.2f}")
        except Exception:
            pass

    async def _on_raw_candidate(self, event: Event):
        if self._gemma_brain_active:
            return
        await self._on_trade_candidate(event)

    async def _on_position_closed(self, event: Event):
        """Track consecutive wins/losses for kill switch."""
        profit = event.payload.get("profit", 0)
        if profit < 0:
            self._consecutive_losses += 1
            limit = getattr(settings, 'CONSECUTIVE_LOSS_LIMIT', 3)
            if self._consecutive_losses >= limit and not self._kill_switch_active:
                self._kill_switch_active = True
                self._kill_switch_until = time.time() + 86400  # 24 hours
                logger.warning(
                    f"[KILL SWITCH] {self._consecutive_losses} consecutive losses — "
                    f"reducing position size by 50% for 24 hours"
                )
                await self.emit(EventTypes.KILL_SWITCH_ACTIVATED, {
                    "consecutive_losses": self._consecutive_losses,
                    "reduction": getattr(settings, 'KILL_SWITCH_REDUCTION', 0.5),
                    "until": datetime.fromtimestamp(self._kill_switch_until, tz=timezone.utc).isoformat(),
                })
        else:
            self._consecutive_losses = 0
            if self._kill_switch_active and time.time() > self._kill_switch_until:
                self._kill_switch_active = False
                logger.info("[KILL SWITCH] Deactivated — winning trade after cooldown")

    async def _on_trade_candidate(self, event: Event):
        """Evaluate a trade candidate through ALL hardened risk checks."""
        candidate = event.payload
        symbol = candidate.get("symbol")
        direction = candidate.get("direction")

        if not symbol or not direction:
            await self._reject(symbol or "UNKNOWN", direction, "Missing symbol or direction")
            return

        try:
            # ─── CHECK 1: Daily loss limit ────────────────────────────
            if await self._check_daily_loss_limit():
                await self._reject(symbol, direction, "Daily loss limit (3%) hit — shutdown")
                return

            # ─── CHECK 2: Direction guard ─────────────────────────────
            if direction not in ("BUY", "SELL"):
                await self._reject(symbol, direction, f"Invalid direction: {direction}")
                return

            # ─── CHECK 3: Spread filter (30 pips = $0.30 on Gold) ─────
            tick = await self.gateway.get_tick(symbol)
            if not tick:
                await self._reject(symbol, direction, "No tick data")
                return

            spread_reject = getattr(settings, 'SPREAD_REJECT_THRESHOLD', 30)
            sym_info = await self.gateway.get_symbol_info(symbol)
            if sym_info:
                point = sym_info.point if sym_info.point > 0 else 0.01
                spread_pips = (tick.ask - tick.bid) / point / 10.0
                if spread_pips > spread_reject:
                    await self._reject(
                        symbol, direction,
                        f"Spread too wide: {spread_pips:.1f} > {spread_reject} pips"
                    )
                    return

            # ─── CHECK 4: Symbol tradeable ────────────────────────────
            if sym_info is None or sym_info.trade_mode == 0:
                await self._reject(symbol, direction, "Symbol disabled/not tradeable")
                return

            # ─── CHECK 5: R:R mandate ─────────────────────────────────
            sl_dist = candidate.get("sl_distance", 0)
            tp_dist = candidate.get("tp_distance", 0)
            if sl_dist <= 0:
                await self._reject(symbol, direction, "Invalid SL distance")
                return

            rr_ratio = tp_dist / sl_dist
            if rr_ratio < settings.MIN_RISK_REWARD_RATIO:
                await self._reject(
                    symbol, direction,
                    f"R:R {rr_ratio:.2f} < {settings.MIN_RISK_REWARD_RATIO}"
                )
                return

            # ─── CHECK 6: Correlation filter ──────────────────────────
            try:
                positions = await self.gateway.get_all_positions()
                has_conflict, conflict_reason = check_correlation_conflict(
                    symbol, direction, positions
                )
                if has_conflict:
                    await self._reject(symbol, direction, f"Correlation: {conflict_reason}")
                    return
            except Exception as e:
                logger.debug(f"Correlation check error (non-blocking): {e}")

            # ─── CHECK 7: Position sizing ─────────────────────────────
            score = candidate.get("score", 0)
            lot = self.risk_manager.calculate_position_size(
                symbol, sl_dist, score,
                scaling_factor=candidate.get("scaling_factor", 1.0),
                ml_prob=candidate.get("ml_prob"),
                emotion_state=candidate.get("emotion_state", "NEUTRAL"),
                emotion_score=candidate.get("emotion_score", 0.5),
            )

            # Apply macro size factor
            macro_factor = candidate.get("macro_size_factor", 1.0)
            lot = round(lot * macro_factor, 2)

            # Apply kill switch reduction
            if self._kill_switch_active:
                reduction = getattr(settings, 'KILL_SWITCH_REDUCTION', 0.5)
                lot = round(lot * reduction, 2)
                logger.info(f"[KILL SWITCH] Lot reduced to {lot} (x{reduction})")

            # Minimum lot floor
            if lot < 0.01:
                lot = 0.01

            # ─── CHECK 8: Effective leverage cap ──────────────────────
            max_lev = getattr(settings, 'MAX_EFFECTIVE_LEVERAGE', 20)
            contract_size = sym_info.trade_contract_size if sym_info else 100
            entry_price = tick.ask if direction == "BUY" else tick.bid
            acct = await self.gateway.get_account_info()
            if acct and acct.get("equity", 0) > 0:
                notional = lot * contract_size * entry_price
                effective_leverage = notional / acct["equity"]
                if effective_leverage > max_lev:
                    # Reduce lot to fit leverage cap
                    max_lot = (max_lev * acct["equity"]) / (contract_size * entry_price)
                    max_lot = round(max_lot, 2)
                    if max_lot < 0.01:
                        await self._reject(
                            symbol, direction,
                            f"Leverage {effective_leverage:.1f}:1 > {max_lev}:1 cap — lot too small"
                        )
                        return
                    logger.info(
                        f"[LEVERAGE CAP] Reduced lot {lot} → {max_lot} "
                        f"(eff leverage {effective_leverage:.1f} → {max_lev})"
                    )
                    lot = max_lot

            # ─── CHECK 9: Execution risk check ────────────────────────
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
                await self._reject(symbol, direction, f"Execution: {reason}")
                return

            # ─── APPROVED ─────────────────────────────────────────────
            approved = {
                **candidate,
                "lot": lot,
                "sl": sl,
                "tp": tp,
                "entry_price": entry_price,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            logger.info(
                f"[{symbol}] APPROVED {direction} | Lot: {lot} | "
                f"SL: {sl:.2f} | TP: {tp:.2f}"
            )
            await self.emit(EventTypes.TRADE_APPROVED, approved)
            self.risk_manager.record_trade(symbol)

        except Exception as e:
            logger.error(f"[{symbol}] Risk evaluation error: {e}")
            import traceback
            traceback.print_exc()
            await self._reject(symbol, direction, f"Risk error: {str(e)}")

    # ─── Helpers ──────────────────────────────────────────────────────────

    async def _check_daily_loss_limit(self) -> bool:
        """Check if daily loss limit has been breached."""
        if self._daily_shutdown:
            return True

        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today != self._daily_date:
                # New day — reset
                await self._init_daily_tracking()
                self._daily_shutdown = False
                return False

            acct = await self.gateway.get_account_info()
            if acct and self._daily_start_equity > 0:
                equity = acct["equity"]
                loss_pct = (self._daily_start_equity - equity) / self._daily_start_equity
                self._daily_loss_pct = loss_pct

                max_loss_pct = getattr(settings, 'MAX_DAILY_LOSS_PERCENT', 3.0) / 100.0
                max_loss_usd = getattr(settings, 'MAX_DAILY_LOSS_USD', 100.0)
                actual_loss_usd = self._daily_start_equity - equity

                if loss_pct >= max_loss_pct or actual_loss_usd >= max_loss_usd:
                    self._daily_shutdown = True
                    logger.warning(
                        f"[DAILY LIMIT] Loss {loss_pct*100:.1f}% "
                        f"(${actual_loss_usd:.2f}) — SHUTTING DOWN"
                    )
                    await self.emit(EventTypes.DAILY_LOSS_LIMIT_HIT, {
                        "loss_percent": loss_pct * 100,
                        "loss_usd": actual_loss_usd,
                        "start_equity": self._daily_start_equity,
                        "current_equity": equity,
                    })
                    return True
        except Exception as e:
            logger.debug(f"Daily loss check error: {e}")

        return False

    async def _reject(self, symbol, direction, reason):
        """Emit a TRADE_REJECTED event."""
        logger.info(f"[{symbol}] REJECTED {direction}: {reason}")
        await self.emit(EventTypes.TRADE_REJECTED, {
            "symbol": symbol or "UNKNOWN",
            "direction": direction or "UNKNOWN",
            "reason": reason,
        })
