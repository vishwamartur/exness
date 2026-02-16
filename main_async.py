
import asyncio
import os
import sys
import traceback
from datetime import datetime

# Add project root to path
sys.path.append(os.path.dirname(__file__))

from config import settings
from execution.mt5_client import MT5Client
from strategy.institutional_strategy import InstitutionalStrategy

async def main():
    print(f"=== INSTITUTIONAL STRATEGY v2.2 (Async) ===")
    print(f"Start Time: {datetime.now()}")
    
    # 1. Initialize MT5
    client = MT5Client()
    if not client.connect():
        print("Failed to connect to MT5. Exiting.")
        return

    # 1.5 Detect Symbols
    if not client.detect_available_symbols():
        print("Failed to detect symbols. Exiting.")
        return

    # 2. Initialize Strategy
    try:
        strategy = InstitutionalStrategy(client)
        print("Agents Initialized.")
    except Exception as e:
        print(f"Failed to init strategy: {e}")
        traceback.print_exc()
        return

    # 3. Main Loop
    print(f"entering main loop... (Interval: {settings.COOLDOWN_SECONDS}s)")
    
    try:
        while True:
            start_time = asyncio.get_running_loop().time()
            
            try:
                await strategy.run_scan_loop()
            except Exception as e:
                print(f"[ERROR] Scan loop failed: {e}")
                traceback.print_exc()
            
            # Calculate sleep time to maintain interval
            elapsed = asyncio.get_running_loop().time() - start_time
            sleep_time = max(1, settings.COOLDOWN_SECONDS - elapsed)
            
            print(f"[SLEEP] Waiting {sleep_time:.1f}s...")
            await asyncio.sleep(sleep_time)
            
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        client.shutdown()
        print("MT5 Shutdown.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
