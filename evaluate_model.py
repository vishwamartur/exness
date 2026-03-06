import torch
import numpy as np
import joblib
import os
import sys
import argparse
from tqdm import tqdm

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from strategy import features
from strategy.lstm_model import BiLSTMWithAttention
from market_data import loader
from config import settings
from train_lstm import create_sequences_fast

def evaluate_model(model_name="lstm_global", timeframe="M15", num_bars=2000):
    """
    Evaluates a saved LSTM model on recent data and calculates:
    - Mean Squared Error (MSE)
    - Mean Absolute Error (MAE)
    - Directional Accuracy (Did it predict the up/down movement correctly?)
    """
    print("\n" + "=" * 60)
    print(f"  MODEL EVALUATION: {model_name}.pth")
    print("=" * 60)
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"[INFO] Device: {device}\n")
    
    models_dir = os.path.join(os.path.dirname(__file__), "models")
    model_path = os.path.join(models_dir, f"{model_name}.pth")
    scaler_path = os.path.join(models_dir, f"{model_name}_scaler.pkl")
    target_scaler_path = os.path.join(models_dir, f"{model_name}_target_scaler.pkl")
    cols_path = os.path.join(models_dir, f"{model_name}_cols.pkl")
    
    # Check if files exist
    for p in [model_path, scaler_path, target_scaler_path, cols_path]:
        if not os.path.exists(p):
            print(f"[ERROR] Missing file: {p}")
            return False
            
    # Load scalers and columns
    print("[STAGE 1] Loading Checkpoints & Scalers...")
    feature_scaler = joblib.load(scaler_path)
    target_scaler = joblib.load(target_scaler_path)
    feature_cols = joblib.load(cols_path)
    
    n_features = len(feature_cols)
    print(f"  ✓ Features expected: {n_features}")
    
    # Init Model
    model = BiLSTMWithAttention(
        input_size=n_features,
        hidden_size=64,
        num_layers=2,
        device=device
    ).to(device)
    
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.eval()
    print(f"  ✓ Model Architecture Loaded\n")

    # Connect to MT5
    if not loader.initial_connect():
        print("[ERROR] Failed to connect to MT5.")
        return False
        
    print(f"[STAGE 2] Fetching recent data ({num_bars} bars) for evaluation...")
    symbols = settings.ALL_BASE_SYMBOLS
    
    total_samples = 0
    total_mse = 0.0
    total_mae = 0.0
    correct_direction = 0
    total_direction_samples = 0
    
    seq_length = settings.LSTM_SEQ_LENGTH
    
    symbol_metrics = {}

    for symbol in tqdm(symbols, desc="Evaluating Symbols"):
        try:
            result = loader.get_historical_data(symbol, timeframe, num_bars)
            if isinstance(result, tuple):
                df, _ = result
            else:
                df = result
                
            if df is None or len(df) <= seq_length:
                continue
                
            df = features.add_technical_features(df)
            df = df.dropna()
            
            if len(df) <= seq_length:
                continue
                
            # Verify columns
            missing_cols = [c for c in feature_cols if c not in df.columns]
            if missing_cols:
                print(f"\n  [WARN] {symbol} missing columns: {missing_cols}")
                continue
                
            X_raw = df[feature_cols].values.astype(np.float32)
            y_raw = df['close'].values.reshape(-1, 1).astype(np.float32)
            
            # Use unscaled prices for directional accuracy base reference
            actual_prices = df['close'].values.astype(np.float32)
            
            # Scale
            X_scaled = feature_scaler.transform(X_raw)
            y_scaled = target_scaler.transform(y_raw)
            
            # Create sequences
            X_seq, y_seq = create_sequences_fast(X_scaled, y_scaled, seq_length)
            
            if len(X_seq) == 0:
                continue
                
            X_tensor = torch.as_tensor(X_seq, dtype=torch.float32).to(device)
            y_tensor = torch.as_tensor(y_seq, dtype=torch.float32).to(device)
            
            # Inference in batches to avoid OOM
            batch_size = 512
            predictions_scaled = []
            
            with torch.no_grad():
                for i in range(0, len(X_tensor), batch_size):
                    batch_X = X_tensor[i:i+batch_size]
                    batch_pred = model(batch_X)
                    predictions_scaled.append(batch_pred.cpu().numpy())
                    
            predictions_scaled = np.vstack(predictions_scaled)
            
            # Inverse transform
            predictions = target_scaler.inverse_transform(predictions_scaled)
            actuals = target_scaler.inverse_transform(y_seq)
            
            # Base reference price for each sequence (last close price in the sequence window)
            # The target is the next period's close price
            # So the movement is target - base_reference
            # df['close'] has length N. Sequences are from index `seq_length` to N
            
            base_prices = actual_prices[seq_length-1 : seq_length-1 + len(y_seq)].reshape(-1, 1)
            
            # Calculate Direction
            actual_movement = actuals - base_prices
            predicted_movement = predictions - base_prices
            
            is_correct_direction = (actual_movement * predicted_movement) > 0
            
            # Calculate Metrics
            mse = np.mean((predictions - actuals) ** 2)
            mae = np.mean(np.abs(predictions - actuals))
            direction_acc = np.mean(is_correct_direction) * 100
            
            symbol_metrics[symbol] = {
                'samples': len(predictions),
                'mse': mse,
                'mae': mae,
                'dir_acc': direction_acc
            }
            
            total_mse += np.sum((predictions - actuals) ** 2)
            total_mae += np.sum(np.abs(predictions - actuals))
            correct_direction += np.sum(is_correct_direction)
            
            total_samples += len(predictions)
            total_direction_samples += len(is_correct_direction)
            
            # Memory cleanup
            del X_tensor, y_tensor, X_seq, y_seq, X_scaled, y_scaled, X_raw, y_raw
            
        except Exception as e:
            print(f"\n  [ERROR] {symbol} Evaluation failed: {e}")
            
    # Compile Final Overrall Metrics
    if total_samples > 0:
        final_mse = total_mse / total_samples
        final_mae = total_mae / total_samples
        final_dir_acc = (correct_direction / total_direction_samples) * 100
        
        print("\n" + "=" * 60)
        print("  EVALUATION RESULTS")
        print("=" * 60)
        print(f"  Total Samples Evaluated: {total_samples:,}")
        print("-" * 60)
        print(f"  Overall MSE (Price):     {final_mse:.6f}")
        print(f"  Overall MAE (Price):     {final_mae:.6f} pips/units")
        print(f"  Directional Accuracy:    {final_dir_acc:.2f}%")
        print("-" * 60)
        
        # Sort symbols by directional accuracy
        print("  Performance by Symbol (Top 10):")
        sorted_syms = sorted(symbol_metrics.items(), key=lambda x: x[1]['dir_acc'], reverse=True)
        for sym, m in sorted_syms[:10]:
            print(f"    {sym:<10} | Acc: {m['dir_acc']:>5.2f}% | MAE: {m['mae']:.5f} | N: {m['samples']}")
            
        if len(sorted_syms) > 10:
            print("  Performance by Symbol (Bottom 5):")
            for sym, m in sorted_syms[-5:]:
                 print(f"    {sym:<10} | Acc: {m['dir_acc']:>5.2f}% | MAE: {m['mae']:.5f} | N: {m['samples']}")
            
    else:
        print("\n[ERROR] No data was evaluated.")
        
    print("\n" + "=" * 60)
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate Trained LSTM Model")
    parser.add_argument('--model', type=str, default='lstm_global', help='Base model name (e.g. lstm_global)')
    parser.add_argument('--timeframe', type=str, default='M15', help='Timeframe to evaluate on')
    parser.add_argument('--bars', type=int, default=2000, help='Number of recent bars to evaluate')
    args = parser.parse_args()
    
    evaluate_model(
        model_name=args.model,
        timeframe=args.timeframe,
        num_bars=args.bars
    )
