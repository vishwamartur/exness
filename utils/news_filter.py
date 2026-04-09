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
import requests
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


# ─── News Trading Opportunities ──────────────────────────────────────────

# Event classification for XAUUSD trading behavior
_EVENT_CLASSIFICATION = {
    # NFP: Huge initial spike, often mean-reverts within 30-60 min
    "NFP": {"type": "NFP", "volatility": "EXTREME", "pattern": "SPIKE_REVERT",
            "expected_move_pips": 300, "revert_probability": 0.65},
    "Non-Farm": {"type": "NFP", "volatility": "EXTREME", "pattern": "SPIKE_REVERT",
                 "expected_move_pips": 300, "revert_probability": 0.65},
    "Nonfarm": {"type": "NFP", "volatility": "EXTREME", "pattern": "SPIKE_REVERT",
                "expected_move_pips": 300, "revert_probability": 0.65},
    # FOMC: Sustained directional move, can trend for hours
    "FOMC": {"type": "FOMC", "volatility": "EXTREME", "pattern": "SUSTAINED_TREND",
             "expected_move_pips": 400, "revert_probability": 0.25},
    "Federal Funds Rate": {"type": "FOMC", "volatility": "EXTREME", "pattern": "SUSTAINED_TREND",
                           "expected_move_pips": 400, "revert_probability": 0.25},
    "Fed Interest Rate": {"type": "FOMC", "volatility": "EXTREME", "pattern": "SUSTAINED_TREND",
                          "expected_move_pips": 400, "revert_probability": 0.25},
    # CPI: Sharp spike, partial reversion, then continuation
    "CPI": {"type": "CPI", "volatility": "HIGH", "pattern": "SPIKE_CONTINUE",
            "expected_move_pips": 200, "revert_probability": 0.40},
    "Consumer Price": {"type": "CPI", "volatility": "HIGH", "pattern": "SPIKE_CONTINUE",
                       "expected_move_pips": 200, "revert_probability": 0.40},
    "Inflation Rate": {"type": "CPI", "volatility": "HIGH", "pattern": "SPIKE_CONTINUE",
                       "expected_move_pips": 200, "revert_probability": 0.40},
    # PPI: Moderate, often front-runs CPI
    "PPI": {"type": "PPI", "volatility": "MEDIUM", "pattern": "SPIKE_CONTINUE",
            "expected_move_pips": 120, "revert_probability": 0.45},
    "Producer Price": {"type": "PPI", "volatility": "MEDIUM", "pattern": "SPIKE_CONTINUE",
                       "expected_move_pips": 120, "revert_probability": 0.45},
    # GDP
    "GDP": {"type": "GDP", "volatility": "MEDIUM", "pattern": "SPIKE_CONTINUE",
            "expected_move_pips": 150, "revert_probability": 0.40},
    # Retail Sales
    "Retail Sales": {"type": "RETAIL", "volatility": "MEDIUM", "pattern": "SPIKE_REVERT",
                     "expected_move_pips": 100, "revert_probability": 0.55},
    # ISM/PMI
    "ISM": {"type": "ISM", "volatility": "MEDIUM", "pattern": "SPIKE_REVERT",
            "expected_move_pips": 100, "revert_probability": 0.50},
    # Unemployment Claims
    "Unemployment Claims": {"type": "CLAIMS", "volatility": "LOW", "pattern": "SPIKE_REVERT",
                            "expected_move_pips": 60, "revert_probability": 0.60},
    "Initial Jobless": {"type": "CLAIMS", "volatility": "LOW", "pattern": "SPIKE_REVERT",
                        "expected_move_pips": 60, "revert_probability": 0.60},
    # ECB / BOE — affect gold via USD cross-rates
    "ECB": {"type": "ECB", "volatility": "HIGH", "pattern": "SUSTAINED_TREND",
            "expected_move_pips": 150, "revert_probability": 0.30},
    "BOE": {"type": "BOE", "volatility": "MEDIUM", "pattern": "SUSTAINED_TREND",
            "expected_move_pips": 100, "revert_probability": 0.35},
}


