"""
Train RF Model — M1 Scalping Version.

Changes from M15 version:
- Uses M1 timeframe data (50,000 bars ≈ ~5 weeks of M1)
- Scalping label: price reaches +0.0003 within 5 bars (≈ 3 pip target)
- Shorter time horizon fits M1 fast-moves
- Saves to scalper_m1_v1.pkl
"""

import pandas as pd
import numpy as np
import joblib
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import settings
from market_data import loader
from strategy import features
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix


def apply_atr_barrier(df, atr_tp_mult=3.0, atr_sl_mult=1.5, time_horizon=20):
    """
    Labels data using ATR-based barriers (matches live trading logic).
    Uses dynamic TP/SL based on ATR, not fixed pips.
    
    1 (Buy): Price hits ATR*TP_mult before ATR*SL_mult within time_horizon bars.
    0 (No Trade): Price hits SL or times out.
    """
    labels = []
    atr = df['atr'].values if 'atr' in df.columns else None
    
    if atr is None:
        print("Warning: ATR column not found, falling back to fixed barriers")
        return apply_fixed_barrier(df, time_horizon)
    
    for i in range(len(df)):
        if i + time_horizon >= len(df):
            labels.append(0)
            continue
        
        entry_price = df['close'].iloc[i]
        current_atr = atr[i]
        
        if current_atr <= 0:
            labels.append(0)
            continue
        
        tp_dist = current_atr * atr_tp_mult
        sl_dist = current_atr * atr_sl_mult
        
        future_window = df.iloc[i+1 : i+1+time_horizon]
        
        hit_tp = False
        hit_sl = False
        
        for j in range(len(future_window)):
            high = future_window['high'].iloc[j]
            low = future_window['low'].iloc[j]
            
            if high >= entry_price + tp_dist:
                hit_tp = True
                break
            if low <= entry_price - sl_dist:
                hit_sl = True
                break
        
        if hit_tp and not hit_sl:
            labels.append(1)
        else:
            labels.append(0)
    
    return pd.Series(labels, index=df.index)


def apply_fixed_barrier(df, time_horizon=20):
    """Fallback: fixed pip barriers like original."""
    labels = []
    point = 0.00001 if "JPY" not in settings.SYMBOL else 0.01
    tp_dist = 15 * point  # 15 pips for M15
    sl_dist = 10 * point  # 10 pips
    
    for i in range(len(df)):
        if i + time_horizon >= len(df):
            labels.append(0)
            continue
        
        entry_price = df['close'].iloc[i]
        future = df.iloc[i+1 : i+1+time_horizon]
        
        hit_tp = any(future['high'] >= entry_price + tp_dist)
        hit_sl = any(future['low'] <= entry_price - sl_dist)
        
        labels.append(1 if hit_tp and not hit_sl else 0)
    
    return pd.Series(labels, index=df.index)


from execution.mt5_client import MT5Client


