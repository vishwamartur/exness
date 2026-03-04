import MetaTrader5 as mt5
import sys
import os
import random

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import settings
from execution.mt5_client import MT5Client

def place_random_trade():
    print("--- Placing Random LIVE Trade ---")
    
    # 1. Connect
    client = MT5Client()
    if not client.connect():
        print("FAIL: Could not connect to MT5.")
        return

    # 2. Random Direction
    direction = random.choice([mt5.ORDER_TYPE_BUY, mt5.ORDER_TYPE_SELL])
    dir_str = "BUY" if direction == mt5.ORDER_TYPE_BUY else "SELL"
    
    print(f"Attempting Market {dir_str} on {settings.SYMBOL}...")
    
    # 3. Execute
    # Using small SL/TP for safety
    result = client.place_order(direction, stop_loss_pips=10, take_profit_pips=10)
    
    if result:
        print(f"SUCCESS: Trade executed! Ticket: {result.order}")
        print("You should see this in your MT5 Terminal.")
    else:
        print("FAIL: Trade execution failed.")
        
    client.shutdown()

if __name__ == "__main__":
    place_random_trade()
