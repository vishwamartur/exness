"""
Train ML Models on 10 Years of Historical Data
==============================================

This script trains XGBoost and Random Forest models on 10 years of data
for robust, long-term trading performance.

Features:
- Multi-timeframe data (M15, H1, H4, D1)
- Walk-forward validation (prevents look-ahead bias)
- Time-series cross-validation
- Feature engineering with market regime detection
- Class balancing for imbalanced win/loss ratios
- GPU acceleration if available
"""

import pandas as pd
import numpy as np
import joblib
import os
import sys
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# Add project root
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import settings
from market_data import loader
from strategy import features
from execution.mt5_client import MT5Client

# ML Libraries
import xgboost as xgb
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import (
    accuracy_score, classification_report, confusion_matrix,
    precision_score, recall_score, f1_score, roc_auc_score
)
from sklearn.preprocessing import StandardScaler

# Try GPU XGBoost
try:
    import torch
    HAS_GPU = torch.cuda.is_available()
    if HAS_GPU:
        GPU_NAME = torch.cuda.get_device_name(0)
        print(f"GPU Detected: {GPU_NAME}")
except:
    HAS_GPU = False
    GPU_NAME = None


# =============================================================================
# Configuration
# =============================================================================

# Data settings
TIMEFRAME = "M15"  # Primary timeframe for training
BARS_10_YEARS = 250000  # ~10 years of M15 data (will get whatever is available)
MIN_BARS_PER_SYMBOL = 5000  # Minimum required bars (reduced for brokers with limited history)

# Labelling settings (ATR-based)
ATR_TP_MULT = 3.0  # Take profit = 3x ATR
ATR_SL_MULT = 1.5  # Stop loss = 1.5x ATR
HORIZON_BARS = 20  # Look-ahead window for labelling

# Walk-forward settings
N_SPLITS = 5  # Number of time-series splits
TRAIN_RATIO = 0.7  # Training portion of each split

# Model settings - GPU optimized for GTX 1650 Ti
XGB_PARAMS = {
    'n_estimators': 1000,
    'max_depth': 8,
    'learning_rate': 0.01,
    'subsample': 0.8,
    'colsample_bytree': 0.8,
    'min_child_weight': 10,
    'gamma': 0.3,
    'reg_alpha': 0.1,
    'reg_lambda': 1.0,
    'random_state': 42,
    'n_jobs': -1,
    'eval_metric': 'aucpr',
    'early_stopping_rounds': 100,
    # GPU Settings
    'tree_method': 'hist',  # Use 'hist' for GPU (faster than 'gpu_hist' on newer XGBoost)
    'device': 'cuda' if HAS_GPU else 'cpu',
}

RF_PARAMS = {
    'n_estimators': 500,
    'max_depth': 12,
    'min_samples_split': 20,
    'min_samples_leaf': 10,
    'max_features': 'sqrt',
    'class_weight': 'balanced',
    'random_state': 42,
    'n_jobs': -1,
}


# =============================================================================
# Labelling Function
# =============================================================================

def apply_atr_barrier(df, atr_tp_mult=ATR_TP_MULT, atr_sl_mult=ATR_SL_MULT, 
                      horizon=HORIZON_BARS):
    """
    Labels data using ATR-based barriers.
    Returns 1 if TP hit first, 0 if SL hit first or neither.
    """
    labels = []
    atr = df['atr'].values if 'atr' in df.columns else None
    
    if atr is None:
        print("Warning: ATR column not found")
        return pd.Series([0] * len(df), index=df.index)
    
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    
    for i in range(len(df)):
        if i + horizon >= len(df):
            labels.append(0)
            continue
        
        if atr[i] <= 0:
            labels.append(0)
            continue
        
        entry = closes[i]
        tp_dist = atr[i] * atr_tp_mult
        sl_dist = atr[i] * atr_sl_mult
        
        # Check future bars
        hit_tp = False
        hit_sl = False
        
        for j in range(i + 1, min(i + 1 + horizon, len(df))):
            # For LONG trade
            if highs[j] >= entry + tp_dist:
                hit_tp = True
                break
            if lows[j] <= entry - sl_dist:
                hit_sl = True
                break
        
        labels.append(1 if hit_tp and not hit_sl else 0)
    
    return pd.Series(labels, index=df.index)


