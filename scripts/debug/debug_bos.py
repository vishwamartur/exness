import asyncio
import sys
import os
import pandas as pd
from datetime import datetime

# Adjust path
sys.path.append(os.path.abspath(os.getcwd()))

from market_data import loader
from strategy.bos_strategy import BOSStrategy
from config import settings
from utils.async_utils import run_in_executor

async def verify_bos():
    symbol = "EURUSD"
    timeframe = "M15" # Testing on M15 as per settings, though user suggested M5
    print(f"--- Verifying BOS Strategy on {symbol} {timeframe} ---")
    
    # 1. Fetch Data
    print("Fetching historical data...")
    df = await run_in_executor(loader.get_historical_data, symbol, timeframe, 1000)
    
    if df is None or len(df) < 100:
        print("Failed to fetch data.")
        return

    # 2. Run Strategy
    bos = BOSStrategy()
    
    signals = []
    
    print(f"Scanning {len(df)} candles...")
    
    rejection_stats = {}
    
    # Simulate sliding window
    for i in range(100, len(df)):
        window = df.iloc[:i+1].copy()
        
        # We need to ensure features are added to window or calculated inside
        # BOSStrategy calculates them if missing.
        
        res = bos.analyze(window)
        
        if res.get('valid'):
            timestamp = window.iloc[-1]['time']
            signals.append({
                'time': timestamp,
                'signal': res['signal'],
                'price': res['price'],
                'reason': res['reason']
            })
            print(f"[{timestamp}] {res['signal']} @ {res['price']} | {res['reason']}")
        elif res.get('reason'):
            reason = res['reason']
            rejection_stats[reason] = rejection_stats.get(reason, 0) + 1

    print(f"\nTotal Signals Found: {len(signals)}")
    
    with open('f:/mt5/rejections.txt', 'w', encoding='utf-8') as f:
        f.write("--- Rejection Summary ---\n")
        f.write(f"Total Candles Scanned: {len(df)-100}\n")
        f.write(f"Total Signals: {len(signals)}\n\n")
        for reason, count in sorted(rejection_stats.items(), key=lambda x: x[1], reverse=True):
            f.write(f"{reason}: {count}\n")
            print(f"{reason}: {count}")

    if signals:
        print("\nLast 5 Signals:")
        for s in signals[-5:]:
             print(f"{s['time']} {s['signal']} {s['price']} {s['reason']}")

if __name__ == "__main__":
    # Force settings
    settings.BOS_ENABLE = True
    asyncio.run(verify_bos())
