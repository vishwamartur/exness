
import sys
import os
import time
import pandas as pd
from datetime import datetime
from config import settings
from execution.mt5_client import MT5Client
from strategy.institutional_strategy import InstitutionalStrategy
from strategy import features

# Force Debug
settings.LOG_LEVEL = "DEBUG"

def debug_main():
    print("=== DEBUG MAIN LOOP ===")
    
    client = MT5Client()
    if not client.connect():
        print("Connect Failed")
        return

    strategy = InstitutionalStrategy(client)
    if not strategy.load_model():
        print("Model load failed (using fallback)")
    
    # We will loop explicitly for one symbol to trace it
    symbol = settings.SYMBOL # "EURUSD"
    print(f"Tracing {symbol} on {settings.TIMEFRAME}...")
    
    for i in range(5):
        print(f"\n--- Iteration {i+1} ---")
        
        # 1. Fetch
        data = strategy._fetch_symbol_data(symbol)
        if not data:
            print("No data.")
            continue
            
        df = data[settings.TIMEFRAME]
        # 2. Features
        df = features.add_technical_features(df)
        
        # 3. Score
        last = df.iloc[-1]
        h4_trend = 0 # Assume 0 for debug if H4 fetch fails
        try:
             h4_trend = strategy.get_h4_trend(symbol)
        except: pass
        
        print(f"Price: {last['close']}, H4: {h4_trend}")
        
        # Calculate manually
        b_score, b_details = strategy._calculate_confluence(symbol, df, "buy", h4_trend=h4_trend)
        s_score, s_details = strategy._calculate_confluence(symbol, df, "sell", h4_trend=h4_trend)
        
        print(f"Buy Score: {b_score} {b_details}")
        print(f"Sell Score: {s_score} {s_details}")
        
        best = max(b_score, s_score)
        direction = "BUY" if b_score >= s_score else "SELL"
        
        # 4. Check setup logic (Copied from strategy.scan_all_markets -> _score_symbol)
        # We need to see if _score_symbol RETURNS a setup
        # But _score_symbol calls Mistral.
        
        # Let's call strategy.scan() for just this symbol?
        # strategy.scan_all_markets() scans ALL.
        
        # Let's inject a fake high score to force execution path check?
        # No, let's see what the ACTUAL score is.
        
        ml_prob = 0.5
        # Extract ML prob from details if possible, or recalc
        # The details dict has 'ML': '...0.05'
        
        print(f"Requirements: Min Score {settings.MIN_CONFLUENCE_SCORE}")
        
        is_setup = False
        if best >= settings.MIN_CONFLUENCE_SCORE:
            is_setup = True
        elif best >= 2:
            # Check ML boost
            # internal logic:
            # ml_prob is needed.
            rf_prob, _ = strategy._get_rf_prediction(df)
            if direction=="BUY" and rf_prob > 0.85: is_setup = True
            if direction=="SELL" and rf_prob < 0.15: is_setup = True
            print(f"ML Probe Check: {rf_prob} -> Setup? {is_setup}")
            
        if is_setup:
            print(">>> SETUP DETECTED! Executing trade logic...")
            
            # Construct setup dict
            setup = {
                'symbol': symbol,
                'direction': direction,
                'score': best,
                'sl_distance': 0.0020, # Mock
                'tp_distance': 0.0040
            }
            
            # 5. Calling _execute_trade
            print("Calling _execute_trade...")
            strategy._execute_trade(setup)
        else:
            print("No Setup.")
            
        time.sleep(2)

    client.shutdown()

if __name__ == "__main__":
    debug_main()