def train():
    # ── 1. Connect and auto-detect all available pairs ──────────────────────
    client = MT5Client()
    if not client.connect():
        print("Failed to connect to MT5.")
        return

    if not client.detect_available_symbols():
        print("No symbols detected.")
        return

    all_symbols = settings.SYMBOLS
    total = len(all_symbols)
    print(f"\n{'='*60}")
    print(f"  M1 SCALPING RF TRAINER — {total} pairs detected")
    print(f"{'='*60}\n")

    # ── 2. Collect M1 data from every pair ──────────────────────────────────
    M1_BARS_PER_SYMBOL = 10_000  # ~1 week per symbol; aggregates to ~100k+ rows
    SCALP_HORIZON = 5            # 5 M1 bars = 5 min exit window
    all_data = []

    for i, symbol in enumerate(all_symbols, 1):
        print(f"[{i:02d}/{total}] {symbol:<12}", end="  ")
        try:
            df = loader.get_historical_data(symbol, "M1", M1_BARS_PER_SYMBOL)
            if df is None or df.empty:
                print("SKIP (no data)")
                continue

            df = features.add_technical_features(df)
            if df.empty:
                print("SKIP (features empty)")
                continue

            df['target'] = apply_atr_barrier(
                df,
                atr_tp_mult=settings.ATR_TP_MULTIPLIER,
                atr_sl_mult=settings.ATR_SL_MULTIPLIER,
                time_horizon=SCALP_HORIZON
            )

            df = df.iloc[:-SCALP_HORIZON].dropna()
            if len(df) < 100:
                print(f"SKIP (too few rows: {len(df)})")
                continue

            all_data.append(df)
            win_rate = df['target'].mean() * 100
            print(f"OK  {len(df):>6,} bars | win% {win_rate:.1f}%")

        except Exception as e:
            print(f"ERR ({e})")
            continue

    if not all_data:
        print("\nNo valid data collected from any symbol.")
        return

    # ── 3. Aggregate ─────────────────────────────────────────────────────────
    full_df = pd.concat(all_data, ignore_index=True)
    print(f"\nTotal dataset: {len(full_df):,} bars across {len(all_data)} symbols")

    positives = (full_df['target'] == 1).sum()
    negatives = (full_df['target'] == 0).sum()
    print(f"Class balance: {positives:,} wins ({positives/len(full_df)*100:.1f}%) "
          f"| {negatives:,} losses ({negatives/len(full_df)*100:.1f}%)")

    # ── 4. Features & Split ──────────────────────────────────────────────────
    drop_cols = ['time', 'open', 'high', 'low', 'close',
                 'tick_volume', 'spread', 'real_volume', 'target']
    feature_cols = [c for c in full_df.columns if c not in drop_cols]

    X = full_df[feature_cols]
    y = full_df['target']

    print(f"Training on {len(X):,} samples with {len(feature_cols)} features")

    # Shuffle=True — mix pairs so model generalises across symbols
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, shuffle=True, random_state=42
    )

    # ── 5. Train Random Forest ───────────────────────────────────────────────
    print("\nTraining Random Forest (M1 Scalper, all pairs)...")
    rf_model = RandomForestClassifier(
        n_estimators=300,
        max_depth=20,
        min_samples_leaf=10,
        min_samples_split=20,
        max_features='sqrt',
        random_state=42,
        class_weight='balanced',
        n_jobs=-1
    )
    rf_model.fit(X_train, y_train)

    # ── 6. Evaluate ──────────────────────────────────────────────────────────
    preds = rf_model.predict(X_test)
    print("\n" + "="*60)
    print("  EVALUATION RESULTS (ALL PAIRS — M1)")
    print("="*60)
    print(f"Training symbols : {len(all_data)}")
    print(f"Total samples    : {len(full_df):,}")
    print(f"Accuracy         : {accuracy_score(y_test, preds):.4f}")
    print(classification_report(y_test, preds))
    print("Confusion Matrix:")
    print(confusion_matrix(y_test, preds))

    # Feature importance (top 15)
    importances = pd.Series(rf_model.feature_importances_, index=feature_cols)
    print("\nTop 15 Features:")
    for feat, imp in importances.nlargest(15).items():
        print(f"  {feat:30s} {imp:.4f}")

    # ── 7. Save ──────────────────────────────────────────────────────────────
    m1_model_path = settings.MODEL_PATH.replace('scalper_v1', 'scalper_m1_v1')
    m1_feat_path  = m1_model_path.replace('.pkl', '_features.pkl')
    os.makedirs(os.path.dirname(m1_model_path), exist_ok=True)
    joblib.dump(rf_model, m1_model_path)
    joblib.dump(feature_cols, m1_feat_path)
    print(f"\nM1 Model saved  → {m1_model_path}")
    print(f"Features saved  → {m1_feat_path}")



if __name__ == "__main__":
    train()
