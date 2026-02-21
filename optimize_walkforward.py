"""
Walk-Forward Optimization Script — M1 Scalping Strategy
========================================================
Fetches M1 data from ALL detected pairs, aggregates it,
then rolls through IS/OOS windows to grid-search optimal params.

Writes best params to models/best_params.json.

Parameters searched:
  - MIN_CONFLUENCE_SCORE: [2, 3, 4]
  - RF_PROB_THRESHOLD:    [0.45, 0.50, 0.55, 0.60]
  - ATR_SL_MULTIPLIER:    [1.0, 1.5, 2.0]
  - ATR_TP_MULTIPLIER:    [2.5, 3.0, 3.5, 4.0]

Run monthly:
  python f:\\mt5\\optimize_walkforward.py
"""

import os
import sys
import json
import itertools
import numpy as np
import pandas as pd

sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import settings
from market_data import loader
from strategy import features as feat_lib
from execution.mt5_client import MT5Client

# ─── Configuration ────────────────────────────────────────────────────────────
TIMEFRAME          = "M1"
BARS_PER_SYMBOL    = 10_000   # ~1 week of M1 per pair

# Window sizes (in bars of the aggregated dataset)
# With ~10 symbols × 10k bars = ~100k rows
# IS = 70%, OOS = 30% of each rolling step
IS_FRACTION  = 0.70   # 70% in-sample
STEP_FRACTION = 0.20  # slide 20% per step (gives ~4-5 windows on 100k rows)

PARAM_GRID = {
    'MIN_CONFLUENCE_SCORE': [2, 3, 4],
    'RF_PROB_THRESHOLD':    [0.45, 0.50, 0.55, 0.60],
    'ATR_SL_MULTIPLIER':    [1.0, 1.5, 2.0],
    'ATR_TP_MULTIPLIER':    [2.5, 3.0, 3.5, 4.0],
}

SCALP_HORIZON = 5   # 5 M1 bars = 5 min exit window


# ─── ATR Barrier Labelling ────────────────────────────────────────────────────
def apply_atr_barrier_fast(df, sl_mult, tp_mult, horizon=5):
    """
    Fast ATR barrier: label 1 if TP hit before SL within `horizon` bars.
    """
    closes = df['close'].values
    highs  = df['high'].values
    lows   = df['low'].values
    atrs   = df['atr'].values if 'atr' in df.columns else np.full(len(df), 0.0005)
    labels = np.zeros(len(df), dtype=int)

    for i in range(len(df) - horizon):
        atr = atrs[i] if atrs[i] > 0 else 0.0005
        tp  = closes[i] + atr * tp_mult
        sl  = closes[i] - atr * sl_mult
        hit_tp = np.any(highs[i+1 : i+1+horizon] >= tp)
        hit_sl = np.any(lows[i+1  : i+1+horizon] <= sl)
        if hit_tp and not hit_sl:
            labels[i] = 1

    return labels


# ─── OOS Simulation ───────────────────────────────────────────────────────────
def simulate_strategy(df_seg, params):
    """
    Forward-test a param combo on a data segment.
    Returns Sharpe proxy: mean(outcomes) / std(outcomes) * sqrt(N)
    """
    labels = apply_atr_barrier_fast(
        df_seg,
        params['ATR_SL_MULTIPLIER'],
        params['ATR_TP_MULTIPLIER'],
        SCALP_HORIZON
    )
    if labels.sum() == 0:
        return -999.0

    rr = params['ATR_TP_MULTIPLIER'] / params['ATR_SL_MULTIPLIER']
    outcomes = np.where(labels == 1, rr, -1.0)
    std = outcomes.std()
    if std == 0:
        return -999.0
    return (outcomes.mean() / std) * np.sqrt(len(outcomes))


