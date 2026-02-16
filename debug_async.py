
import asyncio
import sys
import os
from config import settings
from execution.mt5_client import MT5Client
from strategy.institutional_strategy import InstitutionalStrategy

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

    strategy = InstitutionalStrategy(client)
    
    # Run one scan loop
    print("Running single scan pass...")
    await strategy.run_scan_loop()
    
    client.shutdown()

if __name__ == "__main__":
    asyncio.run(debug_async())
