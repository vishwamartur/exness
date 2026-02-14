"""
Train LSTM — Multi-symbol support.

Trains separate LSTM models for key instruments:
- EURUSD (default)
- XAUUSD (Gold)
- BTCUSD (Bitcoin)
- GBPUSD

Each model gets its own weights, scaler, and feature columns file.
Run: python train_lstm.py                    (trains default EURUSD)
Run: python train_lstm.py XAUUSD BTCUSD     (trains specific symbols)
Run: python train_lstm.py --all              (trains all key symbols)
"""

import torch
import torch.nn as nn
import torch.optim as optim
import pandas as pd
import numpy as np
import joblib
import os
import sys
import argparse
from sklearn.preprocessing import MinMaxScaler
from torch.utils.data import DataLoader, TensorDataset

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from strategy import features
from strategy.lstm_model import BiLSTMWithAttention
from data import loader
from config import settings

# Key symbols to train models for
KEY_SYMBOLS = ["EURUSD", "XAUUSD", "BTCUSD", "GBPUSD"]


def create_sequences(data, target, seq_length):
    xs, ys = [], []
    for i in range(len(data) - seq_length):
        x = data[i : i + seq_length]
        y = target[i + seq_length]
        xs.append(x)
        ys.append(y)
    return np.array(xs), np.array(ys)


def train_lstm(symbol, epochs=50, batch_size=32, lr=0.001, timeframe="M15"):
    """Trains an LSTM model for a specific symbol."""
    print(f"\n{'='*50}")
    print(f"  TRAINING LSTM: {symbol} on {timeframe}")
    print(f"{'='*50}")
    
    # 1. Load Data — use M15 to match live strategy
    df = loader.get_historical_data(symbol, timeframe, settings.HISTORY_BARS)
    if df is None or len(df) < 200:
        print(f"Failed to load sufficient data for {symbol}. Skipping.")
        return False
    
    print(f"Loaded {len(df)} bars")
        
    # 2. Add Features
    df = features.add_technical_features(df)
    df = df.dropna()
    
    # 3. Prepare Data
    drop_cols = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 
                 'spread', 'real_volume', 'target']
    feature_cols = [c for c in df.columns if c not in drop_cols]
    
    target_col = 'close'
    
    X = df[feature_cols].values
    y = df[target_col].values.reshape(-1, 1)
    
    print(f"Features: {len(feature_cols)} | Samples: {len(X)}")
    
    # 4. Scale Data
    feature_scaler = MinMaxScaler()
    target_scaler = MinMaxScaler()
    
    X_scaled = feature_scaler.fit_transform(X)
    y_scaled = target_scaler.fit_transform(y)
    
    # 5. Create Sequences
    seq_length = settings.LSTM_SEQ_LENGTH
    X_seq, y_seq = create_sequences(X_scaled, y_scaled, seq_length)
    
    if len(X_seq) < 100:
        print(f"Not enough sequences ({len(X_seq)}). Need at least 100.")
        return False
    
    # 6. Train/Test Split (time-based)
    train_size = int(len(X_seq) * 0.8)
    X_train, X_test = X_seq[:train_size], X_seq[train_size:]
    y_train, y_test = y_seq[:train_size], y_seq[train_size:]
    
    # 7. Convert to Tensors
    train_data = TensorDataset(
        torch.from_numpy(X_train).float(), 
        torch.from_numpy(y_train).float()
    )
    test_data = TensorDataset(
        torch.from_numpy(X_test).float(), 
        torch.from_numpy(y_test).float()
    )
    
    train_loader = DataLoader(train_data, shuffle=True, batch_size=batch_size)
    test_loader = DataLoader(test_data, batch_size=batch_size)
    
    # 8. Initialize Model
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Device: {device}")
    
    model = BiLSTMWithAttention(
        input_size=X.shape[1],
        hidden_size=64,
        num_layers=2,
        device=device
    ).to(device)
    
    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=lr)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
    
    # 9. Training Loop with early stopping
    best_loss = float('inf')
    patience = 10
    patience_counter = 0
    
    for epoch in range(epochs):
        model.train()
        train_loss = 0
        for X_batch, y_batch in train_loader:
            X_batch, y_batch = X_batch.to(device), y_batch.to(device)
            
            optimizer.zero_grad()
            output = model(X_batch)
            loss = criterion(output, y_batch)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            
            train_loss += loss.item()
            
        # Validation
        model.eval()
        val_loss = 0
        with torch.no_grad():
            for X_batch, y_batch in test_loader:
                X_batch, y_batch = X_batch.to(device), y_batch.to(device)
                output = model(X_batch)
                loss = criterion(output, y_batch)
                val_loss += loss.item()
        
        train_loss /= len(train_loader)
        val_loss /= len(test_loader)
        
        scheduler.step(val_loss)
        
        if (epoch + 1) % 5 == 0 or epoch == 0:
            print(f"Epoch {epoch+1}/{epochs} | Train: {train_loss:.6f} | Val: {val_loss:.6f}")
        
        if val_loss < best_loss:
            best_loss = val_loss
            patience_counter = 0
            # Save Model
            models_dir = os.path.join(os.path.dirname(__file__), "models")
            os.makedirs(models_dir, exist_ok=True)
            torch.save(model.state_dict(), os.path.join(models_dir, f"lstm_{symbol}.pth"))
            joblib.dump(feature_scaler, os.path.join(models_dir, f"lstm_{symbol}_scaler.pkl"))
            joblib.dump(target_scaler, os.path.join(models_dir, f"lstm_{symbol}_target_scaler.pkl"))
            joblib.dump(feature_cols, os.path.join(models_dir, f"lstm_{symbol}_cols.pkl"))
        else:
            patience_counter += 1
            if patience_counter >= patience:
                print(f"Early stopping at epoch {epoch+1}")
                break
    
    print(f"✓ {symbol} training complete. Best val loss: {best_loss:.6f}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train LSTM models")
    parser.add_argument('symbols', nargs='*', default=[settings.SYMBOL],
                        help='Symbols to train (e.g., EURUSD XAUUSD BTCUSD)')
    parser.add_argument('--all', action='store_true', 
                        help='Train all key symbols')
    parser.add_argument('--epochs', type=int, default=50)
    parser.add_argument('--timeframe', type=str, default='M15')
    args = parser.parse_args()
    
    if not loader.initial_connect():
        print("Failed to connect to MT5.")
        sys.exit(1)
    
    symbols_to_train = KEY_SYMBOLS if args.all else args.symbols
    
    print(f"Training LSTM for: {', '.join(symbols_to_train)}")
    
    for sym in symbols_to_train:
        train_lstm(sym, epochs=args.epochs, timeframe=args.timeframe)
    
    print(f"\n{'='*50}")
    print("  ALL TRAINING COMPLETE")
    print(f"{'='*50}")
