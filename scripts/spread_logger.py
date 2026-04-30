"""
Spread Logger — Pre-deployment cost analysis tool for XAUUSD on Exness.

FIRST TASK: Before writing strategy code, run this script for 48 hours.
It logs live spreads and calculates round-trip cost per lot.

If gold needs to move $0.25-0.40 just to break even, scalping is
mathematically impossible on your account type.

Usage:
    python scripts/spread_logger.py [--hours 48] [--symbol XAUUSD]

Output:
    - CSV: scripts/spread_log_YYYYMMDD.csv
    - Summary: mean, median, p95 spread, cost breakdown
"""

import os
import sys
import time
import csv
import argparse
from datetime import datetime, timezone

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings


def main():
    parser = argparse.ArgumentParser(description="XAUUSD Spread Logger")
    parser.add_argument("--hours", type=float, default=48, help="Hours to log")
    parser.add_argument("--symbol", type=str, default="XAUUSD", help="Symbol")
    parser.add_argument("--interval", type=float, default=5.0, help="Seconds between samples")
    parser.add_argument("--commission", type=float, default=3.50, help="Commission per side per lot")
    args = parser.parse_args()

    import MetaTrader5 as mt5

    if not mt5.initialize(path=settings.MT5_PATH,
                          login=settings.MT5_LOGIN,
                          password=settings.MT5_PASSWORD,
                          server=settings.MT5_SERVER):
        print(f"MT5 init failed: {mt5.last_error()}")
        return

    # Detect symbol suffix
    symbol = args.symbol
    for suffix in settings.EXNESS_SUFFIXES:
        test = args.symbol + suffix
        info = mt5.symbol_info(test)
        if info and info.visible:
            symbol = test
            break

    info = mt5.symbol_info(symbol)
    if not info:
        print(f"Symbol {symbol} not found")
        mt5.shutdown()
        return

    contract_size = info.trade_contract_size  # 100 oz for gold
    point = info.point

    print(f"{'='*60}")
    print(f"  SPREAD LOGGER — {symbol}")
    print(f"  Contract Size: {contract_size} oz")
    print(f"  Point: {point}")
    print(f"  Commission: ${args.commission}/side/lot")
    print(f"  Duration: {args.hours} hours")
    print(f"  Interval: {args.interval}s")
    print(f"{'='*60}\n")

    # CSV output
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    csv_path = os.path.join(os.path.dirname(__file__), f"spread_log_{date_str}.csv")
    csvfile = open(csv_path, 'w', newline='')
    writer = csv.writer(csvfile)
    writer.writerow(["timestamp", "bid", "ask", "spread_price", "spread_pips",
                      "round_trip_cost_usd"])

    spreads = []
    costs = []
    start_time = time.time()
    end_time = start_time + (args.hours * 3600)
    sample_count = 0

    try:
        while time.time() < end_time:
            tick = mt5.symbol_info_tick(symbol)
            if tick:
                spread_price = tick.ask - tick.bid
                spread_pips = spread_price / point / 10.0
                # Round-trip cost = spread × contract_size + 2 × commission
                rt_cost = (spread_price * contract_size) + (2 * args.commission)

                spreads.append(spread_price)
                costs.append(rt_cost)

                now = datetime.now(timezone.utc).isoformat()
                writer.writerow([now, f"{tick.bid:.2f}", f"{tick.ask:.2f}",
                                 f"{spread_price:.4f}", f"{spread_pips:.1f}",
                                 f"{rt_cost:.2f}"])

                sample_count += 1
                if sample_count % 100 == 0:
                    csvfile.flush()
                    import numpy as np
                    arr = np.array(spreads)
                    print(f"  [{sample_count}] Spread: {spread_price:.4f} "
                          f"(mean={arr.mean():.4f} med={np.median(arr):.4f} "
                          f"p95={np.percentile(arr, 95):.4f}) "
                          f"RT cost: ${rt_cost:.2f}")

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        csvfile.close()
        mt5.shutdown()

    # Summary
    if spreads:
        import numpy as np
        arr = np.array(spreads)
        cost_arr = np.array(costs)

        print(f"\n{'='*60}")
        print(f"  SPREAD ANALYSIS SUMMARY — {symbol}")
        print(f"{'='*60}")
        print(f"  Samples: {len(spreads)}")
        print(f"  Duration: {(time.time() - start_time)/3600:.1f} hours")
        print(f"")
        print(f"  Spread (price):")
        print(f"    Mean:   ${arr.mean():.4f}")
        print(f"    Median: ${np.median(arr):.4f}")
        print(f"    P5:     ${np.percentile(arr, 5):.4f}")
        print(f"    P95:    ${np.percentile(arr, 95):.4f}")
        print(f"    Max:    ${arr.max():.4f}")
        print(f"")
        print(f"  Round-Trip Cost (per lot):")
        print(f"    Mean:   ${cost_arr.mean():.2f}")
        print(f"    Median: ${np.median(cost_arr):.2f}")
        print(f"    P95:    ${np.percentile(cost_arr, 95):.2f}")
        print(f"")
        print(f"  Break-Even Move (per lot):")
        be = cost_arr.mean() / contract_size
        print(f"    Gold must move ${be:.4f} just to break even")
        print(f"")
        if be > 0.40:
            print(f"  !! WARNING: Break-even > $0.40 — scalping is VERY HARD !!")
        elif be > 0.25:
            print(f"  !! CAUTION: Break-even $0.25-0.40 — tight scalping difficult !!")
        else:
            print(f"  OK: Break-even < $0.25 — scalping viable")
        print(f"")
        print(f"  CSV saved: {csv_path}")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
