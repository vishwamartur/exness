"""
FMP (Financial Modeling Prep) API Client — Free Tier Edition
============================================================

250 calls/day plan — only endpoints confirmed working:
  • /api/v3/fx          — Live forex exchange rates (all pairs cross-table)
  • /api/v3/fx/{pair}   — Single pair live rate (e.g. EURUSD)

Endpoints that require Starter/Premium (402/403 on this plan):
  ✗ /stable/economic-calendar   (needs Starter+)
  ✗ /stable/news/forex-latest   (needs Starter+)
  ✗ /stable/news/crypto-latest  (needs Starter+)
  ✗ /api/v3/economic_calendar   (needs Starter+)

What this client does with the available endpoints:
  1. Live FX rate table — all major pairs, polled at startup + 2h intervals
  2. Single-pair rate   — on-demand for specific symbols (e.g. pre-trade check)

These raw rates are useful for:
  • Cross-checking MT5 prices vs FMP (detect data anomalies)
  • Tracking % change to flag high-volatility periods before entering trades
  • Simple dashboard display of current FX rates

Rate limit budget:
  • /api/v3/fx  once at bot startup        →  1 call
  • Refresh every 2 hours (max 8 sessions) →  8 calls/day
  • On-demand pair checks (sparing)        → ~5 calls/day
  • Total expected usage                   → ~15 calls/day  (well under 250)
"""

from __future__ import annotations

import json
import os
import sys
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── Project root ──────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

# ─── FMP Base URL ──────────────────────────────────────────────────────────────
_BASE_V3 = "https://financialmodelingprep.com/api/v3"

# ─── In-memory cache store ─────────────────────────────────────────────────────
_cache: Dict[str, Dict[str, Any]] = {}


