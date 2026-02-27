
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

    # 2. Initialize Strategy & Stat Arb Engine
    try:
        strategy = InstitutionalStrategy(client, on_event=push_update)
        
        from analysis.stat_arb_manager import StatArbManager
        stat_arb = StatArbManager(client)
        
        print("Agents & Stat-Arb Initialized.")
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
                
                # --- STATISTICAL ARBITRAGE (Pairs Trading) ---
                print(f"[STAT-ARB] Evaluating {len(settings.STAT_ARB_PAIRS)} Cointegrated Hedging Pairs...")
                for symbol_A, symbol_B in settings.STAT_ARB_PAIRS:
                    # 1. Fetch minimum rolling 100 bars for OLS regression math
                    # Use H1 timeframe for arb to filter out microstructure noise and focus on macro divergence
                    from market_data import loader
                    df_A = loader.get_historical_data(symbol_A, "H1", 200)
                    df_B = loader.get_historical_data(symbol_B, "H1", 200)
                    
                    # 2. Analyze Pair
                    # Generates a Signal if Z-Score drifts beyond +-2 standard deviations
                    signal = stat_arb.analyze_pair(symbol_A, symbol_B, df_A, df_B)
                    
                    if signal:
                        action = signal.get("action")
                        
                        if action == "OPEN_SPREAD":
                            stat_arb.execute_spread_trade(
                                symbol_A, symbol_B, 
                                signal["direction_A"], signal["direction_B"], 
                                signal["hedge_ratio"]
                            )
                            # Alert UI
                            push_update({
                                "type": "STAT_ARB_HEDGE",
                                "symbol": f"{symbol_A}/{symbol_B}",
                                "message": f"Deploying Z={signal['z_score']:.2f} Delta-Neutral Spread"
                            })
                            
                        elif action == "CLOSE_SPREAD":
                            stat_arb.close_spread_trade(symbol_A, symbol_B)
                            push_update({
                                "type": "STAT_ARB_FLATTEN",
                                "symbol": f"{symbol_A}/{symbol_B}",
                                "message": f"Reversion! Z={signal['z_score']:.2f} Spread Flattened"
                            })
                            
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
