"""
News Event Filter -- Avoids trading during high-impact economic events.

Data sources (evaluated in order, best -> fallback):
  1. MQL5 Economic Calendar  -- MT5 built-in calendar via CalendarExport.mq5 EA
  2. Forex Factory JSON feed -- this-week calendar from nfs.faireconomy.media
  3. Hardcoded schedule      -- known recurring events (NFP, FOMC, CPI, etc.)

MQL5 Calendar setup:
  - Compile mql5/CalendarExport.mq5 in MetaEditor
  - Attach to any chart (e.g. EURUSD M1) in MT5
  - It writes calendar_events.json to MT5's Files folder every 60 seconds
  - Python auto-detects the file path via utils.mt5_calendar._find_calendar_file()
  - If MT5 is closed, falls back to sources 2 & 3 automatically

Key events to avoid (+-15-45 min buffer around release):
- NFP (Non-Farm Payrolls): First Friday of each month, 13:30 UTC
- FOMC Rate Decision: ~8 times/year, 19:00 UTC
- CPI (Consumer Price Index): Mid-month, 13:30 UTC
- ECB Rate Decision: ~8 times/year, 12:45 UTC
- BOE Rate Decision: ~8 times/year, 12:00 UTC
"""

from datetime import datetime, timezone, timedelta


# Buffer in minutes before/after high-impact news to avoid
NEWS_BUFFER_MINUTES = 30

# Recurring high-impact events (UTC)
# Format: {'day_of_week': 0-6 (Mon-Sun), 'hour': UTC hour, 'minute': UTC minute,
#           'week_of_month': 1-5 (approx), 'affected_pairs': [...]}
HIGH_IMPACT_EVENTS = [
    {
        'name': 'NFP',
        'description': 'Non-Farm Payrolls',
        'day_of_week': 4,  # Friday
        'week_of_month': 1,  # First week
        'hour': 13, 'minute': 30,
        'affected': ['USD'],  # Affects all USD pairs
        'buffer_minutes': 45,
    },
    {
        'name': 'FOMC',
        'description': 'FOMC Rate Decision',
        'day_of_week': 2,  # Wednesday (usually)
        'week_of_month': None,  # Varies — checked monthly
        'hour': 19, 'minute': 0,
        'affected': ['USD'],
        'buffer_minutes': 60,
    },
    {
        'name': 'US_CPI',
        'description': 'US CPI Release',
        'day_of_week': None,  # Varies
        'week_of_month': 2,  # Second week typically
        'hour': 13, 'minute': 30,
        'affected': ['USD'],
        'buffer_minutes': 30,
    },
    {
        'name': 'ECB',
        'description': 'ECB Rate Decision',
        'day_of_week': 3,  # Thursday
        'week_of_month': None,
        'hour': 12, 'minute': 45,
        'affected': ['EUR'],
        'buffer_minutes': 45,
    },
    {
        'name': 'BOE',
        'description': 'BOE Rate Decision',
        'day_of_week': 3,  # Thursday
        'week_of_month': None,
        'hour': 12, 'minute': 0,
        'affected': ['GBP'],
        'buffer_minutes': 45,
    },
]

# Daily recurring high-impact windows (always active)
DAILY_AVOID_WINDOWS = [
    {
        'name': 'US_Open_Volatility',
        'start_hour': 13, 'start_minute': 25,
        'end_hour': 13, 'end_minute': 35,
        'affected': ['USD'],
        'description': 'US economic data releases (13:30 UTC)',
    },
]


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


def _symbol_has_currency(symbol, currency):
    """Check if a currency is part of a symbol pair."""
    base = _strip_suffix(symbol).upper()
    return currency in base


# ─── Live Forex Factory Calendar ─────────────────────────────────────────────
_ff_cache = {'events': [], 'fetched_at': None}

