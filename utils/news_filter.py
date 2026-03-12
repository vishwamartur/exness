"""
News Event Filter — Avoids trading during high-impact economic events.

Fetches live calendar data from ForexFactory JSON endpoint with 4-hour
thread-safe caching.  Falls back to a hardcoded schedule when the live
feed is unreachable.

Key events to avoid (±30 minutes around release):
- NFP (Non-Farm Payrolls): First Friday of each month, 13:30 UTC
- FOMC Rate Decision: ~8 times/year, 19:00 UTC
- CPI (Consumer Price Index): Mid-month, 13:30 UTC
- ECB Rate Decision: ~8 times/year, 12:45 UTC
- BOE Rate Decision: ~8 times/year, 12:00 UTC
"""

import threading
from datetime import datetime, timezone, timedelta


# Buffer in minutes before/after high-impact news to avoid
NEWS_BUFFER_MINUTES = 30

# ─── Thread-safe live calendar cache ─────────────────────────────────────
_CALENDAR_CACHE = {"data": [], "fetched_at": None}
_CACHE_LOCK = threading.Lock()

# ─── Hardcoded fallback schedule ─────────────────────────────────────────
HIGH_IMPACT_EVENTS = [
    {
        'name': 'NFP',
        'description': 'Non-Farm Payrolls',
        'day_of_week': 4,  # Friday
        'week_of_month': 1,
        'hour': 13, 'minute': 30,
        'affected': ['USD'],
        'buffer_minutes': 45,
    },
    {
        'name': 'FOMC',
        'description': 'FOMC Rate Decision',
        'day_of_week': 2,
        'week_of_month': None,
        'hour': 19, 'minute': 0,
        'affected': ['USD'],
        'buffer_minutes': 60,
    },
    {
        'name': 'US_CPI',
        'description': 'US CPI Release',
        'day_of_week': None,
        'week_of_month': 2,
        'hour': 13, 'minute': 30,
        'affected': ['USD'],
        'buffer_minutes': 30,
    },
    {
        'name': 'ECB',
        'description': 'ECB Rate Decision',
        'day_of_week': 3,
        'week_of_month': None,
        'hour': 12, 'minute': 45,
        'affected': ['EUR'],
        'buffer_minutes': 45,
    },
    {
        'name': 'BOE',
        'description': 'BOE Rate Decision',
        'day_of_week': 3,
        'week_of_month': None,
        'hour': 12, 'minute': 0,
        'affected': ['GBP'],
        'buffer_minutes': 45,
    },
]

DAILY_AVOID_WINDOWS = [
    {
        'name': 'US_Open_Volatility',
        'start_hour': 13, 'start_minute': 25,
        'end_hour': 13, 'end_minute': 35,
        'affected': ['USD'],
        'description': 'US economic data releases (13:30 UTC)',
    },
]


# ─── Helpers ─────────────────────────────────────────────────────────────

def _get_week_of_month(date):
    """Returns the week of month (1-5) for a given date."""
    return (date.day - 1) // 7 + 1


def _strip_suffix(symbol):
    """Strips Exness suffixes from symbol."""
    for suffix in ['m', 'c']:
        if symbol.endswith(suffix) and len(symbol) > 3:
            base = symbol[:-len(suffix)]
            if len(base) >= 6:
                return base
    return symbol


def _extract_currencies(symbol):
    """Extract base and quote currency codes from a symbol (e.g. EURUSD -> [EUR, USD])."""
    base = _strip_suffix(symbol).upper()
    currencies = []
    if len(base) >= 6:
        currencies.append(base[:3])
        currencies.append(base[3:6])
    elif len(base) >= 3:
        currencies.append(base[:3])
    return currencies


def _symbol_has_currency(symbol, currency):
    """Check if a currency is part of a symbol pair."""
    base = _strip_suffix(symbol).upper()
    return currency in base


# ─── Live Forex Factory Calendar ─────────────────────────────────────────

def _fetch_calendar():
    """
    Fetch and cache ForexFactory calendar JSON.  Thread-safe.
    Returns list of high-impact events with parsed datetimes.
    """
    try:
        from config import settings
        cache_hours = getattr(settings, 'NEWS_CACHE_HOURS', 4)
        url = getattr(settings, 'NEWS_CALENDAR_URL',
                      'https://nfs.faireconomy.media/ff_calendar_thisweek.json')
    except Exception:
        cache_hours = 4
        url = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'

    with _CACHE_LOCK:
        now = datetime.now(timezone.utc)

        # Return cache if still fresh
        if (_CALENDAR_CACHE["fetched_at"] is not None and
                (now - _CALENDAR_CACHE["fetched_at"]).total_seconds() < cache_hours * 3600):
            return _CALENDAR_CACHE["data"]

        try:
            import requests
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            raw = resp.json()

            parsed = []
            import re as _re
            for ev in raw:
                if ev.get('impact', '').lower() != 'high':
                    continue
                try:
                    dt_str = ev.get('date', '')
                    # Normalise timezone offset for fromisoformat (e.g. -0500 -> -05:00)
                    dt_str = _re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', dt_str)
                    dt = datetime.fromisoformat(dt_str).astimezone(timezone.utc)
                    parsed.append({
                        'name': ev.get('title', 'Unknown'),
                        'currency': ev.get('country', '').upper(),
                        'dt_utc': dt,
                    })
                except Exception:
                    continue

            _CALENDAR_CACHE["data"] = parsed
            _CALENDAR_CACHE["fetched_at"] = now
            print(f"[NEWS] Fetched {len(parsed)} high-impact events from ForexFactory")
            return parsed
        except Exception as e:
            print(f"[NEWS] Calendar fetch failed: {e} — using fallback")
            # Return whatever we have cached (may be stale or empty)
            return _CALENDAR_CACHE["data"]


