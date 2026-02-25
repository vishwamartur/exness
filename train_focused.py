"""
Train ML Models on Focused Dataset (Forex + Gold + Crypto)
==========================================================

Trains XGBoost and Random Forest models specifically on:
- Forex Majors: EURUSD, GBPUSD, USDJPY
- Gold: XAUUSD
- Crypto: BTCUSD, ETHUSD

This ensures the models learn patterns specific to these markets only.
"""

import pandas as pd
import numpy as np
import joblib
import os
import sys
from datetime import datetime, timezone
import warnings
warnings.filterwarnings('ignore')

sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import settings
from market_data import loader
from strategy import features
from execution.mt5_client import MT5Client

import xgboost as xgb
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

# Focused symbol list
FOCUSED_SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD', 'BTCUSD', 'ETHUSD']

# Training parameters
TIMEFRAME = "H1"  # H1 for trend trading
BARS_PER_SYMBOL = 50000  # ~5 years of H1 data
MIN_BARS = 10000

# ATR-based labeling
ATR_TP_MULT = 8.0   # Wide TP for trend
ATR_SL_MULT = 2.5   # Wide SL
HORIZON_BARS = 48   # 48 hours look-ahead


def apply_trend_labels(df, atr_tp=ATR_TP_MULT, atr_sl=ATR_SL_MULT, horizon=HORIZON_BARS):
    """Label data for trend trading (longer hold times)."""
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
        tp_dist = atr[i] * atr_tp
        sl_dist = atr[i] * atr_sl
        
        # Check for both long and short profitability
        long_tp_hit = False
        long_sl_hit = False
        short_tp_hit = False
        short_sl_hit = False
        
        for j in range(i + 1, min(i + 1 + horizon, len(df))):
            # Long checks
            if not long_tp_hit and not long_sl_hit:
                if highs[j] >= entry + tp_dist:
                    long_tp_hit = True
                if lows[j] <= entry - sl_dist:
                    long_sl_hit = True
            
            # Short checks
            if not short_tp_hit and not short_sl_hit:
                if lows[j] <= entry - tp_dist:
                    short_tp_hit = True
                if highs[j] >= entry + sl_dist:
                    short_sl_hit = True
        
        # Label: 1 = buy profitable, -1 = sell profitable, 0 = neither
        if long_tp_hit and not long_sl_hit:
            labels.append(1)
        elif short_tp_hit and not short_sl_hit:
            labels.append(-1)
        else:
            labels.append(0)
    
    return pd.Series(labels, index=df.index)


def add_trend_features(df):
    """Add features optimized for trend trading."""
    df = features.add_technical_features(df)
    
    # Longer-term trend indicators
    if 'close' in df.columns:
        # Multiple timeframe trends
        df['sma_50'] = df['close'].rolling(50).mean()
        df['sma_200'] = df['close'].rolling(200).mean()
        df['ema_50'] = df['close'].ewm(span=50).mean()
        
        # Trend strength
        df['trend_strength'] = (df['close'] - df['sma_200']) / df['sma_200'] * 100
        
        # Price position in range
        df['high_50'] = df['high'].rolling(50).max()
        df['low_50'] = df['low'].rolling(50).min()
        df['range_position'] = (df['close'] - df['low_50']) / (df['high_50'] - df['low_50'])
        
        # Volatility trends
        df['volatility_20'] = df['close'].rolling(20).std()
        df['volatility_50'] = df['close'].rolling(50).std()
        df['volatility_ratio'] = df['volatility_20'] / df['volatility_50']
    
    # MACD with longer periods
    if 'close' in df.columns:
        ema_12 = df['close'].ewm(span=12).mean()
        ema_26 = df['close'].ewm(span=26).mean()
        df['macd'] = ema_12 - ema_26
        df['macd_signal'] = df['macd'].ewm(span=9).mean()
        df['macd_hist'] = df['macd'] - df['macd_signal']
    
    # RSI with multiple periods
    if 'close' in df.columns:
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # RSI trend
        df['rsi_sma'] = df['rsi'].rolling(14).mean()
    
    return df


