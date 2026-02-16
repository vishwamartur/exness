
import sys
import os
import pandas as pd
import traceback
from config import settings
from execution.mt5_client import MT5Client
from strategy.institutional_strategy import InstitutionalStrategy

settings.LOG_LEVEL = "DEBUG"

def debug_scan():
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
            data = strategy._fetch_symbol_data(symbol)
            if not data:
                print("  No data fetched (Cooldown/News/Spread?).")
                continue
        except Exception:
            traceback.print_exc()
            continue
            
        # 2. Quant Analysis
        try:
            q_res = strategy.quant.analyze(symbol, data)
            if not q_res:
                print("  Quant Analysis Failed.")
                continue
                
            print(f"  Score: {q_res['score']} {q_res['direction']}")
            print(f"  ML Prob: {q_res['ml_prob']:.4f}")
            print(f"  Details: {q_res['details']}")
            
            # 3. Analyst Analysis
            a_res = strategy.analyst.analyze_session(symbol, q_res['data'])
            print(f"  Regime: {a_res['regime']}")
            
            # Validation Logic
            threshold = strategy._get_adaptive_threshold()
            is_valid = False
            if q_res['score'] >= threshold: 
                is_valid = True
                print(f"  -> VALID (Score >= {threshold})")
            elif q_res['score'] >= 2 and (q_res['ml_prob'] > 0.85 or q_res['ml_prob'] < 0.15):
                is_valid = True
                print(f"  -> VALID (ML Boost)")
            else:
                print(f"  -> REJECTED")

        except Exception:
            print("  Analysis Crashed:")
            traceback.print_exc()

    client.shutdown()

if __name__ == "__main__":
    debug_scan()
