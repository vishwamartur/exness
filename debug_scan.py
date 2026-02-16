
import sys
import os
import pandas as pd
from datetime import datetime
from config import settings
from execution.mt5_client import MT5Client
from strategy.institutional_strategy import InstitutionalStrategy
from strategy import features

# Force verbose logging
settings.LOG_LEVEL = "DEBUG"

def debug_scan():
    print("Initializing MT5...")
    client = MT5Client()
    if not client.connect():
        print("MT5 Connect Failed")
        return

    print("Initializing Strategy...")
    strategy = InstitutionalStrategy(client)
    if not strategy.load_model():
        print("Model load failed (using random fallback for debug)")
    
    symbols = ["EURUSD", "GBPUSD", "XAUUSD"]
    print(f"\nDebugging {symbols}...\n")

    for symbol in symbols:
        print(f"--- ANALYZING {symbol} ---")
        
        # 1. Fetch Data
        data = strategy._fetch_symbol_data(symbol)
        if not data:
            print("  No data fetched.")
            continue
            
        df = data['M15']
        h1 = data['H1']
        h4 = data['H4']
        
        # 2. Features
        try:
            df_features = features.add_technical_features(df)
            last = df_features.iloc[-1]
        except Exception as e:
            print(f"  Feature error: {e}")
            continue

        # 3. ML Prob
        ml_prob = 0.5
        # features = strategy.prepare_features(df_features) # This method might not exist public
        # We need to replicate how strategy gets prediction
        try:
            rf_prob, _ = strategy._get_rf_prediction(df_features)
            xgb_prob, _ = strategy._get_xgb_prediction(df_features)
            ml_prob = (rf_prob + xgb_prob) / 2 if strategy.xgb_model else rf_prob
        except Exception as e:
            print(f"  ML Error: {e}")

        # 4. H4 Trend
        h4_trend = strategy.get_h4_trend(symbol)
        
        # 5. Score
        buy_score, sell_score, details = strategy._calculate_confluence(
            symbol, df_features, direction="buy", h4_trend=h4_trend
        ) 
        # Wait, _calculate_confluence takes direction arg? 
        # signature: (self, symbol, df_features, direction="buy", h1_trend=None, h4_trend=None)
        
        # We need to call it twice
        b_score, b_details = strategy._calculate_confluence(symbol, df_features, "buy", h4_trend=h4_trend)
        s_score, s_details = strategy._calculate_confluence(symbol, df_features, "sell", h4_trend=h4_trend)
        
        print(f"  Close: {last['close']:.5f}")
        print(f"  H4 Trend: {h4_trend}")
        print(f"  ML Prob:  {ml_prob:.2f} (Threshold: {settings.RF_PROB_THRESHOLD})")
        print(f"  Buy Score:  {b_score} {b_details}")
        print(f"  Sell Score: {s_score} {s_details}")
        
        best_score = max(b_score, s_score)
        
        if best_score < settings.MIN_CONFLUENCE_SCORE:
            print(f"  -> REJECTED: Score {best_score} < Min {settings.MIN_CONFLUENCE_SCORE}")
        elif ml_prob < settings.RF_PROB_THRESHOLD:
             # Check if ML was the reason for low score?
             # Actually calculate_confluence adds point if ml_prob > threshold
             pass
        else:
            print(f"  -> CANDIDATE! (Pending Mistral check)")

    client.shutdown()

if __name__ == "__main__":
    debug_scan()
