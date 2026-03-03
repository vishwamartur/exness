"""
Triple Barrier Labeling — Institutional-Grade Training Targets
================================================================
Replaces the naive "price went up/down" labeling with a method that
matches how real trades work:

  Barrier 1: Take Profit hit  → WIN  (label = 1)
  Barrier 2: Stop Loss hit    → LOSS (label = 0)
  Barrier 3: Time horizon     → TIMEOUT (label = 0 or configurable)

Key advantages over vanilla labeling:
  1. Targets match actual trade mechanics (ATR-dynamic TP/SL)
  2. Handles both LONG and SHORT trade setups
  3. Time barrier prevents "forever holding" bias
  4. Returns rich metadata for analysis (first barrier hit, time-to-hit)

Based on Marcos López de Prado's "Advances in Financial Machine Learning"
"""

import numpy as np
import pandas as pd
from typing import Optional, Tuple


def triple_barrier_labels(
    df: pd.DataFrame,
    atr_tp_mult: float = 3.0,
    atr_sl_mult: float = 1.5,
    time_horizon: int = 20,
    direction: str = 'long',
    timeout_label: int = 0,
    min_atr: float = 1e-8
) -> pd.DataFrame:
    """
    Apply Triple Barrier Labeling to a DataFrame with OHLCV + ATR data.

    For each bar, sets 3 barriers:
      - TP barrier: entry ± (ATR * atr_tp_mult), direction-dependent
      - SL barrier: entry ∓ (ATR * atr_sl_mult), direction-dependent
      - Time barrier: `time_horizon` bars into the future

    The FIRST barrier hit determines the label.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain columns: 'close', 'high', 'low', 'atr'
    atr_tp_mult : float
        Take-profit distance as multiple of ATR (default 3.0)
    atr_sl_mult : float
        Stop-loss distance as multiple of ATR (default 1.5)
    time_horizon : int
        Maximum bars to hold before timeout (default 20)
    direction : str
        'long' — labels for BUY setups (TP = price goes UP)
        'short' — labels for SELL setups (TP = price goes DOWN)
        'both' — generates separate long/short labels
    timeout_label : int
        Label to assign when time barrier hits first (default 0 = LOSS)
    min_atr : float
        Minimum ATR to avoid zero-division (default 1e-8)

    Returns
    -------
    pd.DataFrame with new columns:
        'tb_label' : int — 1 (WIN), 0 (LOSS/TIMEOUT)
        'tb_barrier' : str — 'TP', 'SL', 'TIMEOUT', 'SKIP'
        'tb_bars_to_hit' : int — bars until first barrier was touched
        'tb_return' : float — return at the point of barrier hit
    If direction='both', adds 'tb_label_long', 'tb_label_short', etc.
    """
    if 'atr' not in df.columns:
        raise ValueError("DataFrame must contain 'atr' column. Run features.add_technical_features() first.")

    if direction == 'both':
        # Generate both long and short labels
        long_result = _compute_barriers(df, atr_tp_mult, atr_sl_mult, time_horizon, 'long', timeout_label, min_atr)
        short_result = _compute_barriers(df, atr_tp_mult, atr_sl_mult, time_horizon, 'short', timeout_label, min_atr)

        result = df.copy()
        for col in ['tb_label', 'tb_barrier', 'tb_bars_to_hit', 'tb_return']:
            result[f'{col}_long'] = long_result[col]
            result[f'{col}_short'] = short_result[col]

        # Composite: pick the direction with higher probability
        # Training can use both or pick the better direction per bar
        result['tb_label'] = long_result['tb_label']  # Default to long for backward compat
        result['tb_barrier'] = long_result['tb_barrier']
        return result
    else:
        result_cols = _compute_barriers(df, atr_tp_mult, atr_sl_mult, time_horizon, direction, timeout_label, min_atr)
        out = df.copy()
        for col, values in result_cols.items():
            out[col] = values
        return out