# ─── Public API ──────────────────────────────────────────────────────────

def is_news_blackout(symbol, now_utc=None):
    """
    Returns (True, event_name) if we should avoid trading this symbol
    due to upcoming or ongoing high-impact news.
    Checks live Forex Factory feed first, then falls back to hardcoded schedule.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    try:
        from config import settings
        pre_mins = getattr(settings, 'NEWS_PRE_MINUTES', 15)
        post_mins = getattr(settings, 'NEWS_POST_MINUTES', 15)
    except Exception:
        pre_mins = 15
        post_mins = 15

    buffer_pre = timedelta(minutes=pre_mins)
    buffer_post = timedelta(minutes=post_mins)

    # ── Live Feed Check ──────────────────────────────────────────────────
    try:
        for ev in _fetch_calendar():
            if _symbol_has_currency(symbol, ev['currency']):
                if (ev['dt_utc'] - buffer_pre) <= now_utc <= (ev['dt_utc'] + buffer_post):
                    return True, f"FF:{ev['name']}"
    except Exception:
        pass

    # ── Hardcoded Schedule Fallback ──────────────────────────────────────
    return _hardcoded_blackout_check(symbol, now_utc)


def _hardcoded_blackout_check(symbol, now_utc=None):
    """Check hardcoded event schedule.  Used as fallback when live feed is empty."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    for window in DAILY_AVOID_WINDOWS:
        start = now_utc.replace(hour=window['start_hour'], minute=window['start_minute'], second=0)
        end = now_utc.replace(hour=window['end_hour'], minute=window['end_minute'], second=0)
        if start <= now_utc <= end:
            for currency in window['affected']:
                if _symbol_has_currency(symbol, currency):
                    return True, window['name']

    for event in HIGH_IMPACT_EVENTS:
        affects_symbol = any(_symbol_has_currency(symbol, c) for c in event['affected'])
        if not affects_symbol:
            continue
        if event['day_of_week'] is not None and now_utc.weekday() != event['day_of_week']:
            continue
        if event['week_of_month'] is not None:
            if _get_week_of_month(now_utc) != event['week_of_month']:
                continue
        event_time = now_utc.replace(hour=event['hour'], minute=event['minute'], second=0)
        buffer = timedelta(minutes=event.get('buffer_minutes', NEWS_BUFFER_MINUTES))
        if (event_time - buffer) <= now_utc <= (event_time + buffer):
            return True, event['name']

    return False, ""


def get_upcoming_events(symbol, now_utc=None, lookahead_hours=24):
    """
    Returns a list of upcoming high-impact events for the given symbol
    within the next ``lookahead_hours`` hours.

    Each item is a dict: {'name': str, 'currency': str, 'dt_utc': datetime}
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    cutoff = now_utc + timedelta(hours=lookahead_hours)
    currencies = _extract_currencies(symbol)

    upcoming = []
    for ev in _fetch_calendar():
        if ev['currency'] not in currencies:
            continue
        if now_utc <= ev['dt_utc'] <= cutoff:
            upcoming.append(ev)

    upcoming.sort(key=lambda e: e['dt_utc'])
    return upcoming


def get_active_events(now_utc=None):
    """Returns list of currently active/upcoming news events (live + hardcoded)."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    active = []

    # Live feed
    try:
        from config import settings
        pre = timedelta(minutes=getattr(settings, 'NEWS_PRE_MINUTES', 15))
        post = timedelta(minutes=getattr(settings, 'NEWS_POST_MINUTES', 15))
        for ev in _fetch_calendar():
            if (ev['dt_utc'] - pre) <= now_utc <= (ev['dt_utc'] + post):
                active.append(f"FF:{ev['name']}")
    except Exception:
        pass

    # Hardcoded fallback
    for event in HIGH_IMPACT_EVENTS:
        if event['day_of_week'] is not None and now_utc.weekday() != event['day_of_week']:
            continue
        if event['week_of_month'] is not None:
            if _get_week_of_month(now_utc) != event['week_of_month']:
                continue
        event_time = now_utc.replace(hour=event['hour'], minute=event['minute'], second=0)
        buffer = timedelta(minutes=event.get('buffer_minutes', NEWS_BUFFER_MINUTES))
        if (event_time - buffer) <= now_utc <= (event_time + buffer):
            active.append(event['name'])

    return active
