
import sys
import os
import pandas as pd
import traceback
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
        try:
            data = strategy._fetch_symbol_data(symbol)
            if not data:
                print("  No data fetched.")
                continue
        except Exception:
            traceback.print_exc()
            continue
            
        df = data[settings.TIMEFRAME] # Dynamic key match
        
        # 2. Features
        try:
            df_features = features.add_technical_features(df)
            last = df_features.iloc[-1]
        except Exception as e:
            print(f"  Feature error: {e}")
            continue

        # 3. Check ML Prob (Directly)
        try:
            rf_prob, _ = strategy._get_rf_prediction(df_features)
            # xgb_prob, _ = strategy._get_xgb_prediction(df_features) # Skip XGB if causing issues
            ml_prob = rf_prob 
            if strategy.xgb_model:
                try:
                    xgb_prob, _ = strategy._get_xgb_prediction(df_features)
                    ml_prob = (rf_prob + xgb_prob) / 2
                except Exception as e:
                    print(f"  XGB Error: {e}")
            print(f"  ML Prob (Calculated): {ml_prob:.4f}")
        except Exception as e:
            print(f"  ML Calc Error: {e}")
            ml_prob = 0.5

        # 4. H4 Trend
        h4_trend = 0
        try:
            h4_trend = strategy.get_h4_trend(symbol)
        except Exception:
            pass
        
        # 5. Score
        try:
            # Explicit kwargs to avoid TypeError
            b_score, b_details = strategy._calculate_confluence(
                symbol=symbol, 
                df_features=df_features, 
                direction="buy", 
                h4_trend=h4_trend
            )
            s_score, s_details = strategy._calculate_confluence(
                symbol=symbol, 
                df_features=df_features, 
                direction="sell", 
                h4_trend=h4_trend
            )
            
            print(f"  Close: {last['close']:.5f}")
            print(f"  H4 Trend: {h4_trend}")
            print(f"  Buy Score:  {b_score} {b_details}")
            print(f"  Sell Score: {s_score} {s_details}")
            
            best_score = max(b_score, s_score)
            
            # Replicate the Logic
            is_valid = False
            if best_score >= settings.MIN_CONFLUENCE_SCORE:
                is_valid = True
                print(f"  -> VALID (Standard Score >= {settings.MIN_CONFLUENCE_SCORE})")
            elif best_score >= 2 and (ml_prob > 0.85 or ml_prob < 0.15):
                is_valid = True
                print(f"  -> VALID (ML Boost Override: Score {best_score} >= 2 & Strong ML)")
            else:
                print(f"  -> REJECTED. Score {best_score}. ML {ml_prob:.4f}")
                if best_score >= 2:
                    print(f"     (Failed ML Boost check: Need > 0.85 or < 0.15)")

        except Exception:
            print("  Score Calculation Crashed:")
            traceback.print_exc()

    client.shutdown()

if __name__ == "__main__":
    debug_scan()