def _compute_barriers(
    df: pd.DataFrame,
    atr_tp_mult: float,
    atr_sl_mult: float,
    time_horizon: int,
    direction: str,
    timeout_label: int,
    min_atr: float
) -> dict:
    """
    Core Triple Barrier computation using vectorized operations where possible.
    Falls back to loop for the barrier scanning since it's inherently sequential.
    """
    n = len(df)
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    atr = df['atr'].values

    labels = np.full(n, timeout_label, dtype=int)
    barriers = np.full(n, 'SKIP', dtype=object)
    bars_to_hit = np.full(n, time_horizon, dtype=int)
    returns = np.zeros(n, dtype=float)

    is_long = (direction == 'long')

    for i in range(n):
        # Cannot label bars too close to the end
        if i + time_horizon >= n:
            barriers[i] = 'SKIP'
            continue

        entry = close[i]
        current_atr = max(atr[i], min_atr)

        # Set barriers based on direction
        if is_long:
            tp_price = entry + (current_atr * atr_tp_mult)
            sl_price = entry - (current_atr * atr_sl_mult)
        else:
            tp_price = entry - (current_atr * atr_tp_mult)
            sl_price = entry + (current_atr * atr_sl_mult)

        # Scan future bars for first barrier hit
        hit_found = False
        for j in range(1, time_horizon + 1):
            idx = i + j
            bar_high = high[idx]
            bar_low = low[idx]

            if is_long:
                # Long: TP hit when high >= tp_price
                tp_hit = bar_high >= tp_price
                # Long: SL hit when low <= sl_price
                sl_hit = bar_low <= sl_price
            else:
                # Short: TP hit when low <= tp_price
                tp_hit = bar_low <= tp_price
                # Short: SL hit when high >= sl_price
                sl_hit = bar_high >= sl_price

            if tp_hit and sl_hit:
                # Both hit same bar — assume SL hit first (conservative)
                # In reality you'd need tick data, so we assume worst case
                labels[i] = 0
                barriers[i] = 'SL'
                bars_to_hit[i] = j
                if is_long:
                    returns[i] = (sl_price - entry) / entry
                else:
                    returns[i] = (entry - sl_price) / entry
                hit_found = True
                break
            elif tp_hit:
                labels[i] = 1
                barriers[i] = 'TP'
                bars_to_hit[i] = j
                if is_long:
                    returns[i] = (tp_price - entry) / entry
                else:
                    returns[i] = (entry - tp_price) / entry
                hit_found = True
                break
            elif sl_hit:
                labels[i] = 0
                barriers[i] = 'SL'
                bars_to_hit[i] = j
                if is_long:
                    returns[i] = (sl_price - entry) / entry
                else:
                    returns[i] = (entry - sl_price) / entry
                hit_found = True
                break

        if not hit_found:
            # Time barrier hit — use timeout label
            labels[i] = timeout_label
            barriers[i] = 'TIMEOUT'
            bars_to_hit[i] = time_horizon
            # Return at timeout = close at end of horizon vs entry
            timeout_close = close[i + time_horizon]
            if is_long:
                returns[i] = (timeout_close - entry) / entry
            else:
                returns[i] = (entry - timeout_close) / entry

    return {
        'tb_label': labels,
        'tb_barrier': barriers,
        'tb_bars_to_hit': bars_to_hit,
        'tb_return': returns
    }


def apply_triple_barrier(
    df: pd.DataFrame,
    atr_tp_mult: float = 3.0,
    atr_sl_mult: float = 1.5,
    time_horizon: int = 20
) -> pd.Series:
    """
    Drop-in replacement for the existing `apply_atr_barrier()` function
    used across training scripts.

    Returns a simple pd.Series of labels (1 = WIN, 0 = LOSS/TIMEOUT).
    This maintains backward compatibility while upgrading the labeling logic.

    Key improvement: properly handles the time barrier as a third condition
    rather than defaulting unlabeled bars to 0.
    """
    result = triple_barrier_labels(
        df,
        atr_tp_mult=atr_tp_mult,
        atr_sl_mult=atr_sl_mult,
        time_horizon=time_horizon,
        direction='long',
        timeout_label=0
    )
    return result['tb_label']


def get_barrier_stats(df: pd.DataFrame) -> dict:
    """
    Compute statistics from Triple Barrier labeled data.
    Useful for analyzing label quality and tuning parameters.
    """
    if 'tb_barrier' not in df.columns:
        return {}

    valid = df[df['tb_barrier'] != 'SKIP']
    total = len(valid)

    if total == 0:
        return {'total': 0}

    tp_count = (valid['tb_barrier'] == 'TP').sum()
    sl_count = (valid['tb_barrier'] == 'SL').sum()
    timeout_count = (valid['tb_barrier'] == 'TIMEOUT').sum()

    return {
        'total': total,
        'tp_count': int(tp_count),
        'sl_count': int(sl_count),
        'timeout_count': int(timeout_count),
        'win_rate': tp_count / total if total > 0 else 0,
        'loss_rate': sl_count / total if total > 0 else 0,
        'timeout_rate': timeout_count / total if total > 0 else 0,
        'avg_bars_to_hit': valid['tb_bars_to_hit'].mean(),
        'avg_win_return': valid.loc[valid['tb_barrier'] == 'TP', 'tb_return'].mean() if tp_count > 0 else 0,
        'avg_loss_return': valid.loc[valid['tb_barrier'] == 'SL', 'tb_return'].mean() if sl_count > 0 else 0,
        'risk_reward_ratio': (
            abs(valid.loc[valid['tb_barrier'] == 'TP', 'tb_return'].mean()) /
            abs(valid.loc[valid['tb_barrier'] == 'SL', 'tb_return'].mean())
        ) if sl_count > 0 and tp_count > 0 else 0,
    }
