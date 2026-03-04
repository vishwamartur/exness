
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

def debug_scan_multi_tf():
    print("Initializing MT5...")
    client = MT5Client()
    if not client.connect():
        print("MT5 Connect Failed")
        return

    print("Initializing Strategy...")
    strategy = InstitutionalStrategy(client)
    # We skip loading models for speed if just checking technicals, 
    # but we need ML for full score.
    if not strategy.load_model():
        print("Model load failed (using random fallback for debug)")
    
    symbols = ["EURUSD", "GBPUSD", "XAUUSD"]
    timeframes = ["M1", "M5"]
    
    print(f"\nDebugging {symbols} on {timeframes}...\n")

    for tf in timeframes:
        print(f"\n=== TIMEFRAME {tf} ===")
        # Override Global Setting
        settings.TIMEFRAME = tf
        
        for symbol in symbols:
            print(f"--- {symbol} ({tf}) ---")
            
            # 1. Fetch Data
            try:
                # _fetch_symbol_data uses settings.TIMEFRAME
                data = strategy._fetch_symbol_data(symbol)
                if not data:
                    print("  No data fetched.")
                    continue
                # The dictionary keys are fixed M15, H1, H4 in _fetch_symbol_data?
                # Let's check. If TIMEFRAME is M1, it likely returns 'M1' key?
                # Or uses the variable settings.TIMEFRAME as key?
                # If _fetch_symbol_data returns {settings.TIMEFRAME: df, ...}
                # We need to access it correctly.
                
                df = data.get(tf)
                if df is None:
                    # Fallback: maybe it returns 'M15' hardcoded key?
                    # Let's check keys
                    keys = list(data.keys())
                    if len(keys) > 0:
                        df = data[keys[0]] # Assume first key is primary
                        print(f"  Using key: {keys[0]}")
                    else:
                        print("  Empty data dict")
                        continue
            except Exception:
                traceback.print_exc()
                continue
                
            # 2. Features
            try:
                df_features = features.add_technical_features(df)
                last = df_features.iloc[-1]
            except Exception as e:
                print(f"  Feature error: {e}")
                continue

            # 3. Check ML Prob
            ml_prob = 0.5
            try:
                rf_prob, _ = strategy.quant._get_rf_prediction(df_features)
                ml_prob = rf_prob 
                if strategy.quant.xgb_model:
                    xgb_prob, _ = strategy.quant._get_xgb_prediction(df_features)
                    ml_prob = (rf_prob + xgb_prob) / 2
            except Exception as e:
                print(f"  ML Calc Error: {e}")

            # 4. H4 Trend
            h4_trend = 0
            try:
                h4_trend = strategy.get_h4_trend(symbol)
            except Exception:
                pass
            
            # 5. Score
            try:
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
                print(f"  ML Prob: {ml_prob:.4f}")
                print(f"  Buy:  {b_score} {b_details}")
                print(f"  Sell: {s_score} {s_details}")
                
                best_score = max(b_score, s_score)
                msg = "REJECTED"
                if best_score >= settings.MIN_CONFLUENCE_SCORE:
                   msg = "VALID"
                elif best_score >= 2 and (ml_prob > 0.85 or ml_prob < 0.15):
                   msg = "VALID (ML Boost)"
                
                print(f"  -> {msg} (Score {best_score})")

            except Exception:
                traceback.print_exc()

    client.shutdown()

if __name__ == "__main__":
    debug_scan_multi_tf()