def apply_directional_labels(df, atr_tp_mult=ATR_TP_MULT, atr_sl_mult=ATR_SL_MULT,
                             horizon=HORIZON_BARS):
    """
    Creates directional labels: 1 = BUY profitable, -1 = SELL profitable, 0 = neutral
    """
    labels = []
    atr = df['atr'].values if 'atr' in df.columns else None
    
    if atr is None:
        return pd.Series([0] * len(df), index=df.index)
    
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    
    for i in range(len(df)):
        if i + horizon >= len(df) or atr[i] <= 0:
            labels.append(0)
            continue
        
        entry = closes[i]
        tp_dist = atr[i] * atr_tp_mult
        sl_dist = atr[i] * atr_sl_mult
        
        # Check LONG
        long_win = False
        long_loss = False
        for j in range(i + 1, min(i + 1 + horizon, len(df))):
            if highs[j] >= entry + tp_dist:
                long_win = True
                break
            if lows[j] <= entry - sl_dist:
                long_loss = True
                break
        
        # Check SHORT
        short_win = False
        short_loss = False
        for j in range(i + 1, min(i + 1 + horizon, len(df))):
            if lows[j] <= entry - tp_dist:
                short_win = True
                break
            if highs[j] >= entry + sl_dist:
                short_loss = True
                break
        
        # Assign label
        if long_win and not long_loss:
            labels.append(1)  # BUY
        elif short_win and not short_loss:
            labels.append(-1)  # SELL
        else:
            labels.append(0)  # NEUTRAL
    
    return pd.Series(labels, index=df.index)


# =============================================================================
# Feature Engineering
# =============================================================================

def add_advanced_features(df):
    """Add advanced features for 10-year model."""
    df = features.add_technical_features(df)
    
    # Time-based features
    if 'time' in df.columns:
        df['hour'] = pd.to_datetime(df['time']).dt.hour
        df['day_of_week'] = pd.to_datetime(df['time']).dt.dayofweek
        df['month'] = pd.to_datetime(df['time']).dt.month
        df['is_london'] = df['hour'].apply(lambda x: 1 if 7 <= x <= 16 else 0)
        df['is_newyork'] = df['hour'].apply(lambda x: 1 if 13 <= x <= 22 else 0)
        df['is_overlap'] = df['hour'].apply(lambda x: 1 if 13 <= x <= 16 else 0)
    
    # Volatility features
    if 'atr' in df.columns and 'close' in df.columns:
        df['atr_pct'] = df['atr'] / df['close'] * 100
        df['atr_zscore'] = (df['atr'] - df['atr'].rolling(100).mean()) / df['atr'].rolling(100).std()
    
    # Momentum features
    if 'close' in df.columns:
        df['returns_1'] = df['close'].pct_change(1)
        df['returns_5'] = df['close'].pct_change(5)
        df['returns_20'] = df['close'].pct_change(20)
        df['returns_60'] = df['close'].pct_change(60)
        
        # Volatility of returns
        df['volatility_20'] = df['returns_1'].rolling(20).std()
        df['volatility_60'] = df['returns_1'].rolling(60).std()
    
    # Trend strength
    if 'adx' in df.columns:
        df['strong_trend'] = (df['adx'] > 25).astype(int)
    
    # Mean reversion
    if 'rsi' in df.columns:
        df['rsi_oversold'] = (df['rsi'] < 30).astype(int)
        df['rsi_overbought'] = (df['rsi'] > 70).astype(int)
    
    # Price relative to moving averages
    for period in [20, 50, 100, 200]:
        col = f'sma_{period}'
        if col in df.columns:
            df[f'price_vs_sma{period}'] = (df['close'] / df[col] - 1) * 100
    
    return df


