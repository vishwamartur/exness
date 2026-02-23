"""Probe FMP to find genuinely accessible endpoints on the 250 calls/day plan."""
import urllib.request, json, urllib.parse, sys

KEY = 'N0xBMAcSBraGCUhdpswMTAolAUtSLuA9'
BASE = 'https://financialmodelingprep.com'

endpoints = [
    # v3 legacy endpoints (often more open)
    ('api/v3/fx',                        {}),
    ('api/v3/quotes/forex',              {}),
    ('api/v3/fx/EURUSD',                 {}),
    ('api/v3/quote/EURUSD',              {}),
    ('api/v3/profile/AAPL',              {}),
    ('api/v3/income-statement/AAPL',     {'limit': '1', 'period': 'annual'}),
    ('api/v3/historical-price-full/AAPL',{'serietype': 'line', 'from': '2026-01-01', 'to': '2026-02-01'}),
    # stable endpoints
    ('stable/economic-calendar',         {'from': '2026-02-22', 'to': '2026-02-23'}),
    ('stable/news/forex-latest',         {'page': '0', 'limit': '3'}),
    ('stable/news/crypto-latest',        {'page': '0', 'limit': '3'}),
    ('stable/news/general-latest',       {'page': '0', 'limit': '3'}),
    ('stable/profile',                   {'symbol': 'AAPL'}),
    ('stable/forex-list',                {}),
    ('stable/symbol-list',               {}),
    ('stable/search',                    {'query': 'EUR', 'limit': '3'}),
]

ok_count = 0
for ep, params in endpoints:
    p = dict(params)
    p['apikey'] = KEY
    url = BASE + '/' + ep + '?' + urllib.parse.urlencode(p)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            raw = r.read()
            data = json.loads(raw)
            if isinstance(data, list):
                print(f'[OK  ] {ep}  ->  list  len={len(data)}')
                if data and isinstance(data[0], dict):
                    print(f'         keys: {list(data[0].keys())[:8]}')
                ok_count += 1
            elif isinstance(data, dict):
                if set(data.keys()) & {'error', 'Error', 'message', 'Message'}:
                    print(f'[ERR ] {ep}  ->  {data}')
                else:
                    print(f'[OK  ] {ep}  ->  dict  keys={list(data.keys())[:8]}')
                    ok_count += 1
    except Exception as e:
        print(f'[FAIL] {ep}  ->  {e}')

print(f'\n{ok_count} endpoints available.')
