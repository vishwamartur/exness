
import sys
import os
import pandas as pd
import MetaTrader5 as mt5

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import settings
from analysis.quant_agent import QuantAgent
from analysis.market_analyst import MarketAnalyst
from execution.mt5_client import MT5Client

async def debug_trend(symbol):
    print("[DEBUG] importing client...", flush=True)
    try:
        client = MT5Client()
        print("[DEBUG] client instantiated.", flush=True)
    except Exception as e:
        print(f"[ERROR] Client init failed: {e}", flush=True)
        return

    print("[DEBUG] connecting to mt5...", flush=True)
    if not client.connect():
        print("Failed to connect to MT5", flush=True)
        return
    print("[DEBUG] connected.", flush=True)

    quant = QuantAgent()
    analyst = MarketAnalyst()
    
    print(f"\n[DEBUG] Analyzing Trend for {symbol}...", flush=True)
    
    # Fetch Data
    # usage: get_data(symbol, n_bars=1000) - returns dict of DFs
    # Wait, MarketAnalyst.get_analysis_data returns the dict needed by QuantAgent
    # Let's verify what PairAgent does. PairAgent calls:
    # data_dict = self.analyst.get_analysis_data(self.symbol)
    
    # We need to mimic that.
    # But MarketAnalyst.get_analysis_data might be async or sync? 
    # Checking code... it is usually synchronous in this project or mixed.
    # Let's check MarketAnalyst source if needed, but likely sync.
    
    # Actually, let's just use raw client to get data and pass to quant to be sure we see what it sees.
    # Quant expects a dict with keys: 'M15' (TIMEFRAME), 'H1', 'H4'
    
    data_dict = {}
    for tf_name, tf_const in [('M15', mt5.TIMEFRAME_M15), ('H1', mt5.TIMEFRAME_H1), ('H4', mt5.TIMEFRAME_H4)]:
        rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, 1000)
        if rates is not None:
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            data_dict[tf_name] = df
        else:
            print(f"Failed to get {tf_name} data", flush=True)
            
    # Run Analysis
    # We need to map TIMEFRAME settings. default M15.
    settings.TIMEFRAME = 'M15' 
    
    # 1. Check Trend Calculation Directly
    h1_trend = quant._compute_trend(data_dict.get('H1'))
    h4_trend = quant._compute_trend(data_dict.get('H4'))
    
    h1_df = data_dict.get('H1')
    if h1_df is not None:
        h1_close = h1_df['close'].iloc[-1]
        h1_sma = h1_df['close'].rolling(50).mean().iloc[-1]
        
        print(f"\n--- {symbol} TREND DIAGNOSTICS ---", flush=True)
        print(f"Current Price (H1 Close): {h1_close:.5f}", flush=True)
        print(f"H1 SMA (50): {h1_sma:.5f}", flush=True)
        print(f"Diff: {h1_close - h1_sma:.5f}", flush=True)
        print(f"H1 Trend Result: {h1_trend} (1=UP, -1=DOWN, 0=FLAT)", flush=True)
        print(f"H4 Trend Result: {h4_trend}", flush=True)
    else:
        print("H1 Data missing!", flush=True)
    
    # 2. Check Quantification
    q_res = quant.analyze(symbol, data_dict)
    if q_res:
        print(f"\n--- QUANT RESULT ---", flush=True)
        print(f"Direction: {q_res['direction']}", flush=True)
        print(f"Score: {q_res['score']}", flush=True)
        print(f"ML Probability: {q_res['ml_prob']:.2f}", flush=True)
        print(f"Details: {q_res['details']}", flush=True)
        
        # Check if ML prob is inverted?
        # In _calculate_confluence:
        # Buy if prob > 0.85 (or threshold)
        # Sell if prob < 0.15 (or 1-threshold)
        # This implies prob is "Probability of BUY"??
        # Usually ML models target[1] is UP.
        pass
    else:
        print("Quant Analysis Failed", flush=True)

import asyncio
import traceback

if __name__ == "__main__":
    try:
        asyncio.run(debug_trend('EURUSD'))
    except Exception as e:
        print(f"CRASH: {e}", flush=True)
        traceback.print_exc()