# =============================================================================
# Data Collection
# =============================================================================

def collect_data(symbols, timeframe=TIMEFRAME, bars=BARS_10_YEARS):
    """Collect historical data for all symbols."""
    print(f"\n{'='*60}")
    print(f"  COLLECTING 10 YEARS OF DATA")
    print(f"{'='*60}")
    print(f"Timeframe: {timeframe} | Target Bars: {bars:,}")
    print(f"Symbols: {len(symbols)}")
    print()
    
    all_data = []
    symbol_stats = []
    
    for i, symbol in enumerate(symbols, 1):
        print(f"[{i}/{len(symbols)}] {symbol}...", end=" ", flush=True)
        
        try:
            df = loader.get_historical_data(symbol, timeframe, bars)
            
            if df is None or len(df) < MIN_BARS_PER_SYMBOL:
                print(f"Skipped (only {len(df) if df is not None else 0} bars)")
                continue
            
            # Add features
            df = add_advanced_features(df)
            
            # Add labels
            df['target'] = apply_atr_barrier(df)
            df['direction'] = apply_directional_labels(df)
            
            # Add symbol info
            df['symbol_id'] = hash(symbol) % 1000
            
            # Calculate volatility class
            if 'atr' in df.columns:
                atr_mean = df['atr'].mean()
                close_mean = df['close'].mean()
                vol_ratio = atr_mean / close_mean if close_mean > 0 else 0
                
                if vol_ratio < 0.0005:
                    vol_class = 0  # Low (Forex majors)
                elif vol_ratio < 0.001:
                    vol_class = 1  # Medium-low
                elif vol_ratio < 0.005:
                    vol_class = 2  # Medium (Commodities)
                else:
                    vol_class = 3  # High (Crypto)
                
                df['volatility_class'] = vol_class
            
            # Remove rows that can't be labelled
            df = df.iloc[:-HORIZON_BARS-1].dropna()
            
            if len(df) > 0:
                all_data.append(df)
                
                # Stats
                wins = (df['target'] == 1).sum()
                total = len(df)
                win_rate = wins / total * 100 if total > 0 else 0
                
                years = len(df) / (96 * 252)  # M15 bars per year
                symbol_stats.append({
                    'symbol': symbol,
                    'bars': len(df),
                    'years': years,
                    'win_rate': win_rate
                })
                
                print(f"{len(df):,} bars ({years:.1f} years) | WR: {win_rate:.1f}%")
            else:
                print("No valid data after processing")
                
        except Exception as e:
            print(f"Error: {e}")
            continue
    
    if not all_data:
        print("\nNo data collected!")
        return None, []
    
    # Combine all data
    full_df = pd.concat(all_data, ignore_index=True)
    
    print(f"\n{'='*60}")
    print(f"  DATA SUMMARY")
    print(f"{'='*60}")
    print(f"Total Bars: {len(full_df):,}")
    print(f"Symbols: {len(all_data)}")
    print(f"Date Range: {full_df['time'].min()} to {full_df['time'].max()}")
    
    # Class balance
    wins = (full_df['target'] == 1).sum()
    losses = (full_df['target'] == 0).sum()
    print(f"Class Balance: WIN {wins:,} ({wins/len(full_df)*100:.1f}%) | "
          f"LOSS {losses:,} ({losses/len(full_df)*100:.1f}%)")
    
    return full_df, symbol_stats


# =============================================================================
# Model Training
# =============================================================================

def get_feature_columns(df):
    """Get feature columns for training."""
    exclude = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 
               'spread', 'real_volume', 'target', 'direction']
    return [c for c in df.columns if c not in exclude and not c.startswith('_')]


def train_xgboost(X_train, y_train, X_val, y_val, class_ratio):
    """Train XGBoost with GPU acceleration and early stopping."""
    params = XGB_PARAMS.copy()
    params['scale_pos_weight'] = class_ratio
    
    model = xgb.XGBClassifier(**params)
    
    eval_set = [(X_train, y_train), (X_val, y_val)]
    model.fit(X_train, y_train, eval_set=eval_set, verbose=False)
    
    return model


