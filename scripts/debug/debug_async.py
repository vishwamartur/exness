
import asyncio
import sys
import os
from config import settings
from execution.mt5_client import MT5Client
from strategy.institutional_strategy import InstitutionalStrategy
from api.stream_server import start_server, push_update

# Force Debug
settings.LOG_LEVEL = "DEBUG"

async def debug_async():
    print("=== DEBUG ASYNC ARCHITECTURE ===")
    
    client = MT5Client()
    if not client.connect(): return
    
    # Detect Symbols
    if not client.detect_available_symbols():
        print("No symbols found.")
        return

    # Start Server
    try:
        start_server()
        await asyncio.sleep(2) # Wait for server start
    except: pass

    strategy = InstitutionalStrategy(client, on_event=push_update)
    
    # Run scan loop (5 iterations for Dashboard test)
    print("Starting Scan Loop (5 iterations)...")
    for i in range(5):
        print(f"\n--- Iteration {i+1}/5 ---")
        try:
            await strategy.run_scan_loop()
        except Exception as e:
            print(f"Loop Error: {e}")
        
        await asyncio.sleep(5) # Small pause
    
    client.shutdown()

if __name__ == "__main__":
    asyncio.run(debug_async())
