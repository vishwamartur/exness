
import sys
import os
from config import settings
from execution.mt5_client import MT5Client
from strategy.institutional_strategy import InstitutionalStrategy

# Force Debug
settings.LOG_LEVEL = "DEBUG"

def debug_researcher():
    print("=== DEBUG RESEARCHER AGENT ===")
    
    client = MT5Client()
    if not client.connect(): return

    strategy = InstitutionalStrategy(client)
    
    symbol = "EURUSD" # Test symbol
    print(f"Analyzing {symbol}...")
    
    # 1. Fetch & Quant
    data = strategy._fetch_symbol_data(symbol)
    if not data:
        print("No data.")
        return

    q_res = strategy.quant.analyze(symbol, data)
    if not q_res:
        print("Quant failed.")
        return
        
    a_res = strategy.analyst.analyze_session(symbol, q_res['data'])
    
    print(f"Quant Score: {q_res['score']}")
    print(f"Analyst Regime: {a_res['regime']}")
    
    # 2. Call Researcher
    print("\n[RESEARCHER] Conducting Debate...")
    try:
        # Mock analyst data structure for researcher
        analyst_mock = {'regime': a_res['regime']}
        # Attributes are just q_res
        
        research = strategy.researcher.conduct_research(symbol, q_res, analyst_mock)
        
        print(f"\n--> Action: {research['action']}")
        print(f"--> Confidence: {research['confidence']}%")
        print(f"--> Reason: {research['reason']}")
        
    except Exception as e:
        print(f"Researcher Error: {e}")

    client.shutdown()

if __name__ == "__main__":
    debug_researcher()
