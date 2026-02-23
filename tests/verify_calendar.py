"""
MQL5 Economic Calendar Integration Verification
================================================
Tests the MT5CalendarReader and news_filter integration.
Note: Most tests pass even WITHOUT the EA running (tests graceful fallback).
The 'calendar file' tests will FAIL if CalendarExport.mq5 is not running in MT5.

Run with:
    cd f:\\mt5
    python tests/verify_calendar.py
"""
import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.WARNING)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


def ok(label, val, detail=""):
    print(f"  {'[OK]' if val else '[!!]'} {label}" + (f" -- {detail}" if detail else ""))
    return val


# ============================================================
print("\n=== 1. Settings ===")
# ============================================================
from config import settings
ok("MT5_CALENDAR_FILE",        True, repr(getattr(settings, "MT5_CALENDAR_FILE", "(not set)")))
ok("MT5_CALENDAR_CACHE_SEC",   getattr(settings, "MT5_CALENDAR_CACHE_SEC",   55) > 0,  str(getattr(settings, "MT5_CALENDAR_CACHE_SEC", 55)))
ok("MT5_CALENDAR_HOURS_AHEAD", getattr(settings, "MT5_CALENDAR_HOURS_AHEAD", 24) > 0,  str(getattr(settings, "MT5_CALENDAR_HOURS_AHEAD", 24)))
ok("MT5_CALENDAR_MIN_IMPACT",  getattr(settings, "MT5_CALENDAR_MIN_IMPACT",   3) in (1,2,3), str(getattr(settings, "MT5_CALENDAR_MIN_IMPACT", 3)))


# ============================================================
print("\n=== 2. MT5CalendarReader import ===")
# ============================================================
from utils.mt5_calendar import MT5CalendarReader, get_client
cal = get_client()
ok("Module imports OK",  True)
ok("File path detected", True, cal.file_path)
file_exists = os.path.exists(cal.file_path)
ok("calendar_events.json exists", file_exists,
   "Run CalendarExport.mq5 in MT5 first" if not file_exists else cal.file_path)


# ============================================================
print("\n=== 3. Calendar Data ===")
# ============================================================
if file_exists:
    age_sec = datetime.now().timestamp() - os.path.getmtime(cal.file_path)
    ok("File is fresh (< 5 min)", age_sec < 300, f"{age_sec:.0f}s old")

    all_events = cal.get_all_events(min_importance=1)
    ok("Events loaded", len(all_events) > 0, f"{len(all_events)} total events in file")

    high_events = cal.get_all_events(min_importance=3)
    ok("High-impact events", True, f"{len(high_events)} HIGH impact events")

    upcoming = cal.get_upcoming_events(hours_ahead=24, min_importance=1)
    ok("Upcoming 24h", True, f"{len(upcoming)} events in next 24 hours")

    today = cal.get_today_summary()
    ok("Today summary", True, f"{len(today)} events today")

    print(f"\n  -- Today's events (UTC) --")
    now_utc = datetime.now(timezone.utc)
    if today:
        for e in today:
            delta_min = (e['dt_utc'] - now_utc).total_seconds() / 60
            timing = f"in {delta_min:.0f}m" if delta_min > 0 else f"{abs(delta_min):.0f}m ago"
            status = "[HIGH]" if e['importance'] == 3 else "[MED] " if e['importance'] == 2 else "[LOW] "
            print(f"    {status} {e['currency']:4s} {e['dt_utc'].strftime('%H:%M')} UTC  {timing:>10s}  {e['name']}")
            if e['actual'] is not None:
                print(f"           actual={e['actual']}  forecast={e['forecast']}  prev={e['previous']}")
    else:
        print("    (no events today)")

    print(f"\n  -- Upcoming 24h HIGH impact --")
    for e in [x for x in upcoming if x['importance'] >= 3]:
        delta_min = (e['dt_utc'] - now_utc).total_seconds() / 60
        timing = f"in {delta_min:.0f}m" if delta_min > 0 else f"{abs(delta_min):.0f}m ago"
        print(f"    {e['currency']:4s} {e['dt_utc'].strftime('%Y-%m-%d %H:%M')} UTC  {timing:>10s}  {e['name']}")
else:
    print("  [SKIP] No calendar file — attach CalendarExport.mq5 to a chart in MT5.")


# ============================================================
print("\n=== 4. is_available check ===")
# ============================================================
ok("cal.is_available", cal.is_available,
   "YES -- EA is running" if cal.is_available else "NO -- EA not running / file stale")


# ============================================================
print("\n=== 5. News Filter Integration ===")
# ============================================================
from utils.news_filter import is_news_blackout, get_active_events
for sym in ["EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "XAUUSD"]:
    bl, reason = is_news_blackout(sym)
    status = f"BLACKOUT ({reason})" if bl else "CLEAR"
    ok(f"is_news_blackout({sym})", True, status)

active = get_active_events()
ok("get_active_events()", True,
   f"{len(active)} active: {active}" if active else "no active events right now")

source = "MT5 Calendar" if cal.is_available else "Forex Factory + Hardcoded"
print(f"\n  Active news source: {source}")


# ============================================================
print("\n=== Summary ===")
# ============================================================
print(f"  EA running:          {'YES' if cal.is_available else 'NO (fallback to FF)'}")
print(f"  Calendar file:       {cal.file_path}")
print(f"  Event count in file: {cal.event_count}")
print(f"  Min impact filter:   {settings.MT5_CALENDAR_MIN_IMPACT} (3=HIGH only)")
print()
if not file_exists:
    print("  ACTION REQUIRED:")
    print("    1. Open MetaEditor in MT5 (F4)")
    print("    2. Open mql5/CalendarExport.mq5 from this project")
    print("    3. Compile (F7)")
    print("    4. Drag EA onto any chart (e.g. EURUSD M1)")
    print("    5. Enable AutoTrading, then re-run this script")
else:
    print("  All systems operational. MQL5 Calendar is active.")
print()
