
import sys
import os
import asyncio
import traceback
from datetime import datetime, timezone

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import settings
from execution.mt5_client import MT5Client
from strategy.institutional_strategy import InstitutionalStrategy

# Enable Debug Mode for verbose output
settings.DEBUG_MODE = True
settings.LOG_LEVEL = "DEBUG"

async def debug_scan_session():
    print(f"\n{'='*60}\n  DEBUG SCAN SESSION\n{'='*60}")
    
    # 1. Connect MT5
    client = MT5Client()
    if not client.connect():
        print("[ERROR] MT5 Connection Failed")
        return

    try:
        # Override Symbols BEFORE Strategy Init
        # Use ALL_BASE_SYMBOLS to test everything
        target_symbols = settings.ALL_BASE_SYMBOLS
        settings.SYMBOLS = target_symbols
        
        # Populate Category Lists manually for test (since auto-detect didn't run)
        settings.SYMBOLS_CRYPTO = settings.SYMBOLS_CRYPTO_BASE
        settings.SYMBOLS_COMMODITIES = settings.SYMBOLS_COMMODITIES_BASE
        
        print(f"[INIT] Settings Symbols set to: {len(settings.SYMBOLS)} pairs")
        print(f"[INIT] Categories: {len(settings.SYMBOLS_CRYPTO)} Crypto, {len(settings.SYMBOLS_COMMODITIES)} Commodities")

        # 2. Initialize Strategy (Coordinator)
        print("[INIT] Initializing Strategy & Agents...")
        strategy = InstitutionalStrategy(client)
        
        # Verify Agent Count
        print(f"[TEST] Created {len(strategy.agents)} Pair Agents.")
        
        # 4. Run Scan Loop
        print("\n[ACTION] Running Scan Loop for ALL pairs...")
        await strategy.run_scan_loop()
        
        # Filter agents to only these for debug (if they exist in settings)
        # We can't easily remove them from strategy.agents as it's a dict, 
        # but we can filter the loop or just let it run if not too many.
        # Let's just run the full scan loop, but maybe limit concurrency if needed.
        # Or better, just let it run as normal to catch everything.
        
        print(f"[TEST] Target Symbols: {list(strategy.agents.keys())}")
        
        # 4. Run Scan Loop
        print("\n[ACTION] Running Scan Loop...")
        await strategy.run_scan_loop()
        
        print("\n[DONE] Scan Complete.")
        
    except Exception as e:
        print(f"\n[CRITICAL] Scan Crashed: {e}")
        traceback.print_exc()
    finally:
        client.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(debug_scan_session())
    except KeyboardInterrupt:
        pass
