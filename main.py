import time
import sys
import os
from datetime import datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import settings
from execution.mt5_client import MT5Client
from strategy.scalping_strategy import ScalpingStrategy

def main():
    print("Starting MT5 ML Scalping Bot (Multi-Pair Mode)...")
    print(f"Trading Pairs: {settings.SYMBOLS}")
    
    # 1. Initialize Client
    client = MT5Client()
    if not client.connect():
        print("Could not connect to MT5. Exiting...")
        return

    # 2. Initialize Strategy
    strategy = ScalpingStrategy(client)
    if not strategy.load_model():
        print("Could not load model. Run train_model.py first! Exiting...")
        client.shutdown()
        return
        
    print(f"Listening for ticks...")
    
    try:
        while True:
            cycle_start = time.time()
            
            # Iterate over all symbols
            for symbol in settings.SYMBOLS:
                # Run strategy logic for this symbol
                strategy.check_market(symbol)
            
            print(f"--- Cycle Complete: {datetime.now().strftime('%H:%M:%S')} ---")
            
            # Smart sleep: Try to maintain constant cycle time or minimum sleep
            elapsed = time.time() - cycle_start
            sleep_time = max(1.0, 10.0 - elapsed) # Aim for 10s cycles
            time.sleep(sleep_time) 
            
    except KeyboardInterrupt:
        print("\nStopping bot...")
    finally:
        client.shutdown()
        print("MT5 connection closed.")

if __name__ == "__main__":
    main()
