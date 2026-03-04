
import sys
import os
import pandas as pd
from execution.mt5_client import MT5Client
from strategy.institutional_strategy import InstitutionalStrategy
from strategy import features

def debug_ml():
    print("Initializing...")
    client = MT5Client()
    if not client.connect(): return
    strategy = InstitutionalStrategy(client)
    strategy.load_model()
    
    symbol = "EURUSD"
    print(f"Fetching {symbol}...")
    data = strategy._fetch_symbol_data(symbol)
    if not data:
        print("No data")
        return
        
    df = data['M15']
    df_features = features.add_technical_features(df)
    
    print("Predicting...")
    rf_prob, _ = strategy.quant._get_rf_prediction(df_features)
    print(f"RF Prob: {rf_prob}")
    
    if strategy.quant.xgb_model:
        xgb_prob, _ = strategy.quant._get_xgb_prediction(df_features)
        print(f"XGB Prob: {xgb_prob}")

    print("Checking H4 Trend...")
    try:
        h4_trend = strategy.get_h4_trend(symbol)
        print(f"H4 Trend: {h4_trend}")
    except Exception as e:
        print(f"H4 Error: {e}")
    
    client.shutdown()

if __name__ == "__main__":
    debug_ml()