class FMPClient:
    """
    Rate-limited Financial Modeling Prep API client.
    Uses only the v3 endpoints confirmed to work on the 250 calls/day plan.

    Usage:
        client = FMPClient()
        rates  = client.get_fx_rates()          # all forex pairs
        rate   = client.get_fx_pair("EURUSD")   # single pair
    """

    def __init__(self):
        try:
            from config import settings as _s
            self.api_key          = getattr(_s, "FMP_API_KEY", "") or ""
            self.max_daily_calls  = getattr(_s, "FMP_MAX_DAILY_CALLS", 50)
            self.fx_cache_min     = getattr(_s, "FMP_FX_CACHE_MINUTES", 120)  # 2h refresh
        except Exception:
            self.api_key         = os.getenv("FMP_API_KEY", "")
            self.max_daily_calls = 50
            self.fx_cache_min    = 120

        self._counter_file = os.path.join(
            _ROOT,
            f"fmp_calls_{datetime.now().strftime('%Y-%m-%d')}.json",
        )
        self._daily_count = self._load_counter()

        if not self.api_key:
            logger.warning("[FMP] FMP_API_KEY is not set — client will be inactive.")
        else:
            logger.debug(
                f"[FMP] Client ready. Key: {self.api_key[:6]}… | "
                f"Calls today: {self._daily_count}/{self.max_daily_calls}"
            )

    # ── Counter helpers ────────────────────────────────────────────────────────

    def _load_counter(self) -> int:
        try:
            if os.path.exists(self._counter_file):
                with open(self._counter_file, "r") as f:
                    data = json.load(f)
                    return int(data.get("calls", 0))
        except Exception:
            pass
        return 0

    def _save_counter(self) -> None:
        try:
            with open(self._counter_file, "w") as f:
                json.dump({
                    "calls": self._daily_count,
                    "date":  datetime.now().strftime("%Y-%m-%d"),
                }, f)
        except Exception as e:
            logger.debug(f"[FMP] Counter save failed: {e}")

    def _can_call(self) -> bool:
        if not self.api_key:
            return False
        if self._daily_count >= self.max_daily_calls:
            logger.warning(
                f"[FMP] Daily call budget exhausted "
                f"({self._daily_count}/{self.max_daily_calls})."
            )
            return False
        return True

    def _record_call(self) -> None:
        self._daily_count += 1
        self._save_counter()

    # ── HTTP helper ────────────────────────────────────────────────────────────

    def _get(self, path: str, params: Optional[Dict[str, str]] = None) -> Optional[Any]:
        """GET /api/v3/{path} with rate-limit guard and error handling."""
        if not self._can_call():
            return None

        query = dict(params or {})
        query["apikey"] = self.api_key
        url = f"{_BASE_V3}/{path.lstrip('/')}?{urllib.parse.urlencode(query)}"

        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                raw = resp.read().decode("utf-8")
                self._record_call()
                data = json.loads(raw)
                logger.debug(
                    f"[FMP] GET {path} → {self._daily_count} calls used today"
                )
                return data
        except Exception as e:
            logger.warning(f"[FMP] Request failed ({path}): {e}")
            return None

    # ── Cache helper ───────────────────────────────────────────────────────────

    def _cache_get(self, key: str, ttl_minutes: int) -> Optional[Any]:
        entry = _cache.get(key)
        if entry and (datetime.now(timezone.utc) - entry["ts"]).total_seconds() < ttl_minutes * 60:
            return entry["data"]
        return None

    def _cache_set(self, key: str, data: Any) -> None:
        _cache[key] = {"data": data, "ts": datetime.now(timezone.utc)}

    # ── Public endpoints ───────────────────────────────────────────────────────

    def get_fx_rates(self) -> List[Dict[str, Any]]:
        """
        Fetches live FX rates for all major pairs from FMP.
        Cached for FMP_FX_CACHE_MINUTES (default 120 min).

        Returns list of dicts:
            {ticker, bid, ask, open, low, high, changes, date}
        e.g. ticker="EUR/USD", changes=-0.0025 (today's drift)
        """
        cached = self._cache_get("fx_rates", self.fx_cache_min)
        if cached is not None:
            return cached

        data = self._get("fx")

        if isinstance(data, list) and data:
            self._cache_set("fx_rates", data)
            logger.info(f"[FMP] FX rates fetched: {len(data)} pairs")
            return data

        logger.debug("[FMP] FX rates returned no data")
        return []

    def get_fx_pair(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetches live rate for a single forex pair.
        symbol: MT5-style e.g. "EURUSD" — normalized to "EURUSD" for FMP.

        Returns dict or None.
        """
        # Normalize: MT5 uses "EURUSD", FMP uses "EURUSD" in v3/fx/{pair}
        clean = symbol.upper().rstrip("MCZ")  # strip Exness suffixes m/c/z
        cache_key = f"fx_pair_{clean}"
        cached = self._cache_get(cache_key, self.fx_cache_min)
        if cached is not None:
            return cached

        data = self._get(f"fx/{clean}")

        if isinstance(data, list) and data:
            result = data[0]
            self._cache_set(cache_key, result)
            return result
        return None

    def get_high_volatility_pairs(self, threshold_pct: float = 0.5) -> List[str]:
        """
        Returns a list of FMP tickers currently moving more than threshold_pct%
        (absolute daily change). Useful for knowing which markets are hot.

        Uses the cached fx_rates — no extra API call if already fresh.
        """
        rates = self.get_fx_rates()
        hot = []
        for r in rates:
            try:
                change_pct = abs(float(r.get("changesPercentage", r.get("changes", 0))))
                if change_pct >= threshold_pct:
                    hot.append(r.get("ticker", r.get("name", "")))
            except (TypeError, ValueError):
                continue
        return hot

    # ── Utility ────────────────────────────────────────────────────────────────

    @property
    def calls_remaining_today(self) -> int:
        return max(0, self.max_daily_calls - self._daily_count)

    @property
    def is_active(self) -> bool:
        return bool(self.api_key) and self._daily_count < self.max_daily_calls


# ─── Module-level singleton ────────────────────────────────────────────────────
_shared_client: Optional[FMPClient] = None


def get_client() -> FMPClient:
    """Returns (or creates) the module-level singleton FMPClient."""
    global _shared_client
    if _shared_client is None:
        _shared_client = FMPClient()
    return _shared_client
