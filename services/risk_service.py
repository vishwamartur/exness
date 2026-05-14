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
        self._daily_trades_since_tier: int = 0

        # Dynamic daily drawdown tier tracking
        self._daily_drawdown_tier: int = 0  # 0=normal, 1=tier1, 2=tier2, 3=shutdown

        # Kill switch: consecutive loss tracking
        self._consecutive_losses: int = 0
        self._kill_switch_active: bool = False
        self._kill_switch_until: float = 0  # Unix timestamp

    @property
    def daily_drawdown_tier(self) -> int:
        """Current drawdown tier: 0=normal, 1=1% (max 2 trades), 2=2% (1 trade at 50%), 3=shutdown."""
        return self._daily_drawdown_tier

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

            # ─── CHECK 6b: Correlation-aware gold exposure ────────────
            try:
                positions = await self.gateway.get_all_positions()
                acct = await self.gateway.get_account_info()
                if acct and acct.get("equity", 0) > 0 and sym_info:
                    equity = acct["equity"]
                    contract_size = sym_info.trade_contract_size if sym_info else 100
                    # Sum directional exposure for gold positions in same direction
                    gold_exposure = 0.0
                    for pos in positions:
                        pos_symbol = pos.symbol if hasattr(pos, 'symbol') else ""
                        if 'XAU' in pos_symbol.upper():
                            pos_dir = 'BUY' if (hasattr(pos, 'type') and pos.type == 0) else 'SELL'
                            if pos_dir == direction:
                                pos_vol = pos.volume if hasattr(pos, 'volume') else 0.0
                                pos_contract = contract_size  # Same gold contract size
                                gold_exposure += pos_vol * pos_contract
                    # Check if adding this trade would exceed 2% of equity
                    max_gold_exposure = equity * 0.02
                    if gold_exposure > 0 and 'XAU' in symbol.upper():
                        # Will be applied after lot calculation to reduce proportionally
                        self._gold_exposure_ratio = min(1.0, max_gold_exposure / (gold_exposure + contract_size * 0.01)) if gold_exposure >= max_gold_exposure else 1.0
                    else:
                        self._gold_exposure_ratio = 1.0
                else:
                    self._gold_exposure_ratio = 1.0
            except Exception as e:
                self._gold_exposure_ratio = 1.0
                logger.debug(f"Gold exposure check error (non-blocking): {e}")

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

            # Apply gold exposure reduction (CHECK 6b)
            if hasattr(self, '_gold_exposure_ratio') and self._gold_exposure_ratio < 1.0:
                original_lot = lot
                lot = round(lot * self._gold_exposure_ratio, 2)
                logger.info(
                    f"[GOLD EXPOSURE] Lot reduced {original_lot} -> {lot} "
                    f"(gold directional exposure ratio: {self._gold_exposure_ratio:.2f})"
                )

            # Apply time-based risk reduction near session close
            try:
                now_utc = datetime.now(timezone.utc)
                current_hour = now_utc.hour
                current_minute = now_utc.minute
                current_minutes = current_hour * 60 + current_minute
                # London close = 13:00 UTC, NY close = 17:00 UTC
                london_close_minutes = int(getattr(settings, 'LONDON_SESSION_END', 13.0) * 60)
                ny_close_minutes = int(getattr(settings, 'NY_SESSION_END', 17.0) * 60)
                session_close_reduction = getattr(settings, 'SESSION_CLOSE_RISK_REDUCTION', 0.3)

                near_session_close = False
                for close_min in [london_close_minutes, ny_close_minutes]:
                    # Within 60 minutes before session close
                    if 0 <= (close_min - current_minutes) <= 60:
                        near_session_close = True
                        break

                if near_session_close:
                    original_lot = lot
                    lot = round(lot * (1 - session_close_reduction), 2)
                    logger.info(
                        f"[SESSION CLOSE] Lot reduced {original_lot} -> {lot} "
                        f"({session_close_reduction*100:.0f}% reduction near session close)"
                    )
            except Exception as e:
                logger.debug(f"Time-based risk reduction error (non-blocking): {e}")

            # Apply dynamic daily limit size reduction (tier 2 = 50% size)
            if getattr(settings, 'DYNAMIC_DAILY_LIMIT_ENABLED', True):
                if self._daily_drawdown_tier == 2:
                    original_lot = lot
                    lot = round(lot * 0.5, 2)
                    logger.info(
                        f"[DYNAMIC LIMIT] Tier 2 active - lot reduced {original_lot} -> {lot} (50%)"
                    )

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

            # ─── CHECK 10: Portfolio heat ─────────────────────────────
            portfolio_heat = await self._calculate_portfolio_heat()
            max_heat = getattr(settings, 'MAX_PORTFOLIO_HEAT_PERCENT', 3.0)
            if portfolio_heat > max_heat:
                await self._reject(
                    symbol, direction,
                    f"Portfolio heat {portfolio_heat:.1f}% > {max_heat}% cap"
                )
                return

            # ─── APPROVED ─────────────────────────────────────────────
            approved = {
                **candidate,
                "lot": lot,
                "sl": sl,
                "tp": tp,
                "entry_price": entry_price,
                "portfolio_heat": round(portfolio_heat, 2),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            logger.info(
                f"[{symbol}] APPROVED {direction} | Lot: {lot} | "
                f"SL: {sl:.2f} | TP: {tp:.2f}"
            )
            await self.emit(EventTypes.TRADE_APPROVED, approved)
            self.risk_manager.record_trade(symbol)
            self._daily_trades_since_tier += 1

        except Exception as e:
            logger.error(f"[{symbol}] Risk evaluation error: {e}")
            import traceback
            traceback.print_exc()
            await self._reject(symbol, direction, f"Risk error: {str(e)}")

    # ─── Helpers ──────────────────────────────────────────────────────────

    async def _check_daily_loss_limit(self) -> bool:
        """Check if daily loss limit has been breached with dynamic tightening."""
        if self._daily_shutdown:
            return True

        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today != self._daily_date:
                # New day - reset
                await self._init_daily_tracking()
                self._daily_shutdown = False
                self._daily_drawdown_tier = 0
                self._daily_trades_since_tier = 0
                return False

            acct = await self.gateway.get_account_info()
            if acct and self._daily_start_equity > 0:
                equity = acct["equity"]
                loss_pct = (self._daily_start_equity - equity) / self._daily_start_equity
                self._daily_loss_pct = loss_pct

                # Dynamic daily limit tiers
                if getattr(settings, 'DYNAMIC_DAILY_LIMIT_ENABLED', True):
                    shutdown_pct = getattr(settings, 'DRAWDOWN_SHUTDOWN_PERCENT', 2.5) / 100.0
                    tier2_pct = getattr(settings, 'DRAWDOWN_TIER_2_PERCENT', 2.0) / 100.0
                    tier1_pct = getattr(settings, 'DRAWDOWN_TIER_1_PERCENT', 1.0) / 100.0

                    if loss_pct >= shutdown_pct:
                        self._daily_drawdown_tier = 3
                        self._daily_shutdown = True
                        logger.warning(
                            f"[DYNAMIC LIMIT] Drawdown {loss_pct*100:.1f}% >= "
                            f"{shutdown_pct*100:.1f}% - FULL SHUTDOWN"
                        )
                        await self.emit(EventTypes.DAILY_LOSS_LIMIT_HIT, {
                            "loss_percent": loss_pct * 100,
                            "tier": 3,
                            "start_equity": self._daily_start_equity,
                            "current_equity": equity,
                        })
                        return True
                    elif loss_pct >= tier2_pct:
                        if self._daily_drawdown_tier < 2:
                            self._daily_drawdown_tier = 2
                            self._daily_trades_since_tier = 0
                            logger.warning(
                                f"[DYNAMIC LIMIT] Tier 2: Drawdown {loss_pct*100:.1f}% - "
                                f"max 1 trade at 50% size"
                            )
                        # Allow max 1 trade in tier 2
                        if self._daily_trades_since_tier >= 1:
                            return True
                    elif loss_pct >= tier1_pct:
                        if self._daily_drawdown_tier < 1:
                            self._daily_drawdown_tier = 1
                            self._daily_trades_since_tier = 0
                            logger.warning(
                                f"[DYNAMIC LIMIT] Tier 1: Drawdown {loss_pct*100:.1f}% - "
                                f"max 2 remaining trades"
                            )
                        # Allow max 2 trades in tier 1
                        if self._daily_trades_since_tier >= 2:
                            return True

                # Fixed daily loss limit (original behavior as fallback)
                max_loss_pct = getattr(settings, 'MAX_DAILY_LOSS_PERCENT', 3.0) / 100.0
                max_loss_usd = getattr(settings, 'MAX_DAILY_LOSS_USD', 100.0)
                actual_loss_usd = self._daily_start_equity - equity

                if loss_pct >= max_loss_pct or actual_loss_usd >= max_loss_usd:
                    self._daily_shutdown = True
                    logger.warning(
                        f"[DAILY LIMIT] Loss {loss_pct*100:.1f}% "
                        f"(${actual_loss_usd:.2f}) - SHUTTING DOWN"
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

    async def _calculate_portfolio_heat(self) -> float:
        """
        Calculate portfolio heat: sum of (sl_distance * lot * contract_size) for all
        open positions as a percentage of equity.
        
        Returns:
            Portfolio heat as a percentage of equity.
        """
        try:
            positions = await self.gateway.get_all_positions()
            acct = await self.gateway.get_account_info()
            if not acct or acct.get("equity", 0) <= 0:
                return 0.0

            equity = acct["equity"]
            total_risk = 0.0

            for pos in positions:
                pos_symbol = pos.symbol if hasattr(pos, 'symbol') else ""
                pos_vol = pos.volume if hasattr(pos, 'volume') else 0.0
                pos_sl = pos.sl if hasattr(pos, 'sl') else 0.0
                pos_open = pos.price_open if hasattr(pos, 'price_open') else 0.0
                pos_type = pos.type if hasattr(pos, 'type') else 0

                if pos_sl <= 0 or pos_open <= 0:
                    continue

                # Calculate SL distance
                if pos_type == 0:  # BUY
                    sl_distance = pos_open - pos_sl
                else:  # SELL
                    sl_distance = pos_sl - pos_open

                if sl_distance <= 0:
                    continue

                # Get contract size for this position's symbol
                try:
                    sym_info = await self.gateway.get_symbol_info(pos_symbol)
                    contract_size = sym_info.trade_contract_size if sym_info else 100
                except Exception:
                    contract_size = 100

                total_risk += sl_distance * pos_vol * contract_size

            heat_pct = (total_risk / equity) * 100.0
            return heat_pct
        except Exception as e:
            logger.debug(f"Portfolio heat calculation error: {e}")
            return 0.0
