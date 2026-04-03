import MetaTrader5 as mt5
import sys

# Connect
if not mt5.initialize():
    print("initialize() failed")
    sys.exit()

symbol = "XAUUSDm"
if not mt5.market_book_add(symbol):
    print(f"market_book_add({symbol}) failed, error code =", mt5.last_error())
    # Try another symbol if XAUUSDm fails
    symbol = "EURUSDm"
    if not mt5.market_book_add(symbol):
         print(f"market_book_add({symbol}) failed, error code =", mt5.last_error())
    
print(f"Subscribed to {symbol}")

# Get book
book = mt5.market_book_get(symbol)
if book is None:
    print(f"market_book_get returned None, error code =", mt5.last_error())
else:
    print(f"Got {len(book)} records for {symbol}")
    for item in book[:5]:
        print(item)

mt5.market_book_release(symbol)
mt5.shutdown()
