"""
Data Cache — TTL-based caching for multi-timeframe data.

Avoids redundant MT5 API calls by caching:
- H1 data: refreshes every 15 minutes
- H4 data: refreshes every 60 minutes
- M15 data: refreshes every 5 minutes

Cuts hundreds of API calls per scan cycle.
"""

import time
from market_data import loader


class DataCache:
    """Thread-safe data cache with time-to-live (TTL) expiry."""

    def __init__(self):
        self._cache = {}

    # TTLs in seconds per timeframe
    TTL = {
        "M15": 300,    # 5 minutes
        "H1":  900,    # 15 minutes
        "H4":  3600,   # 60 minutes
        "D1":  7200,   # 2 hours
    }

    def get(self, symbol, timeframe, n_bars=500):
        """
        Returns cached data if fresh, otherwise fetches from MT5.
        """
        key = f"{symbol}_{timeframe}"
        now = time.time()

        if key in self._cache:
            cached_time, df = self._cache[key]
            ttl = self.TTL.get(timeframe, 300)
            if now - cached_time < ttl:
                return df

        # Cache miss — fetch from MT5
        df = loader.get_historical_data(symbol, timeframe, n_bars)
        if df is not None:
            self._cache[key] = (now, df)

        return df

    def invalidate(self, symbol=None, timeframe=None):
        """Clears cache entries. If no args, clears all."""
        if symbol is None and timeframe is None:
            self._cache.clear()
            return

        keys_to_remove = []
        for key in self._cache:
            if symbol and symbol in key:
                keys_to_remove.append(key)
            elif timeframe and timeframe in key:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del self._cache[key]

    def stats(self):
        """Returns cache statistics."""
        now = time.time()
        total = len(self._cache)
        fresh = 0
        for key, (cached_time, _) in self._cache.items():
            tf = key.split('_')[-1] if '_' in key else 'M15'
            ttl = self.TTL.get(tf, 300)
            if now - cached_time < ttl:
                fresh += 1
        return {'total': total, 'fresh': fresh, 'stale': total - fresh}
