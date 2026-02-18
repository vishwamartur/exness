
import sys
import os
import pandas as pd
import traceback
from config import settings
from execution.mt5_client import MT5Client
from strategy.institutional_strategy import InstitutionalStrategy

settings.LOG_LEVEL = "DEBUG"

import asyncio

async def debug_scan():
    print("Initializing MT5...")
    client = MT5Client()
    if not client.connect():
        print("MT5 Connect Failed")
        return

    print("Initializing Strategy...")
    strategy = InstitutionalStrategy(client)
    
    symbols = ["EURUSD", "GBPUSD", "XAUUSD"]
    print(f"\nDebugging {symbols}...\n")

    for symbol in symbols:
        print(f"--- ANALYZING {symbol} ---")
        
        # 1. Fetch Data
        try:
            from market_data import loader
            from utils.async_utils import run_in_executor
            
            # Fetch data using loader
            df = await run_in_executor(loader.get_historical_data, symbol, settings.TIMEFRAME, 500)
            
            if df is None or df.empty:
                print(f"  No data fetched for {symbol}.")
                continue
                
            # Prepare data dict for QuantAgent
            data = {settings.TIMEFRAME: df}
            
            # Add H1 if enabled (simulating PairAgent)
            if settings.H1_TREND_FILTER:
                 h1 = await run_in_executor(loader.get_historical_data, symbol, "H1", 100)
                 if h1 is not None:
                    data['H1'] = h1

        except Exception:
            traceback.print_exc()
            continue
            
        # 2. Quant Analysis
        try:
            # Check if analyze is async (it likely is or will be)
            q_res = strategy.quant.analyze(symbol, data)
            if asyncio.iscoroutine(q_res):
                q_res = await q_res
                
            if not q_res:
                print("  Quant Analysis Failed.")
                continue
                
            print(f"  Score: {q_res.get('score', 'N/A')} {q_res.get('direction', 'N/A')}")
            print(f"  Score: {q_res.get('score', 'N/A')} {q_res.get('direction', 'N/A')}")
            print(f"  ML Prob: {q_res.get('ml_prob', 0):.4f}")
            print(f"  Details: {q_res.get('details', {})}")
            print(f"  H4 Trend: {q_res.get('h4_trend')} | H1 Trend: {q_res.get('details', {}).get('H1')}")
            print(f"  ADX: {q_res.get('features', {}).get('adx', 0):.2f}")
            
            # 3. Analyst Analysis
            a_res = strategy.analyst.analyze_session(symbol, q_res.get('data'))
            if asyncio.iscoroutine(a_res):
                a_res = await a_res
                
            print(f"  Regime: {a_res.get('regime', 'unknown')}")
            
            # Validation Logic
            # Check if _get_adaptive_threshold is async
            # Check if _get_adaptive_threshold is async
            threshold = settings.MIN_CONFLUENCE_SCORE
            # if asyncio.iscoroutine(threshold):
            #     threshold = await threshold

            is_valid = False
            score = q_res.get('score', 0)
            ml_prob = q_res.get('ml_prob', 0.5)

            if score >= threshold: 
                is_valid = True
                print(f"  -> VALID (Score {score} >= {threshold})")
            elif score >= 2 and (ml_prob > 0.85 or ml_prob < 0.15):
                is_valid = True
                print(f"  -> VALID (ML Boost)")
            else:
                print(f"  -> REJECTED")
 
        except Exception:
            print("  Analysis Crashed:")
            print(traceback.format_exc())

    client.shutdown()

if __name__ == "__main__":
    asyncio.run(debug_scan())
