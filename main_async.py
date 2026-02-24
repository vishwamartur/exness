
import asyncio
import os
import sys
import traceback
from datetime import datetime

# Reconfigure stdout for utf-8 (Windows fix)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Add project root to path
sys.path.append(os.path.dirname(__file__))

from config import settings
from execution.mt5_client import MT5Client
from strategy.institutional_strategy import InstitutionalStrategy
from api.stream_server import start_server, push_update

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

    # 1.8 Start Stream Server
    try:
        port = start_server()
    except Exception as e:
        print(f"Failed to start stream server: {e}")
        port = 8000

    # 1.9 Launch React Dashboard
    import subprocess, webbrowser, pathlib
    dashboard_dir = pathlib.Path(__file__).parent / "dashboard"
    if dashboard_dir.exists():
        try:
            subprocess.Popen(
                "npm run dev",
                cwd=str(dashboard_dir),
                shell=True,                          # Required on Windows (npm is npm.cmd)
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print("[DASHBOARD] Vite dev server starting at http://localhost:5173")
            await asyncio.sleep(3)   # let Vite warm up
            webbrowser.open("http://localhost:5173")
            print("[DASHBOARD] Opened in browser ✓")
        except Exception as e:
            print(f"[DASHBOARD] Could not launch dashboard: {e}")
    else:
        print("[DASHBOARD] dashboard/ folder not found — run 'cd dashboard && npm install' first")

    # 2. Initialize Strategy
    try:
        strategy = InstitutionalStrategy(client, on_event=push_update)
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
