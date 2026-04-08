"""
MT5Gateway — Thread-safe proxy for all MetaTrader5 API calls.

The MT5 Python API is NOT thread-safe. All calls must originate from the
same thread that called mt5.initialize(). This gateway serializes every
MT5 operation through a dedicated thread using asyncio.run_in_executor.

Usage:
    gateway = MT5Gateway()
    await gateway.connect()
    tick = await gateway.get_tick("XAUUSD")
    await gateway.shutdown()
"""

import asyncio
import logging
import functools
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import MetaTrader5 as mt5

from config import settings

logger = logging.getLogger("MT5Gateway")

# Single-thread executor for MT5 calls
_mt5_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5")


def _mt5_call(func: Callable, *args, **kwargs) -> Any:
    """Execute a synchronous function in the MT5 thread."""
    return func(*args, **kwargs)


class MT5Gateway:
    """
    Async-safe wrapper around the MetaTrader5 API.
    All blocking MT5 calls are routed through a single-threaded executor.
    """

    def __init__(self):
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def _run(self, func: Callable, *args, **kwargs) -> Any:
        """Run a synchronous function in the MT5 thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _mt5_executor,
            functools.partial(func, *args, **kwargs),
        )

    # ─── Connection ───────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Initialize and login to MT5."""
        def _connect():
            if not mt5.initialize(
                path=settings.MT5_PATH,
                login=settings.MT5_LOGIN,
                password=settings.MT5_PASSWORD,
                server=settings.MT5_SERVER,
            ):
                logger.error(f"MT5 initialize() failed: {mt5.last_error()}")
                return False

            if not mt5.login(
                settings.MT5_LOGIN,
                password=settings.MT5_PASSWORD,
                server=settings.MT5_SERVER,
            ):
                logger.error("MT5 login() failed")
                return False

            return True

        self._connected = await self._run(_connect)
        if self._connected:
            logger.info("MT5 connected successfully")
        return self._connected

    async def shutdown(self):
        """Shutdown MT5 connection."""
        await self._run(mt5.shutdown)
        self._connected = False
        logger.info("MT5 shutdown")

    @property
    def connected(self) -> bool:
        return self._connected

    # ─── Symbol Detection ─────────────────────────────────────────────────

    async def detect_available_symbols(self) -> bool:
        """Auto-detect available symbols on this Exness account."""
        def _detect():
            found_symbols = []
            found_majors = []
            found_minors = []
            found_crypto = []
            found_commodities = []
            suffix_detected = None

            BLOCKED_QUOTE_CCY = {'KRW', 'CNH', 'CNY', 'ZAR', 'TRY', 'HUF', 'CZK', 'SEK', 'NOK', 'DKK'}

            for base in settings.ALL_BASE_SYMBOLS:
                matched = None
                for suffix in settings.EXNESS_SUFFIXES:
                    candidate = base + suffix
                    info = mt5.symbol_info(candidate)
                    if info is not None:
                        if info.trade_mode == 0:
                            break
                        if info.currency_profit in BLOCKED_QUOTE_CCY:
                            break
                        if not info.visible:
                            mt5.symbol_select(candidate, True)
                        matched = candidate
                        if suffix_detected is None and suffix:
                            suffix_detected = suffix
                        break

                if matched:
                    found_symbols.append(matched)
                    if base in settings.SYMBOLS_FOREX_MAJORS_BASE:
                        found_majors.append(matched)
                    elif base in settings.SYMBOLS_FOREX_MINORS_BASE:
                        found_minors.append(matched)
                    elif base in settings.SYMBOLS_CRYPTO_BASE:
                        found_crypto.append(matched)
                    elif base in settings.SYMBOLS_COMMODITIES_BASE:
                        found_commodities.append(matched)

            # Update settings at runtime
            settings.SYMBOLS = found_symbols
            settings.SYMBOLS_FOREX_MAJORS = found_majors
            settings.SYMBOLS_FOREX_MINORS = found_minors
            settings.SYMBOLS_CRYPTO = found_crypto
            settings.SYMBOLS_COMMODITIES = found_commodities

            suffix_label = f"'{suffix_detected}'" if suffix_detected else "none"
            print(f"  Account suffix: {suffix_label}")
            print(f"  Found {len(found_symbols)} instruments:")
            print(f"    Forex Majors:  {len(found_majors)} — {', '.join(found_majors)}")
            print(f"    Forex Minors:  {len(found_minors)} — {', '.join(found_minors[:5])}{'...' if len(found_minors) > 5 else ''}")
            print(f"    Crypto:        {len(found_crypto)} — {', '.join(found_crypto)}")
            print(f"    Commodities:   {len(found_commodities)} — {', '.join(found_commodities)}")

            return len(found_symbols) > 0

        return await self._run(_detect)

    # ─── Market Data ──────────────────────────────────────────────────────

    async def get_tick(self, symbol: str) -> Optional[Any]:
        """Get latest tick for a symbol."""
        return await self._run(mt5.symbol_info_tick, symbol)

    async def get_symbol_info(self, symbol: str) -> Optional[Any]:
        """Get symbol info."""
        return await self._run(mt5.symbol_info, symbol)

    async def get_rates(self, symbol: str, timeframe_str: str, count: int):
        """Get historical rates. Returns raw rates array."""
        tf_map = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
            "W1": mt5.TIMEFRAME_W1,
        }
        tf = tf_map.get(timeframe_str, mt5.TIMEFRAME_M1)
        return await self._run(mt5.copy_rates_from_pos, symbol, tf, 0, count)

    # ─── Account ──────────────────────────────────────────────────────────

    async def get_account_info(self) -> Optional[Dict]:
        """Get account info as dict."""
        def _get():
            info = mt5.account_info()
            if info is None:
                return None
            return {
                "balance": info.balance,
                "equity": info.equity,
                "margin": info.margin,
                "free_margin": info.margin_free,
                "profit": info.profit,
                "leverage": info.leverage,
                "currency": info.currency,
            }
        return await self._run(_get)

    async def get_account_balance(self) -> float:
        info = await self._run(mt5.account_info)
        return info.balance if info else 0.0

    # ─── Positions ────────────────────────────────────────────────────────

    async def get_positions(self, symbol: str = None) -> List:
        """Get open positions."""
        def _get():
            if symbol:
                pos = mt5.positions_get(symbol=symbol)
            else:
                pos = mt5.positions_get()
            return list(pos) if pos else []
        return await self._run(_get)

    async def get_all_positions(self) -> List:
        """Get all open positions across all symbols."""
        return await self.get_positions()

    # ─── Orders ───────────────────────────────────────────────────────────

    async def send_order(self, request: Dict) -> Optional[Any]:
        """Send an order to MT5. Returns the result object or None."""
        def _send():
            result = mt5.order_send(request)
            if result is None:
                logger.error(f"order_send returned None: {mt5.last_error()}")
                return None
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error(f"Order failed: {result.comment} ({result.retcode})")
                return None
            return result
        return await self._run(_send)

    async def modify_position(self, ticket: int, sl: float, tp: float) -> bool:
        """Modify SL/TP of an existing position."""
        def _modify():
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "sl": sl,
                "tp": tp,
                "magic": 234000,
            }
            result = mt5.order_send(request)
            return result and result.retcode == mt5.TRADE_RETCODE_DONE
        return await self._run(_modify)

    async def close_position(self, ticket: int) -> bool:
        """Fully close a position by ticket."""
        def _close():
            position = mt5.positions_get(ticket=ticket)
            if position is None or len(position) == 0:
                return False

            pos = position[0]
            tick = mt5.symbol_info_tick(pos.symbol)
            if not tick:
                return False

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                "price": tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask,
                "deviation": settings.DEVIATION,
                "magic": 234000,
                "comment": "Close EDA",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
                "position": ticket,
            }

            result = mt5.order_send(request)
            return result and result.retcode == mt5.TRADE_RETCODE_DONE
        return await self._run(_close)

    async def partial_close(self, ticket: int, fraction: float = 0.5) -> bool:
        """Partially close a position."""
        def _partial():
            position = mt5.positions_get(ticket=ticket)
            if position is None or len(position) == 0:
                return False

            pos = position[0]
            close_volume = round(pos.volume * fraction, 2)

            sym_info = mt5.symbol_info(pos.symbol)
            if sym_info and close_volume < sym_info.volume_min:
                return False

            tick = mt5.symbol_info_tick(pos.symbol)
            if not tick:
                return False

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": close_volume,
                "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                "price": tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask,
                "deviation": settings.DEVIATION,
                "magic": 234000,
                "comment": "Partial EDA",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
                "position": ticket,
            }

            result = mt5.order_send(request)
            return result and result.retcode == mt5.TRADE_RETCODE_DONE
        return await self._run(_partial)

    # ─── History ──────────────────────────────────────────────────────────

    async def get_history_deals(self, date_from, date_to) -> List:
        """Get history deals in a time range."""
        def _get():
            deals = mt5.history_deals_get(date_from, date_to)
            return list(deals) if deals else []
        return await self._run(_get)
