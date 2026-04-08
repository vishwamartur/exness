"""
MarketDataService — Periodically fetches candles for all symbols and publishes
MARKET_DATA_READY events. Other services subscribe to these events instead
of fetching data themselves.

Replaces: PairAgent._fetch_data() inline calls
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from core.mt5_gateway import MT5Gateway
from config import settings
from market_data import loader
from utils.async_utils import run_in_executor

logger = logging.getLogger("MarketDataService")


class MarketDataService(BaseService):
    """
    Fetches market data for all configured symbols and publishes
    MARKET_DATA_READY events for downstream analysis services.
    """

    def __init__(self, event_bus: EventBus, gateway: MT5Gateway):
        super().__init__(event_bus)
        self.gateway = gateway
        self._tick_cache: Dict[str, dict] = {}
        self._data_cache: Dict[str, dict] = {}  # {symbol: {timeframe: df}}
        self._last_fetch_time: Dict[str, float] = {}

    @property
    def name(self) -> str:
        return "MarketDataService"

    async def _setup(self):
        """Subscribe to scan start events to trigger data fetching."""
        self.bus.subscribe(EventTypes.SCAN_START, self._on_scan_start)

    async def _on_scan_start(self, event: Event):
        """When a scan cycle starts, fetch data for all symbols."""
        symbols = settings.SYMBOLS
        if not symbols:
            logger.warning("No symbols configured")
            return

        # Fetch data for all symbols in parallel
        tasks = [self._fetch_symbol_data(symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Data fetch failed for {symbols[i]}: {result}")

    async def _fetch_symbol_data(self, symbol: str):
        """Fetch all timeframe data for a symbol and publish event."""
        try:
            # Check spread first
            tick = await self.gateway.get_tick(symbol)
            if not tick:
                return

            spread_ok, spread_reason = self._check_spread(symbol, tick)

            # Fetch primary timeframe
            df, truncated = await run_in_executor(
                loader.get_historical_data, symbol, settings.TIMEFRAME, 2000
            )

            if df is None or len(df) < 100:
                return
            if truncated:
                return

            data_dict = {settings.TIMEFRAME: df}

            # Multi-timeframe data
            for tf_name, tf_enabled in [
                ("M5", getattr(settings, 'M5_TREND_FILTER', False)),
                ("H1", settings.H1_TREND_FILTER),
                ("H4", settings.H4_TREND_FILTER),
            ]:
                if tf_enabled:
                    tf_df, tf_trunc = await run_in_executor(
                        loader.get_historical_data, symbol, tf_name, 250
                    )
                    if tf_df is not None and not tf_trunc:
                        data_dict[tf_name] = tf_df

            # Cache it
            self._data_cache[symbol] = data_dict
            self._last_fetch_time[symbol] = time.time()

            # Publish
            await self.emit(EventTypes.MARKET_DATA_READY, {
                "symbol": symbol,
                "data_dict": data_dict,
                "tick": {
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "time": tick.time,
                },
                "spread_ok": spread_ok,
                "spread_reason": spread_reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # Also publish tick update for trade management
            await self.emit(EventTypes.TICK_UPDATE, {
                "symbol": symbol,
                "bid": tick.bid,
                "ask": tick.ask,
                "time": tick.time,
            })

        except Exception as e:
            logger.error(f"[{symbol}] Data fetch error: {e}")

    def _check_spread(self, symbol: str, tick) -> tuple:
        """Check if spread is acceptable."""
        try:
            if (tick.ask - tick.bid) <= 0:
                return True, ""

            import MetaTrader5 as mt5
            sym_info = mt5.symbol_info(symbol)
            point = sym_info.point if sym_info and sym_info.point > 0 else 0.00001
            spread_points = (tick.ask - tick.bid) / point
            spread_pips = spread_points / 10.0

            if symbol in getattr(settings, 'SYMBOLS_CRYPTO', []):
                max_spread = getattr(settings, 'MAX_SPREAD_PIPS_CRYPTO', 20000.0)
            elif symbol in getattr(settings, 'SYMBOLS_COMMODITIES', []):
                max_spread = getattr(settings, 'MAX_SPREAD_PIPS_COMMODITY', 150.0)
            else:
                max_spread = getattr(settings, 'MAX_SPREAD_PIPS', 3.0)

            if spread_pips > max_spread:
                return False, f"Spread too wide ({spread_pips:.1f} > {max_spread:.1f} pips)"

            return True, ""
        except Exception:
            return True, ""

    def get_cached_data(self, symbol: str) -> Optional[dict]:
        """Get cached data for a symbol (for services that missed the event)."""
        return self._data_cache.get(symbol)