# ─── Main ─────────────────────────────────────────────────────────────────────
def run_walkforward():
    print("=" * 60)
    print("  WALK-FORWARD OPTIMIZATION — M1 Scalping (All Pairs)")
    print("=" * 60)

    # 1. Connect and detect all account pairs
    client = MT5Client()
    if not client.connect():
        print("[ERROR] Could not connect to MT5.")
        return

    if not client.detect_available_symbols():
        print("[ERROR] No symbols detected.")
        return

    all_symbols = settings.SYMBOLS
    total = len(all_symbols)
    print(f"\nDetected {total} symbols: {', '.join(all_symbols)}\n")

    # 2. Collect M1 data from every pair
    all_data = []
    for i, symbol in enumerate(all_symbols, 1):
        print(f"  [{i:02d}/{total}] {symbol:<12}", end="  ")
        try:
            df = loader.get_historical_data(symbol, TIMEFRAME, BARS_PER_SYMBOL)
            if df is None or df.empty:
                print("SKIP (no data)")
                continue

            df = feat_lib.add_technical_features(df)
            if df.empty or len(df) < 200:
                print(f"SKIP (only {len(df)} rows after features)")
                continue

            all_data.append(df)
            print(f"OK   {len(df):>6,} bars")

        except Exception as e:
            print(f"ERR  ({e})")

    if not all_data:
        print("\n[ERROR] No valid data from any symbol.")
        return

    # 3. Aggregate all pairs into one shuffled dataset
    full_df = pd.concat(all_data, ignore_index=True)
    # Sort by time so rolling windows are chronological
    if 'time' in full_df.columns:
        full_df = full_df.sort_values('time').reset_index(drop=True)
    N = len(full_df)
    print(f"\nTotal dataset: {N:,} bars across {len(all_data)} symbols\n")

    # 4. Build rolling windows
    is_size   = int(N * IS_FRACTION)
    step_size = int(N * STEP_FRACTION)
    if step_size < 100:
        step_size = max(100, N // 5)

    windows = []
    start = 0
    while start + is_size + step_size <= N:
        is_end  = start + is_size
        oos_end = min(is_end + step_size, N)
        windows.append((start, is_end, is_end, oos_end))
        start += step_size

    if not windows:
        # Not enough data for rolling — just use one IS/OOS split
        is_end = int(N * IS_FRACTION)
        windows = [(0, is_end, is_end, N)]
        print(f"  Only 1 window available (dataset too small for rolling).")

    print(f"Walk-forward windows: {len(windows)}")
    print(f"  IS size : ~{is_size:,} bars  |  OOS size: ~{step_size:,} bars\n")

    all_combos = list(itertools.product(*PARAM_GRID.values()))
    param_keys = list(PARAM_GRID.keys())
    print(f"Grid search: {len(all_combos)} parameter combinations per window\n")

    window_results = []

    for w_idx, (is_start, is_end, oos_start, oos_end) in enumerate(windows):
        df_is  = full_df.iloc[is_start:is_end]
        df_oos = full_df.iloc[oos_start:oos_end]

        best_is_score = -np.inf
        best_combo    = None

        for combo in all_combos:
            params = dict(zip(param_keys, combo))
            score  = simulate_strategy(df_is, params)
            if score > best_is_score:
                best_is_score = score
                best_combo    = params

        oos_score = simulate_strategy(df_oos, best_combo) if best_combo else -999.0

        print(f"  Window {w_idx+1:02d}  IS: {best_is_score:+.3f}  OOS: {oos_score:+.3f}")
        if best_combo:
            sl = best_combo['ATR_SL_MULTIPLIER']
            tp = best_combo['ATR_TP_MULTIPLIER']
            cs = best_combo['MIN_CONFLUENCE_SCORE']
            rf = best_combo['RF_PROB_THRESHOLD']
            print(f"           → SL×{sl} TP×{tp}  Score≥{cs}  RF≥{rf}")

        window_results.append({'params': best_combo, 'oos_score': oos_score})

    # 5. Aggregate: find combo with best average OOS Sharpe
    combo_scores: dict = {}
    for wr in window_results:
        if wr['params'] is None:
            continue
        key = str(sorted(wr['params'].items()))
        combo_scores.setdefault(key, []).append(wr['oos_score'])

    if not combo_scores:
        print("\n[ERROR] No valid window results to aggregate.")
        return

    best_key = max(combo_scores, key=lambda k: np.mean(combo_scores[k]))
    best_params_final = dict(eval(best_key))
    avg_oos = np.mean(combo_scores[best_key])

    print("\n" + "=" * 60)
    print(f"  BEST PARAMS  (avg OOS Sharpe: {avg_oos:+.3f})")
    print("=" * 60)
    for k, v in best_params_final.items():
        print(f"  {k:<30s} = {v}")

    # 6. Save
    out_path = os.path.join(os.path.dirname(__file__), 'models', 'best_params.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as fh:
        json.dump(best_params_final, fh, indent=2)

    print(f"\n  Saved → {out_path}")
    print("  To apply: set these values in .env or load in settings.py")


if __name__ == "__main__":
    run_walkforward()
