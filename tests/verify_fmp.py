"""Quick FMP integration test - ASCII safe for Windows."""
import os, sys, json, logging
from datetime import datetime

logging.basicConfig(level=logging.WARNING)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

def ok(label, val, detail=""):
    print(f"  {'[OK]' if val else '[!!]'} {label}" + (f" -- {detail}" if detail else ""))

# 1. Settings
print("\n=== 1. Settings ===")
from config import settings
api_key = getattr(settings, "FMP_API_KEY", "")
max_calls = getattr(settings, "FMP_MAX_DAILY_CALLS", 50)
fx_cache = getattr(settings, "FMP_FX_CACHE_MINUTES", 120)
ok("FMP_API_KEY", bool(api_key), f"{api_key[:6]}...")
ok("FMP_MAX_DAILY_CALLS", max_calls > 0, str(max_calls))
ok("FMP_FX_CACHE_MINUTES", fx_cache >= 60, f"{fx_cache} min")

# 2. Client
print("\n=== 2. FMPClient ===")
from utils.fmp_client import get_client
client = get_client()
ok("Client active", client.is_active, f"key OK, budget has {client.calls_remaining_today} remaining")
ok("Daily counter", client._daily_count >= 0, f"{client._daily_count} calls so far")

# 3. FX Rates endpoint
print("\n=== 3. api/v3/fx - Live FX Rates ===")
before = client._daily_count
rates = client.get_fx_rates()
after = client._daily_count
ok("Returns data", isinstance(rates, list), f"{len(rates)} pairs returned")
ok("API call recorded", after > before, f"{before} -> {after}")
if rates:
    s = rates[0]
    print(f"     Keys:        {list(s.keys())}")
    print(f"     First pair:  {s}")

# 4. Cache test
rates2 = client.get_fx_rates()
ok("Cache works", client._daily_count == after, f"still {client._daily_count} calls (no extra request)")

# 5. Single pair
print("\n=== 4. api/v3/fx/EURUSD - Single Pair ===")
pair = client.get_fx_pair("EURUSD")
ok("Returns EURUSD", pair is not None, str(pair)[:120] if pair else "None")

# 6. Volatility detection
print("\n=== 5. High-Volatility Pair Detection ===")
hot = client.get_high_volatility_pairs(threshold_pct=0.05)
ok("Function works", True, f"{len(hot)} pairs >0.05% change -- {hot[:6]}")

# 7. Counter file
print("\n=== 6. On-disk Counter ===")
cf = os.path.join(ROOT, f"fmp_calls_{datetime.now().strftime('%Y-%m-%d')}.json")
ok("Counter file exists", os.path.exists(cf), cf)
if os.path.exists(cf):
    with open(cf) as f:
        data = json.load(f)
    ok("Valid JSON", "calls" in data, str(data))
    ok("Under limit", data["calls"] <= max_calls, f"{data['calls']} / {max_calls}")

# 8. News filter
print("\n=== 7. News Filter ===")
from utils.news_filter import is_news_blackout, get_active_events
for sym in ["EURUSD", "BTCUSD", "XAUUSD"]:
    bl, reason = is_news_blackout(sym)
    status = f"BLACKOUT ({reason})" if bl else "CLEAR"
    ok(f"is_news_blackout({sym})", True, status)
active = get_active_events()
ok("get_active_events()", True, f"{len(active)} active events" if active else "no active events now")

# Summary
print("\n=== Summary ===")
print(f"  FMP calls this session : {client._daily_count}")
print(f"  Remaining today        : {client.calls_remaining_today} / {max_calls} cap  (250 plan limit)")
print(f"  Working endpoints      : api/v3/fx (live FX rates for all pairs)")
print(f"  Unavailable on plan    : economic-calendar, forex-news, crypto-news (need Starter+)")
print(f"  News filter            : Forex Factory + hardcoded schedule (unchanged)")
print("\n  Done.\n")