def train():
    print("="*60)
    print("  FOCUSED MODEL TRAINING")
    print("="*60)
    print(f"Symbols: {FOCUSED_SYMBOLS}")
    print(f"Timeframe: {TIMEFRAME}")
    print(f"Target: {BARS_PER_SYMBOL:,} bars per symbol")
    print()
    
    # Connect to MT5
    client = MT5Client()
    if not client.connect():
        print("Failed to connect to MT5")
        return
    
    all_data = []
    symbol_stats = []
    
    for symbol in FOCUSED_SYMBOLS:
        print(f"\n[{symbol}] Fetching data...", end=" ")
        
        try:
            df = loader.get_historical_data(symbol, TIMEFRAME, BARS_PER_SYMBOL)
            
            if df is None or len(df) < MIN_BARS:
                print(f"Insufficient data ({len(df) if df is not None else 0} bars)")
                continue
            
            print(f"Got {len(df):,} bars")
            
            # Add features
            df = add_trend_features(df)
            
            # Add labels
            df['target'] = apply_trend_labels(df)
            
            # Add symbol encoding
            df['symbol_id'] = hash(symbol) % 1000
            
            # Remove unlabeled data
            df = df.iloc[:-HORIZON_BARS-1].dropna()
            
            if len(df) > MIN_BARS:
                all_data.append(df)
                
                # Stats
                buys = (df['target'] == 1).sum()
                sells = (df['target'] == -1).sum()
                neutral = (df['target'] == 0).sum()
                
                print(f"  Labels: BUY {buys} | SELL {sells} | NEUTRAL {neutral}")
                
                symbol_stats.append({
                    'symbol': symbol,
                    'bars': len(df),
                    'buy_pct': buys / len(df) * 100,
                    'sell_pct': sells / len(df) * 100
                })
            
        except Exception as e:
            print(f"Error: {e}")
            continue
    
    if not all_data:
        print("\nNo data collected!")
        return
    
    # Combine all data
    full_df = pd.concat(all_data, ignore_index=True)
    
    print(f"\n{'='*60}")
    print("  DATA SUMMARY")
    print(f"{'='*60}")
    print(f"Total Samples: {len(full_df):,}")
    print(f"Symbols: {len(all_data)}")
    
    # Feature columns
    exclude = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 
               'spread', 'real_volume', 'target']
    feature_cols = [c for c in full_df.columns if c not in exclude]
    
    print(f"Features: {len(feature_cols)}")
    
    # Prepare data
    X = full_df[feature_cols].values
    y = full_df['target'].values
    
    # Convert to binary classification (1 = profitable, 0 = not)
    y_binary = (y != 0).astype(int)
    
    print(f"\nClass Distribution:")
    print(f"  Profitable: {(y_binary == 1).sum():,} ({(y_binary == 1).mean()*100:.1f}%)")
    print(f"  Not Profitable: {(y_binary == 0).sum():,} ({(y_binary == 0).mean()*100:.1f}%)")
    
    # Time-series split
    split_idx = int(len(X) * 0.8)
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y_binary[:split_idx], y_binary[split_idx:]
    
    print(f"\nTrain: {len(X_train):,} | Test: {len(X_test):,}")
    
    # Calculate class weight
    pos = (y_train == 1).sum()
    neg = (y_train == 0).sum()
    scale_pos_weight = neg / pos if pos > 0 else 1.0
    
    # Train XGBoost
    print(f"\n{'='*60}")
    print("  TRAINING XGBOOST")
    print(f"{'='*60}")
    
    xgb_model = xgb.XGBClassifier(
        n_estimators=500,
        max_depth=8,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos_weight,
        random_state=42,
        n_jobs=-1,
        eval_metric='aucpr',
        early_stopping_rounds=50
    )
    
    eval_set = [(X_train, y_train), (X_test, y_test)]
    xgb_model.fit(X_train, y_train, eval_set=eval_set, verbose=False)
    
    # Evaluate
    preds = xgb_model.predict(X_test)
    print(f"\nAccuracy: {accuracy_score(y_test, preds):.3f}")
    print(classification_report(y_test, preds, target_names=['Not Profitable', 'Profitable']))
    
    # Feature importance
    importance = xgb_model.feature_importances_
    top_features = sorted(zip(feature_cols, importance), key=lambda x: x[1], reverse=True)[:15]
    
    print(f"\nTop 15 Features:")
    for feat, imp in top_features:
        print(f"  {feat:25s}: {imp:.4f}")
    
    # Train Random Forest
    print(f"\n{'='*60}")
    print("  TRAINING RANDOM FOREST")
    print(f"{'='*60}")
    
    rf_model = RandomForestClassifier(
        n_estimators=300,
        max_depth=12,
        min_samples_split=20,
        min_samples_leaf=10,
        class_weight='balanced',
        random_state=42,
        n_jobs=-1
    )
    
    rf_model.fit(X_train, y_train)
    
    # Evaluate RF
    rf_preds = rf_model.predict(X_test)
    print(f"\nAccuracy: {accuracy_score(y_test, rf_preds):.3f}")
    print(classification_report(y_test, rf_preds, target_names=['Not Profitable', 'Profitable']))
    
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
    print("  MODELS SAVED")
    print(f"{'='*60}")
    print(f"XGBoost: {xgb_path}")
    print(f"Random Forest: {rf_path}")
    print(f"Features: {feat_path}")
    
    # Save training report
    report_path = os.path.join(os.path.dirname(__file__), "focused_training_report.txt")
    with open(report_path, "w") as f:
        f.write("FOCUSED MODEL TRAINING REPORT\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Symbols: {', '.join(FOCUSED_SYMBOLS)}\n")
        f.write(f"Timeframe: {TIMEFRAME}\n")
        f.write(f"Total Samples: {len(full_df):,}\n\n")
        
        f.write("Symbol Stats:\n")
        for s in symbol_stats:
            f.write(f"  {s['symbol']}: {s['bars']:,} bars | Buy: {s['buy_pct']:.1f}% | Sell: {s['sell_pct']:.1f}%\n")
        
        f.write(f"\nTop Features:\n")
        for feat, imp in top_features:
            f.write(f"  {feat}: {imp:.4f}\n")
    
    print(f"\nReport saved to {report_path}")


if __name__ == "__main__":
    train()
