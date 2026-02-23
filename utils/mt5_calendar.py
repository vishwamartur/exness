"""
MT5 Economic Calendar Reader
============================
Reads the JSON file written by CalendarExport.mq5 EA running inside MetaTrader 5.

The EA uses native MQL5 CalendarValueHistory() / CalendarEventById() /
CalendarCountryById() — the same real-time data the MT5 terminal shows in its
own calendar tab. No API keys, no rate limits, no external network calls.

JSON schema (written by CalendarExport.mq5):
  [
    {
      "id":             840030016,
      "name":           "Non-Farm Payrolls",
      "country":        "US",
      "currency":       "USD",
      "time_utc":       "2026-02-07 13:30:00",
      "importance":     3,
      "importance_str": "high",
      "actual":         143.000,
      "forecast":       170.000,
      "previous":       256.000
    }, ...
  ]

Usage:
    from utils.mt5_calendar import get_client
    cal = get_client()
    events = cal.get_upcoming_events(hours_ahead=24)
    in_blackout, name = cal.is_high_impact_window("USD")
"""

from __future__ import annotations

import json
import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

# ─── In-memory cache ───────────────────────────────────────────────────────────
_cache: Dict[str, Any] = {"events": [], "loaded_at": None}


def _find_calendar_file() -> str:
    """
    Locate calendar_events.json.
    Priority:
      1. MT5_CALENDAR_FILE env / settings override
      2. Standard MT5 Files folder locations (Windows)
      3. Project root (if EA writes relative path there)
    """
    try:
        from config import settings
        override = getattr(settings, "MT5_CALENDAR_FILE", "")
        if override and os.path.exists(override):
            return override
    except Exception:
        pass

    env_override = os.getenv("MT5_CALENDAR_FILE", "")
    if env_override and os.path.exists(env_override):
        return env_override

    # Common MT5 Files folder locations on Windows
    candidates = [
        # Current user's MT5 installation (most common)
        os.path.join(os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal"),
        # Alternative for portable/other installs
        r"C:\Program Files\MetaTrader 5",
        r"C:\Program Files (x86)\MetaTrader 5",
    ]

    for base in candidates:
        if not os.path.isdir(base):
            continue
        # Walk one level deep — MT5 uses a hash subfolder per terminal instance
        try:
            for entry in os.scandir(base):
                if not entry.is_dir():
                    continue
                candidate = os.path.join(entry.path, "MQL5", "Files", "calendar_events.json")
                if os.path.exists(candidate):
                    logger.info(f"[MT5Cal] Found calendar file: {candidate}")
                    return candidate
        except Exception:
            continue

    # Fallback: project root (if MT5 DataPath points there)
    local = os.path.join(_ROOT, "calendar_events.json")
    return local  # Returned even if missing — caller checks existence


class MT5CalendarReader:
    """
    Reads the MQL5-written calendar JSON and provides event query methods.

    The file is refreshed by CalendarExport.mq5 every ~60 seconds.
    This reader caches in memory for cache_ttl_sec seconds to avoid
    re-parsing on every call (hot path in news_filter).
    """

    def __init__(self):
        try:
            from config import settings as _s
            self.cache_ttl_sec  = getattr(_s, "MT5_CALENDAR_CACHE_SEC",   55)
            self.min_importance = getattr(_s, "MT5_CALENDAR_MIN_IMPACT",    3)
            self.hours_ahead    = getattr(_s, "MT5_CALENDAR_HOURS_AHEAD",  24)
        except Exception:
            self.cache_ttl_sec  = 55
            self.min_importance = 3
            self.hours_ahead    = 24

        self._file = _find_calendar_file()
        self._events: List[Dict[str, Any]] = []
        self._loaded_at: Optional[datetime] = None

        if os.path.exists(self._file):
            logger.info(f"[MT5Cal] Calendar file: {self._file}")
        else:
            logger.warning(
                f"[MT5Cal] Calendar file not found: {self._file}\n"
                "  --> Attach CalendarExport.mq5 to any chart in MT5 to generate it."
            )

    # ── File loading ───────────────────────────────────────────────────────────

    def _load(self) -> List[Dict[str, Any]]:
        """Load (or return cached) calendar events from file."""
        now = datetime.now(timezone.utc)

        if (self._loaded_at is not None and
                (now - self._loaded_at).total_seconds() < self.cache_ttl_sec):
            return self._events

        if not os.path.exists(self._file):
            return self._events  # return stale if file missing

        try:
            with open(self._file, "r", encoding="utf-8") as f:
                raw = json.load(f)

            parsed = []
            for ev in raw:
                try:
                    # Parse "2026-02-22 13:30:00" → UTC datetime
                    dt = datetime.strptime(
                        ev["time_utc"], "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=timezone.utc)
                    parsed.append({
                        "id":             ev.get("id"),
                        "name":           ev.get("name", "Unknown"),
                        "currency":       ev.get("currency", ""),
                        "country":        ev.get("country", ""),
                        "dt_utc":         dt,
                        "importance":     int(ev.get("importance", 0)),
                        "importance_str": ev.get("importance_str", ""),
                        "actual":         ev.get("actual"),
                        "forecast":       ev.get("forecast"),
                        "previous":       ev.get("previous"),
                    })
                except Exception:
                    continue

            self._events    = parsed
            self._loaded_at = now

            # Check file age
            file_age_min = (now.timestamp() - os.path.getmtime(self._file)) / 60
            if file_age_min > 5:
                logger.warning(
                    f"[MT5Cal] Calendar file is {file_age_min:.1f} min old. "
                    "Is CalendarExport.mq5 running in MT5?"
                )
            else:
                logger.debug(f"[MT5Cal] Loaded {len(parsed)} events (file age: {file_age_min:.1f} min)")

        except Exception as e:
            logger.warning(f"[MT5Cal] Failed to read calendar file: {e}")

        return self._events

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        """True if the calendar file exists and was recently written by the EA."""
        if not os.path.exists(self._file):
            return False
        age_sec = datetime.now().timestamp() - os.path.getmtime(self._file)
        return age_sec < 300  # file must be < 5 minutes old

    def get_all_events(self, min_importance: Optional[int] = None) -> List[Dict[str, Any]]:
        """Returns all events from the file (no time filter)."""
        imp = min_importance if min_importance is not None else 1
        return [e for e in self._load() if e["importance"] >= imp]

    def get_upcoming_events(
        self,
        hours_ahead: Optional[int] = None,
        hours_behind: float = 1.0,
        min_importance: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns events within a time window around now.
        Default: 1 hour behind → 24 hours ahead, high-impact only.
        """
        now = datetime.now(timezone.utc)
        hrs = hours_ahead if hours_ahead is not None else self.hours_ahead
        imp = min_importance if min_importance is not None else self.min_importance

        start = now - timedelta(hours=hours_behind)
        end   = now + timedelta(hours=hrs)

        return [
            e for e in self._load()
            if e["importance"] >= imp and start <= e["dt_utc"] <= end
        ]

    def is_high_impact_window(
        self,
        currency: str,
        now_utc: Optional[datetime] = None,
        pre_min: int = 15,
        post_min: int = 15,
        min_importance: int = 3,
    ) -> Tuple[bool, str]:
        """
        Returns (True, event_name) if currency has a high-impact event
        within [now - pre_min, now + post_min].
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        pre  = timedelta(minutes=pre_min)
        post = timedelta(minutes=post_min)
        currency = currency.upper()

        for ev in self._load():
            if ev["importance"] < min_importance:
                continue
            if ev["currency"].upper() != currency:
                continue
            if (ev["dt_utc"] - pre) <= now_utc <= (ev["dt_utc"] + post):
                return True, ev["name"]

        return False, ""

    def get_events_for_currency(
        self,
        currency: str,
        hours_ahead: int = 24,
        min_importance: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """All upcoming events affecting a given currency."""
        now = datetime.now(timezone.utc)
        imp = min_importance if min_importance is not None else self.min_importance
        end = now + timedelta(hours=hours_ahead)
        currency = currency.upper()

        return [
            e for e in self._load()
            if e["currency"].upper() == currency
            and e["importance"] >= imp
            and e["dt_utc"] >= now
            and e["dt_utc"] <= end
        ]

    def get_today_summary(self) -> List[Dict[str, Any]]:
        """All events for today UTC, sorted by time, all impacts."""
        now   = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end   = start + timedelta(days=1)
        evs   = [e for e in self._load() if start <= e["dt_utc"] < end]
        return sorted(evs, key=lambda e: e["dt_utc"])

    @property
    def file_path(self) -> str:
        return self._file

    @property
    def event_count(self) -> int:
        return len(self._load())


# ─── Module-level singleton ────────────────────────────────────────────────────
_shared: Optional[MT5CalendarReader] = None


def get_client() -> MT5CalendarReader:
    """Returns (or creates) the module-level singleton MT5CalendarReader."""
    global _shared
    if _shared is None:
        _shared = MT5CalendarReader()
    return _shared