def _fetch_forex_factory_events():
    """
    Fetches this week's high-impact events from the Forex Factory JSON feed.
    Caches for settings.NEWS_CALENDAR_CACHE_MINUTES to avoid excessive requests.
    Returns list of dicts: {name, currency, dt_utc}
    """
    try:
        from config import settings
        import urllib.request, json, time as _time

        cache_mins = getattr(settings, 'NEWS_CALENDAR_CACHE_MINUTES', 60)
        now = datetime.now(timezone.utc)

        if (_ff_cache['fetched_at'] is not None and
                (now - _ff_cache['fetched_at']).total_seconds() < cache_mins * 60):
            return _ff_cache['events']

        url = getattr(settings, 'NEWS_CALENDAR_URL',
                      'https://nfs.faireconomy.media/ff_calendar_thisweek.json')
        with urllib.request.urlopen(url, timeout=5) as resp:
            raw = json.loads(resp.read().decode())

        parsed = []
        for ev in raw:
            if ev.get('impact', '').lower() != 'high':
                continue
            try:
                # Format: "2024-02-21T13:30:00-0500" or similar
                dt_str = ev.get('date', '')
                # Normalise timezone offset format for fromisoformat
                import re as _re
                dt_str = _re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', dt_str)
                dt = datetime.fromisoformat(dt_str).astimezone(timezone.utc)
                parsed.append({
                    'name': ev.get('title', 'Unknown'),
                    'currency': ev.get('country', '').upper(),
                    'dt_utc': dt,
                })
            except Exception:
                continue

        _ff_cache['events'] = parsed
        _ff_cache['fetched_at'] = now
        return parsed
    except Exception:
        return _ff_cache.get('events', [])


def is_news_blackout(symbol, now_utc=None):
    """
    Returns (True, event_name) if trading this symbol should be avoided
    due to upcoming or ongoing high-impact news.

    Check order:
      1. MQL5 Calendar (MT5 built-in, via CalendarExport.mq5 shared JSON)
      2. Forex Factory JSON feed (live, this-week calendar)
      3. Hardcoded schedule (always-available fallback)
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    try:
        from config import settings
        pre  = timedelta(minutes=getattr(settings, 'NEWS_PRE_MINUTES', 15))
        post = timedelta(minutes=getattr(settings, 'NEWS_POST_MINUTES', 15))
        min_imp = getattr(settings, 'MT5_CALENDAR_MIN_IMPACT', 3)
    except Exception:
        pre  = timedelta(minutes=15)
        post = timedelta(minutes=15)
        min_imp = 3

    # -- 1. MQL5 Economic Calendar (MT5 terminal native data) ------------------
    try:
        from utils.mt5_calendar import get_client as _mt5cal
        cal = _mt5cal()
        if cal.is_available:
            for ev in cal.get_all_events(min_importance=min_imp):
                if _symbol_has_currency(symbol, ev['currency']):
                    if (ev['dt_utc'] - pre) <= now_utc <= (ev['dt_utc'] + post):
                        return True, f"MT5:{ev['name']}"
    except Exception:
        pass

    # -- 2. Forex Factory Live Feed --------------------------------------------
    try:
        for ev in _fetch_forex_factory_events():
            if _symbol_has_currency(symbol, ev['currency']):
                if (ev['dt_utc'] - pre) <= now_utc <= (ev['dt_utc'] + post):
                    return True, f"FF:{ev['name']}"
    except Exception:
        pass

    # -- 3. Hardcoded Schedule Fallback ----------------------------------------
    for window in DAILY_AVOID_WINDOWS:
        start = now_utc.replace(hour=window['start_hour'], minute=window['start_minute'], second=0)
        end   = now_utc.replace(hour=window['end_hour'],   minute=window['end_minute'],   second=0)
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


def get_active_events(now_utc=None):
    """Returns list of currently active/upcoming news events (all sources)."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    active = []

    try:
        from config import settings
        pre     = timedelta(minutes=getattr(settings, 'NEWS_PRE_MINUTES', 15))
        post    = timedelta(minutes=getattr(settings, 'NEWS_POST_MINUTES', 15))
        min_imp = getattr(settings, 'MT5_CALENDAR_MIN_IMPACT', 3)
    except Exception:
        pre     = timedelta(minutes=15)
        post    = timedelta(minutes=15)
        min_imp = 3

    # MQL5 Calendar
    try:
        from utils.mt5_calendar import get_client as _mt5cal
        cal = _mt5cal()
        if cal.is_available:
            for ev in cal.get_all_events(min_importance=min_imp):
                if (ev['dt_utc'] - pre) <= now_utc <= (ev['dt_utc'] + post):
                    active.append(f"MT5:{ev['name']}")
    except Exception:
        pass

    # Forex Factory live feed
    try:
        for ev in _fetch_forex_factory_events():
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

