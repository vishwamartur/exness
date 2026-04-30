"""
TradeManagerService — Active trade management (trailing stops, breakeven,
early cuts, partial closes).

Subscribes to: SCAN_START (to manage on each scan cycle)
Publishes: POSITION_MODIFIED, POSITION_CLOSED

Replaces: PairAgent.manage_active_trades() + RiskManager.monitor_positions()
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from core.mt5_gateway import MT5Gateway
from utils.risk_manager import RiskManager
from utils.async_utils import run_in_executor
from market_data import loader
from config import settings

logger = logging.getLogger("TradeManagerService")


class TradeManagerService(BaseService):
    """
    Manages active positions: trailing stops, breakeven, early loss cutting.
    Runs on each scan cycle for all open positions.
    """

    def __init__(self, event_bus: EventBus, gateway: MT5Gateway,
                 risk_manager: RiskManager = None):
        super().__init__(event_bus)
        self.gateway = gateway
        self.risk_manager = risk_manager
        # ATR cache per symbol
        self._atr_cache: Dict[str, float] = {}
        self._atr_cache_time: Dict[str, float] = {}

    @property
    def name(self) -> str:
        return "TradeManagerService"

    async def _setup(self):
        if self.risk_manager is None:
            from execution.mt5_client import MT5Client
            client = MT5Client()
            self.risk_manager = RiskManager(client)

        self.bus.subscribe(EventTypes.SCAN_START, self._on_scan_start)

    async def _on_scan_start(self, event: Event):
        """Manage all active positions at the start of each scan cycle."""
        try:
            positions = await self.gateway.get_all_positions()
            positions = positions or []

            # --- SYNC DATABASE CLOSURES ---
            try:
                import sqlite3
                import os
                import datetime
                import MetaTrader5 as _mt5
                
                db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trade_journal.db")
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT ticket FROM trades WHERE outcome = 'OPEN'")
                open_db_tickets = {row[0] for row in cursor.fetchall()}
                conn.close()
                
                active_mt5_tickets = {pos.ticket for pos in positions}
                missing_tickets = open_db_tickets - active_mt5_tickets
                
                if missing_tickets:
                    # Look back 30 days for deals
                    deals = _mt5.history_deals_get(datetime.datetime.now() - datetime.timedelta(days=30), datetime.datetime.now())
                    for missing_ticket in missing_tickets:
                        total_profit = 0.0
                        last_price = 0.0
                        reason = "Missing from MT5 (Auto-Sync)"
                        
                        if deals:
                            closing_deals = [d for d in deals if d.position_id == missing_ticket and d.entry == _mt5.DEAL_ENTRY_OUT]
                            if closing_deals:
                                total_profit = sum(d.profit + d.commission + d.swap for d in closing_deals)
                                last_price = closing_deals[-1].price
                                reason = "MT5 Native Close (SL/TP)"
                                
                        await self.emit(EventTypes.POSITION_CLOSED, {
                            "ticket": missing_ticket,
                            "exit_price": last_price,
                            "profit": total_profit,
                            "reason": reason,
                            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
                        })
            except Exception as e:
                logger.error(f"Sync closures failed: {e}")
            # --- END SYNC ---

            if not positions:
                return

            # Group positions by symbol
            by_symbol: Dict[str, list] = {}
            for pos in positions:
                sym = pos.symbol
                if sym not in by_symbol:
                    by_symbol[sym] = []
                by_symbol[sym].append(pos)

            # Manage each symbol's positions
            tasks = [
                self._manage_symbol(symbol, symbol_positions)
                for symbol, symbol_positions in by_symbol.items()
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logger.error(f"Trade management error: {e}")

    async def _manage_symbol(self, symbol: str, positions: list):
        """Manage positions for a single symbol."""
        try:
            tick = await self.gateway.get_tick(symbol)
            if not tick:
                return

            # Get ATR (cached for 5 minutes)
            atr = await self._get_atr(symbol)

            # Get DataFrame for momentum analysis (smart exit)
            df_for_exit = None
            if getattr(settings, 'SMART_EXIT_ENABLED', False):
                try:
                    from strategy import features
                    df_exit, trunc = await run_in_executor(
                        loader.get_historical_data, symbol, settings.TIMEFRAME, 200
                    )
                    if df_exit is not None and not trunc:
                        df_exit = features.add_technical_features(df_exit)
                        df_for_exit = df_exit
                except Exception:
                    pass

            # Use RiskManager's monitor_positions
            actions = self.risk_manager.monitor_positions(
                symbol, positions, tick, atr=atr, df=df_for_exit
            )

            # ⚡ QUICK SCALP MODE — aggressive inline profit booking
            if getattr(settings, 'QUICK_SCALP_MODE', False) and atr > 0:
                quick_actions = self._quick_scalp_manage(symbol, positions, tick, atr)
                # Merge: quick scalp actions override (dedupe by ticket)
                existing_tickets = {a.get("ticket") for a in actions}
                for qa in quick_actions:
                    if qa.get("ticket") not in existing_tickets:
                        actions.append(qa)
                    else:
                        # Quick scalp action takes priority
                        actions = [a for a in actions if a.get("ticket") != qa.get("ticket")]
                        actions.append(qa)

            # Execute actions
            for act in actions:
                try:
                    if act["type"] == "CLOSE":
                        success = await self.gateway.close_position(act["ticket"])
                        if success:
                            logger.info(f"[{symbol}] CLOSED #{act['ticket']}: {act['reason']}")
                            await self.emit(EventTypes.POSITION_CLOSED, {
                                "ticket": act["ticket"],
                                "symbol": symbol,
                                "reason": act["reason"],
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })

                    elif act["type"] == "MODIFY":
                        success = await self.gateway.modify_position(
                            act["ticket"], act["sl"], act["tp"]
                        )
                        if success:
                            logger.info(f"[{symbol}] MODIFIED #{act['ticket']}: {act['reason']}")
                            await self.emit(EventTypes.POSITION_MODIFIED, {
                                "ticket": act["ticket"],
                                "symbol": symbol,
                                "new_sl": act["sl"],
                                "reason": act["reason"],
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })

                    elif act["type"] == "PARTIAL":
                        success = await self.gateway.partial_close(
                            act["ticket"], act["fraction"]
                        )
                        if success:
                            logger.info(f"[{symbol}] PARTIAL #{act['ticket']}: {act['reason']}")
                            await self.emit(EventTypes.POSITION_MODIFIED, {
                                "ticket": act["ticket"],
                                "symbol": symbol,
                                "action": "PARTIAL_CLOSE",
                                "fraction": act["fraction"],
                                "reason": act["reason"],
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })

                except Exception as e:
                    logger.error(f"[{symbol}] Action failed: {e}")

            # ATR trailing stop (separate from smart exit)
            if getattr(settings, 'USE_TRAILING_STOP', False) and atr > 0:
                for pos in positions:
                    try:
                        # Inline trailing logic (avoiding MT5Client dependency)
                        import MetaTrader5 as _mt5
                        multiplier = getattr(settings, 'TRAILING_ATR_MULTIPLIER', 1.5)
                        trail_distance = atr * multiplier

                        if pos.type == _mt5.ORDER_TYPE_BUY:
                            new_sl = tick.bid - trail_distance
                            if new_sl > pos.sl and new_sl < tick.bid:
                                await self.gateway.modify_position(pos.ticket, new_sl, pos.tp)
                        else:
                            new_sl = tick.ask + trail_distance
                            if (new_sl < pos.sl or pos.sl == 0) and new_sl > tick.ask:
                                await self.gateway.modify_position(pos.ticket, new_sl, pos.tp)
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"[{symbol}] Symbol management error: {e}")

    def _quick_scalp_manage(self, symbol: str, positions: list, tick, atr: float) -> list:
        """
        ⚡ Quick Scalp Mode: aggressive profit booking logic.
        - Book 50% profit at 0.3x ATR
        - Move to breakeven at 0.3x ATR
        - Trail tightly at 0.4x ATR
        - Cut losers fast at 0.3x ATR against
        """
        import MetaTrader5 as _mt5
        actions = []

        be_atr = getattr(settings, 'QUICK_SCALP_BREAKEVEN_ATR', 0.3)
        trail_atr = getattr(settings, 'QUICK_SCALP_TRAIL_ATR', 0.4)
        partial_frac = getattr(settings, 'QUICK_SCALP_PARTIAL_FRACTION', 0.50)
        early_cut_atr = getattr(settings, 'QUICK_SCALP_EARLY_CUT_ATR', 0.3)

        for pos in positions:
            try:
                is_buy = pos.type == _mt5.ORDER_TYPE_BUY
                entry = pos.price_open
                current_sl = pos.sl

                if is_buy:
                    profit_dist = tick.bid - entry
                    loss_dist = entry - tick.bid
                else:
                    profit_dist = entry - tick.ask
                    loss_dist = tick.ask - entry

                # 1. Quick loss cut — if losing > 0.3x ATR, close immediately
                if loss_dist >= early_cut_atr * atr and profit_dist < 0:
                    print(f"[QUICK SCALP] ⚡ {symbol} #{pos.ticket} — Early loss cut "
                          f"(loss={loss_dist:.4f} > {early_cut_atr}xATR)")
                    actions.append({
                        "type": "CLOSE",
                        "ticket": pos.ticket,
                        "reason": f"QScalp early loss cut ({loss_dist:.4f})"
                    })
                    continue

                # 2. Partial close at breakeven threshold (50% off)
                if profit_dist >= be_atr * atr:
                    # If we haven't taken partial yet (track by a simple volume check)
                    if pos.volume > getattr(settings, 'LOT_SIZE', 0.01) * 0.6:
                        print(f"[QUICK SCALP] ⚡ {symbol} #{pos.ticket} — Partial close 50% "
                              f"(profit={profit_dist:.4f})")
                        actions.append({
                            "type": "PARTIAL",
                            "ticket": pos.ticket,
                            "fraction": partial_frac,
                            "reason": f"QScalp partial profit at {profit_dist:.4f}"
                        })

                    # 3. Move SL to breakeven
                    if is_buy:
                        new_sl = entry + (atr * 0.02)  # tiny buffer
                        if new_sl > current_sl:
                            print(f"[QUICK SCALP] ⚡ {symbol} #{pos.ticket} — Move to breakeven")
                            actions.append({
                                "type": "MODIFY",
                                "ticket": pos.ticket,
                                "sl": new_sl,
                                "tp": pos.tp,
                                "reason": "QScalp breakeven lock"
                            })
                    else:
                        new_sl = entry - (atr * 0.02)
                        if new_sl < current_sl or current_sl == 0:
                            print(f"[QUICK SCALP] ⚡ {symbol} #{pos.ticket} — Move to breakeven")
                            actions.append({
                                "type": "MODIFY",
                                "ticket": pos.ticket,
                                "sl": new_sl,
                                "tp": pos.tp,
                                "reason": "QScalp breakeven lock"
                            })

                # 4. Aggressive trailing stop at 0.4x ATR
                trail_dist = trail_atr * atr
                if is_buy:
                    trail_sl = tick.bid - trail_dist
                    if trail_sl > current_sl and trail_sl < tick.bid:
                        actions.append({
                            "type": "MODIFY",
                            "ticket": pos.ticket,
                            "sl": trail_sl,
                            "tp": pos.tp,
                            "reason": f"QScalp trail {trail_dist:.4f}"
                        })
                else:
                    trail_sl = tick.ask + trail_dist
                    if (trail_sl < current_sl or current_sl == 0) and trail_sl > tick.ask:
                        actions.append({
                            "type": "MODIFY",
                            "ticket": pos.ticket,
                            "sl": trail_sl,
                            "tp": pos.tp,
                            "reason": f"QScalp trail {trail_dist:.4f}"
                        })

            except Exception as e:
                logger.debug(f"[{symbol}] Quick scalp manage error: {e}")

        return actions

    async def _get_atr(self, symbol: str) -> float:
        """Get ATR for a symbol (cached 5 min)."""
        cached_time = self._atr_cache_time.get(symbol, 0)
        if time.time() - cached_time < 300 and symbol in self._atr_cache:
            return self._atr_cache[symbol]

        try:
            from strategy import features
            df, trunc = await run_in_executor(
                loader.get_historical_data, symbol, settings.TIMEFRAME, 200
            )
            if df is not None and not trunc:
                df = features.add_technical_features(df)
                atr = float(df['atr'].iloc[-1])
                self._atr_cache[symbol] = atr
                self._atr_cache_time[symbol] = time.time()
                return atr
        except Exception:
            pass

        return self._atr_cache.get(symbol, 0.0)