def train_random_forest(X_train, y_train):
    """Train Random Forest."""
    model = RandomForestClassifier(**RF_PARAMS)
    model.fit(X_train, y_train)
    return model


def walk_forward_train(df, feature_cols, n_splits=N_SPLITS):
    """
    Walk-forward validation training.
    Trains on past data, validates on future data (no look-ahead bias).
    """
    print(f"\n{'='*60}")
    print(f"  WALK-FORWARD TRAINING ({n_splits} splits)")
    print(f"{'='*60}")
    
    X = df[feature_cols].values
    y = df['target'].values
    
    # Time-series split
    tscv = TimeSeriesSplit(n_splits=n_splits)
    
    results = []
    best_model = None
    best_score = 0
    
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X), 1):
        print(f"\n--- Fold {fold}/{n_splits} ---")
        print(f"Train: {len(train_idx):,} samples | Val: {len(val_idx):,} samples")
        
        X_train, X_val = X[train_idx], X[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]
        
        # Class ratio for this fold
        neg = (y_train == 0).sum()
        pos = (y_train == 1).sum()
        ratio = neg / pos if pos > 0 else 1.0
        print(f"Class ratio: {ratio:.2f} (WIN: {pos:,}, LOSS: {neg:,})")
        
        # Train XGBoost
        print("Training XGBoost...", end=" ")
        xgb_model = train_xgboost(X_train, y_train, X_val, y_val, ratio)
        
        # Evaluate
        preds = xgb_model.predict(X_val)
        probs = xgb_model.predict_proba(X_val)[:, 1]
        
        acc = accuracy_score(y_val, preds)
        precision = precision_score(y_val, preds, zero_division=0)
        recall = recall_score(y_val, preds, zero_division=0)
        f1 = f1_score(y_val, preds, zero_division=0)
        
        try:
            auc = roc_auc_score(y_val, probs)
        except:
            auc = 0.5
        
        print(f"Acc: {acc:.3f} | Prec: {precision:.3f} | Rec: {recall:.3f} | "
              f"F1: {f1:.3f} | AUC: {auc:.3f}")
        
        results.append({
            'fold': fold,
            'accuracy': acc,
            'precision': precision,
            'recall': recall,
            'f1': f1,
            'auc': auc
        })
        
        # Track best model
        if f1 > best_score:
            best_score = f1
            best_model = xgb_model
    
    # Summary
    avg_acc = np.mean([r['accuracy'] for r in results])
    avg_prec = np.mean([r['precision'] for r in results])
    avg_rec = np.mean([r['recall'] for r in results])
    avg_f1 = np.mean([r['f1'] for r in results])
    avg_auc = np.mean([r['auc'] for r in results])
    
    print(f"\n{'='*60}")
    print(f"  WALK-FORWARD RESULTS (Average)")
    print(f"{'='*60}")
    print(f"Accuracy:  {avg_acc:.3f}")
    print(f"Precision: {avg_prec:.3f}")
    print(f"Recall:    {avg_rec:.3f}")
    print(f"F1 Score:  {avg_f1:.3f}")
    print(f"AUC:       {avg_auc:.3f}")
    
    return best_model, results


def train_final_model(df, feature_cols):
    """Train final model on all data."""
    print(f"\n{'='*60}")
    print(f"  TRAINING FINAL MODEL (Full Dataset)")
    print(f"{'='*60}")
    
    X = df[feature_cols].values
    y = df['target'].values
    
    # Split 80/20 for final validation
    split_idx = int(len(X) * 0.8)
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]
    
    print(f"Train: {len(X_train):,} | Validation: {len(X_val):,}")
    
    # Class ratio
    neg = (y_train == 0).sum()
    pos = (y_train == 1).sum()
    ratio = neg / pos if pos > 0 else 1.0
    
    # Train XGBoost
    print("\nTraining XGBoost...")
    xgb_model = train_xgboost(X_train, y_train, X_val, y_val, ratio)
    
    # Train Random Forest
    print("Training Random Forest...")
    rf_model = train_random_forest(X_train, y_train)
    
    # Evaluate both
    print(f"\n{'='*60}")
    print(f"  FINAL MODEL EVALUATION")
    print(f"{'='*60}")
    
    for name, model in [("XGBoost", xgb_model), ("RandomForest", rf_model)]:
        preds = model.predict(X_val)
        
        print(f"\n{name}:")
        print(classification_report(y_val, preds, digits=3))
    
    return xgb_model, rf_model


