"""
News Event Filter — Avoids trading during high-impact economic events.

Uses investing.com/forex-factory-style calendar data.
Since we can't reliably scrape calendars in real-time, this uses a
schedule-based approach for known recurring high-impact events.

Key events to avoid (±30 minutes around release):
- NFP (Non-Farm Payrolls): First Friday of each month, 13:30 UTC
- FOMC Rate Decision: ~8 times/year, 19:00 UTC
- CPI (Consumer Price Index): Mid-month, 13:30 UTC
- ECB Rate Decision: ~8 times/year, 12:45 UTC
- BOE Rate Decision: ~8 times/year, 12:00 UTC

Also, optional full-disable for Asian session low-liquidity periods.
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


def is_news_blackout(symbol, now_utc=None):
    """
    Returns (True, event_name) if we should avoid trading this symbol
    due to upcoming or ongoing high-impact news.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    # Check daily recurring windows
    for window in DAILY_AVOID_WINDOWS:
        start = now_utc.replace(hour=window['start_hour'], minute=window['start_minute'], second=0)
        end = now_utc.replace(hour=window['end_hour'], minute=window['end_minute'], second=0)

        if start <= now_utc <= end:
            for currency in window['affected']:
                if _symbol_has_currency(symbol, currency):
                    return True, window['name']

    # Check scheduled events
    for event in HIGH_IMPACT_EVENTS:
        # Check if this event affects this symbol
        affects_symbol = False
        for currency in event['affected']:
            if _symbol_has_currency(symbol, currency):
                affects_symbol = True
                break

        if not affects_symbol:
            continue

        # Check day of week
        if event['day_of_week'] is not None and now_utc.weekday() != event['day_of_week']:
            continue

        # Check week of month (approximate)
        if event['week_of_month'] is not None:
            if _get_week_of_month(now_utc) != event['week_of_month']:
                continue

        # Check time proximity
        event_time = now_utc.replace(hour=event['hour'], minute=event['minute'], second=0)
        buffer = timedelta(minutes=event.get('buffer_minutes', NEWS_BUFFER_MINUTES))

        if (event_time - buffer) <= now_utc <= (event_time + buffer):
            return True, event['name']

    return False, ""


def get_active_events(now_utc=None):
    """Returns list of currently active/upcoming news events."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    active = []
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