def classify_event_impact(event_name: str) -> dict:
    """
    Classify a news event by its expected impact pattern on XAUUSD.
    Returns event type, volatility level, expected pattern, and move size.
    """
    name_upper = event_name.upper()
    for keyword, classification in _EVENT_CLASSIFICATION.items():
        if keyword.upper() in name_upper:
            return {**classification, "matched_keyword": keyword}

    # Default for unmatched high-impact events
    return {
        "type": "OTHER",
        "volatility": "MEDIUM",
        "pattern": "SPIKE_REVERT",
        "expected_move_pips": 80,
        "revert_probability": 0.50,
        "matched_keyword": None,
    }


def get_news_trade_opportunities(symbols=None, now_utc=None, lookahead_minutes=30):
    """
    Find upcoming high-impact USD news events that create XAUUSD trading
    opportunities within the lookahead window.

    Returns a list of dicts:
        {
            'name': str,
            'currency': str,
            'dt_utc': datetime,
            'minutes_until': float,
            'classification': dict,
            'tradeable_symbols': list,
        }
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if symbols is None:
        symbols = ["XAUUSD"]

    cutoff = now_utc + timedelta(minutes=lookahead_minutes)

    # Gold-relevant currencies (USD directly, EUR/GBP via cross-rate effects)
    gold_currencies = {"USD", "EUR", "GBP"}

    opportunities = []

    # Check live calendar
    for ev in _fetch_calendar():
        if ev['currency'] not in gold_currencies:
            continue
        if not (now_utc <= ev['dt_utc'] <= cutoff):
            continue

        classification = classify_event_impact(ev['name'])
        minutes_until = (ev['dt_utc'] - now_utc).total_seconds() / 60.0

        # Filter: only trade events with at least MEDIUM volatility
        if classification['volatility'] in ('LOW',):
            continue

        # Find which XAUUSD variants are available
        tradeable = []
        for sym in symbols:
            base = _strip_suffix(sym).upper()
            if "XAU" in base or "GOLD" in base:
                tradeable.append(sym)

        if not tradeable:
            continue

        opportunities.append({
            'name': ev['name'],
            'currency': ev['currency'],
            'dt_utc': ev['dt_utc'],
            'minutes_until': round(minutes_until, 1),
            'classification': classification,
            'tradeable_symbols': tradeable,
        })

    # Also check hardcoded schedule for nearby events
    for event in HIGH_IMPACT_EVENTS:
        if event.get('day_of_week') is not None and now_utc.weekday() != event['day_of_week']:
            continue
        if event.get('week_of_month') is not None:
            if _get_week_of_month(now_utc) != event['week_of_month']:
                continue

        event_time = now_utc.replace(
            hour=event['hour'], minute=event['minute'], second=0, microsecond=0
        )
        if not (now_utc <= event_time <= cutoff):
            continue

        # Check if this hardcoded event isn't already covered by live feed
        already_covered = any(
            abs((opp['dt_utc'] - event_time).total_seconds()) < 600
            for opp in opportunities
        )
        if already_covered:
            continue

        # Only USD events affect gold directly
        if 'USD' not in event.get('affected', []):
            continue

        classification = classify_event_impact(event['name'])
        minutes_until = (event_time - now_utc).total_seconds() / 60.0

        tradeable = []
        for sym in (symbols or ["XAUUSD"]):
            base = _strip_suffix(sym).upper()
            if "XAU" in base or "GOLD" in base:
                tradeable.append(sym)

        if tradeable:
            opportunities.append({
                'name': event['name'],
                'currency': 'USD',
                'dt_utc': event_time,
                'minutes_until': round(minutes_until, 1),
                'classification': classification,
                'tradeable_symbols': tradeable,
            })

    opportunities.sort(key=lambda x: x['dt_utc'])
    return opportunities


def get_pre_news_window(event_dt_utc, mode="BREAKOUT"):
    """
    Calculate the pre-news preparation window for a given event.

    For STRADDLE mode: place orders 5-15 minutes before event
    For BREAKOUT mode: start monitoring 2 minutes before, trade after first confirmed candle

    Returns (window_start, window_end, action_description)
    """
    if mode == "STRADDLE":
        window_start = event_dt_utc - timedelta(minutes=15)
        window_end = event_dt_utc - timedelta(minutes=2)
        action = "Place buy-stop/sell-stop straddle"
    else:  # BREAKOUT
        window_start = event_dt_utc - timedelta(minutes=5)
        window_end = event_dt_utc + timedelta(minutes=15)
        action = "Monitor for post-news breakout confirmation"

    return window_start, window_end, action
