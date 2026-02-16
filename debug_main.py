
import sys
import os
import time
from config import settings
from execution.mt5_client import MT5Client
from strategy.institutional_strategy import InstitutionalStrategy

# Force Debug
settings.LOG_LEVEL = "DEBUG"

def debug_main():
    print("=== DEBUG MAIN AGENT LOOP ===")
    
    client = MT5Client()
    if not client.connect(): return

    strategy = InstitutionalStrategy(client)
    
    symbol = settings.SYMBOL
    print(f"Tracing {symbol}...")
    
    for i in range(3):
        print(f"\n--- Iteration {i+1} ---")
        
        # 1. Fetch
        data = strategy._fetch_symbol_data(symbol)
        if not data:
            print("No data.")
            continue
            
        # 2. Quant
        q_res = strategy.quant.analyze(symbol, data)
        if not q_res:
            print("Quant analysis failed.")
            continue
            
        # 3. Analyst
        a_res = strategy.analyst.analyze_session(symbol, q_res['data'])
        
        print(f"Score: {q_res['score']} {q_res['direction']}")
        print(f"ML: {q_res['ml_prob']:.2f}")
        print(f"Regime: {a_res['regime']}")
        
        threshold = strategy._get_adaptive_threshold()
        print(f"Threshold: {threshold}")
        
        validated = False
        if q_res['score'] >= threshold: validated = True
        elif q_res['score'] >= 2 and (q_res['ml_prob'] > 0.85 or q_res['ml_prob'] < 0.15): validated = True
        
        if validated and a_res['regime'] != 'RANGING':
            print(">>> SETUP DETECTED!")
            # Validate logic calling strategy._execute_trade
            try:
                # Construct setup dict manually to test _execute_trade or rely on scan
                # Just print here
                print("Execution Logic Triggered")
            except Exception as e:
                print(f"Exec Error: {e}")
        else:
            print("No Setup.")
            
        time.sleep(2)

    client.shutdown()

if __name__ == "__main__":
    debug_main()
