import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta

def check_orders():
    if not mt5.initialize():
        print("MT5 init failed, error code:", mt5.last_error())
        return

    print("=== MT5 DEAL & ORDER HISTORY ===")
    
    # Get active pending orders
    orders = mt5.orders_get()
    if orders is None:
        print("No pending orders or error:", mt5.last_error())
    elif len(orders) > 0:
        print(f"Active Pending Orders ({len(orders)}):")
        for order in orders:
            print(f"- {order.ticket}: {order.symbol} Type:{order.type} Vol:{order.volume_initial} Price:{order.price_open} State:{order.state}")
    else:
        print("No Active Pending Orders.")

    # Get recent history
    from_date = datetime.now() - timedelta(hours=6)
    history = mt5.history_orders_get(from_date, datetime.now())
    
    if history is None:
        print("No history found, error:", mt5.last_error())
    elif len(history) > 0:
        print(f"\nRecent Order History ({len(history)} items). Showing last 10:")
        # Sort by time
        history_list = sorted(list(history), key=lambda x: x.time_setup)
        for order in history_list[-10:]:
            
            # Translate Type
            types = {0:"Buy", 1:"Sell", 2:"BuyLimit", 3:"SellLimit", 4:"BuyStop", 5:"SellStop"}
            o_type = types.get(order.type, order.type)
            
            # Translate Reason
            reasons = {0:"Client", 1:"Expert", 2:"Dealer", 3:"SL", 4:"TP", 5:"SO", 6:"Rollover"}
            o_reason = reasons.get(order.reason, order.reason)
            
            # Translate State
            states = {0:"Started", 1:"Placed", 2:"Canceled", 3:"Partial", 4:"Filled", 5:"Rejected", 6:"Expired"}
            o_state = states.get(order.state, order.state)
            
            print(f"- [{datetime.fromtimestamp(order.time_setup)}] {order.ticket}: {order.symbol} {o_type} | State: {o_state} | Reason: {o_reason} | Prc: {order.price_open}")
    else:
        print("\nNo recent order history (6 hours).")
        
    mt5.shutdown()

if __name__ == "__main__":
    check_orders()
