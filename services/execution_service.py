"""
ExecutionService — Handles order placement on approved trades.

Subscribes to: TRADE_APPROVED
Publishes: TRADE_EXECUTED or TRADE_FAILED

Replaces: InstitutionalStrategy._execute_trade() order placement logic
"""

import logging
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from core.mt5_gateway import MT5Gateway
from config import settings

logger = logging.getLogger("ExecutionService")


class ExecutionService(BaseService):
    """
    Places orders on MT5 when trades are approved by RiskService.
    Handles both market and limit orders with proper expiration.
    """

    def __init__(self, event_bus: EventBus, gateway: MT5Gateway):
        super().__init__(event_bus)
        self.gateway = gateway

    @property
    def name(self) -> str:
        return "ExecutionService"

    async def _setup(self):
        self.bus.subscribe(EventTypes.TRADE_APPROVED, self._on_trade_approved)

    async def _on_trade_approved(self, event: Event):
        """Place an order for an approved trade."""
        trade = event.payload
        symbol = trade.get("symbol")
        direction = trade.get("direction")
        lot = trade.get("lot", settings.LOT_SIZE)
        sl = trade.get("sl", 0)
        tp = trade.get("tp", 0)

        if not symbol or not direction:
            return

        try:
            # Get fresh tick
            tick = await self.gateway.get_tick(symbol)
            if not tick:
                await self.emit(EventTypes.TRADE_FAILED, {
                    "symbol": symbol,
                    "direction": direction,
                    "reason": "No tick data at execution time",
                })
                return

            # Build order request
            exp_minutes = getattr(settings, 'LIMIT_ORDER_EXPIRATION_MINUTES', 15)
            dt = datetime.now() + timedelta(minutes=exp_minutes)
            expiration_ts = int(dt.timestamp())

            # Determine order type and price
            # Quick Scalp Mode always uses MARKET orders for instant fill
            quick_scalp = getattr(settings, 'QUICK_SCALP_MODE', False)
            force_market = getattr(settings, 'FORCE_TEST_TRADES', False) or (
                quick_scalp and getattr(settings, 'QUICK_SCALP_MARKET_ORDER', True)
            )

            # Override lot size if quick scalp mode
            if quick_scalp and not getattr(settings, 'FORCE_TEST_TRADES', False):
                scalp_lot = getattr(settings, 'QUICK_SCALP_LOT_SIZE', 0.5)
                max_lot = getattr(settings, 'QUICK_SCALP_MAX_LOT', 2.0)
                lot = min(max(lot, scalp_lot), max_lot)
                logger.info(f"[{symbol}] ⚡ QUICK SCALP MODE — Big Lot: {lot}")

            if force_market:
                # Market order — instant fill (Quick Scalp or Test mode)
                if direction == "BUY":
                    price = tick.ask
                    sl = price - trade.get("sl_distance", 0)
                    tp = price + trade.get("tp_distance", 0)
                    order_type = mt5.ORDER_TYPE_BUY
                else:
                    price = tick.bid
                    sl = price + trade.get("sl_distance", 0)
                    tp = price - trade.get("tp_distance", 0)
                    order_type = mt5.ORDER_TYPE_SELL

                order_comment = "QScalp⚡" if quick_scalp else "EDA Market"
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": lot,
                    "type": order_type,
                    "price": price,
                    "sl": sl,
                    "tp": tp,
                    "deviation": settings.DEVIATION,
                    "magic": 234000,
                    "comment": order_comment,
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
            else:
                # Limit order (Maker strategy — sit on bid/ask)
                if direction == "BUY":
                    limit_price = tick.bid
                    sl = limit_price - trade.get("sl_distance", 0)
                    tp = limit_price + trade.get("tp_distance", 0)
                    order_type = mt5.ORDER_TYPE_BUY_LIMIT
                else:
                    limit_price = tick.ask
                    sl = limit_price + trade.get("sl_distance", 0)
                    tp = limit_price - trade.get("tp_distance", 0)
                    order_type = mt5.ORDER_TYPE_SELL_LIMIT

                price = limit_price

                request = {
                    "action": mt5.TRADE_ACTION_PENDING,
                    "symbol": symbol,
                    "volume": lot,
                    "type": order_type,
                    "price": price,
                    "sl": sl,
                    "tp": tp,
                    "deviation": settings.DEVIATION,
                    "magic": 234000,
                    "comment": "EDA Limit",
                    "type_time": mt5.ORDER_TIME_SPECIFIED,
                    "type_filling": mt5.ORDER_FILLING_RETURN,
                    "expiration": expiration_ts,
                }

            # Execute
            logger.info(
                f"[{symbol}] Placing {'MARKET' if force_market else 'LIMIT'} "
                f"{direction} @ {price:.5f} | Lot: {lot} | SL: {sl:.5f} | TP: {tp:.5f}"
            )

            result = await self.gateway.send_order(request)

            if result:
                logger.info(f"[{symbol}] ORDER FILLED: ticket={result.order}")
                await self.emit(EventTypes.TRADE_EXECUTED, {
                    "ticket": result.order,
                    "symbol": symbol,
                    "direction": direction,
                    "price": price,
                    "lot": lot,
                    "sl": sl,
                    "tp": tp,
                    "score": trade.get("score", 0),
                    "ml_prob": trade.get("ml_prob", 0.5),
                    "details": trade.get("details", {}),
                    "regime": trade.get("regime", "UNKNOWN"),
                    "emotion_state": trade.get("emotion_state", "NEUTRAL"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

                # Also emit backward-compatible TRADE_EXECUTION for dashboard
                await self.emit(EventTypes.TRADE_EXECUTION, {
                    "symbol": symbol,
                    "direction": direction,
                    "price": price,
                    "lot": lot,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            else:
                await self.emit(EventTypes.TRADE_FAILED, {
                    "symbol": symbol,
                    "direction": direction,
                    "reason": "Order rejected by MT5",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        except Exception as e:
            logger.error(f"[{symbol}] Execution error: {e}")
            import traceback
            traceback.print_exc()
            await self.emit(EventTypes.TRADE_FAILED, {
                "symbol": symbol,
                "direction": direction,
                "reason": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