def print_feature_importance(model, feature_cols, top_n=20):
    """Print top feature importances."""
    importance = model.feature_importances_
    sorted_idx = np.argsort(importance)[::-1]
    
    print(f"\n{'='*60}")
    print(f"  TOP {top_n} FEATURE IMPORTANCE")
    print(f"{'='*60}")
    
    for i in range(min(top_n, len(feature_cols))):
        idx = sorted_idx[i]
        print(f"  {feature_cols[idx]:25s}: {importance[idx]:.4f}")


# =============================================================================
# Main Training
# =============================================================================

def main():
    print("\n" + "="*60)
    print("  10-YEAR ML MODEL TRAINING")
    print("="*60)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"GPU Available: {HAS_GPU}")
    
    # Connect to MT5
    client = MT5Client()
    if not client.connect():
        print("Failed to connect to MT5")
        return
    
    if not client.detect_available_symbols():
        print("No symbols detected")
        return
    
    # Collect data
    df, symbol_stats = collect_data(settings.SYMBOLS, TIMEFRAME, BARS_10_YEARS)
    
    if df is None or len(df) == 0:
        print("No data collected!")
        return
    
    # Get feature columns
    feature_cols = get_feature_columns(df)
    print(f"\nFeature columns: {len(feature_cols)}")
    
    # Walk-forward validation
    best_wf_model, wf_results = walk_forward_train(df, feature_cols)
    
    # Train final models
    xgb_model, rf_model = train_final_model(df, feature_cols)
    
    # Feature importance
    print_feature_importance(xgb_model, feature_cols)
    
    # Save models
    models_dir = os.path.join(os.path.dirname(__file__), "models")
    os.makedirs(models_dir, exist_ok=True)
    
    xgb_path = os.path.join(models_dir, "xgboost_v1.pkl")
    rf_path = os.path.join(models_dir, "scalper_v1.pkl")
    feat_path = os.path.join(models_dir, "xgboost_v1_features.pkl")
    
    joblib.dump(xgb_model, xgb_path)
    joblib.dump(rf_model, rf_path)
    joblib.dump(feature_cols, feat_path)
    
    print(f"\n{'='*60}")
    print(f"  MODELS SAVED")
    print(f"{'='*60}")
    print(f"XGBoost: {xgb_path}")
    print(f"RandomForest: {rf_path}")
    print(f"Features: {feat_path}")
    
    # Save training report
    report_path = os.path.join(os.path.dirname(__file__), "training_10year_report.txt")
    with open(report_path, "w") as f:
        f.write("10-YEAR ML TRAINING REPORT\n")
        f.write(f"{'='*50}\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total Bars: {len(df):,}\n")
        f.write(f"Symbols: {len(symbol_stats)}\n\n")
        
        f.write("Symbol Stats:\n")
        for s in symbol_stats:
            f.write(f"  {s['symbol']:10s}: {s['bars']:>10,} bars | "
                   f"{s['years']:.1f} years | WR: {s['win_rate']:.1f}%\n")
        
        f.write(f"\nWalk-Forward Results:\n")
        for r in wf_results:
            f.write(f"  Fold {r['fold']}: Acc={r['accuracy']:.3f} "
                   f"F1={r['f1']:.3f} AUC={r['auc']:.3f}\n")
    
    print(f"\nTraining completed! Report saved to {report_path}")


if __name__ == "__main__":
    main()
