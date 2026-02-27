"""
Offline Training Script for PPO Position Manager
Loads recent historical market data and trains the stable-baselines3 model to optimize exits based on the custom Gym Environment.
"""
import os
import pandas as pd
from stable_baselines3 import PPO
from analysis.mt5_trading_env import MT5TradingEnv
from market_data import loader
from strategy import features
from config import settings

def _get_asset_class(symbol):
    if symbol in getattr(settings, 'SYMBOLS_CRYPTO', []): return 'crypto'
    elif symbol in getattr(settings, 'SYMBOLS_COMMODITIES', []): return 'commodity'
    return 'forex'

def train_ppo():
    print("==================================================")
    print("  INITIALIZING OFFLINE PPO TRAINING RUN")
    print("==================================================")
    import sqlite3
    import datetime
    
    # Target symbols for robust training
    train_symbols = ['EURUSD', 'GBPUSD', 'BTCUSD', 'XAUUSD']
    total_timesteps = 25000
    
    # Initialize env
    env = MT5TradingEnv()
    
    # Online Learning: Retrain on recent trades from database
    master_dfs = []
    
    try:
        conn = sqlite3.connect('f:/mt5/trade_journal.db')
        # Fetch the last 100 trades to build environment state distributions
        query = "SELECT symbol, entry_time, close_time FROM trades WHERE outcome IN ('WIN', 'LOSS') ORDER BY entry_time DESC LIMIT 100"
        recent_trades = pd.read_sql_query(query, conn)
        conn.close()
        
        if len(recent_trades) > 0:
            print(f"[DATA] Found {len(recent_trades)} recent trades in journal for Online Learning.")
            
            # Fetch surrounding price action for these specific trades to train PPO Exits
            for _, row in recent_trades.iterrows():
                symbol = row['symbol']
                # Skip if we don't have basic features implemented for the asset class
                if symbol not in train_symbols and _get_asset_class(symbol) == 'commodity':
                     pass
                     
                print(f"[DATA] Fetching context window around trade for {symbol}...")
                # In a live system, you'd fetch by exact Datetime bounds.
                # For this script we will pull a localized 1000 bar chunk.
                df = loader.get_historical_data(symbol, settings.TIMEFRAME, 500)
                if df is not None and len(df) > 100:
                    df = features.add_technical_features(df)
                    df.dropna(inplace=True)
                    master_dfs.append(df)
        else:
            print("[DATA] No recent trades in journal. Falling back to general history.")
            for symbol in train_symbols:
                df = loader.get_historical_data(symbol, settings.TIMEFRAME, 1000)
                if df is not None and len(df) > 100:
                    df = features.add_technical_features(df)
                    df.dropna(inplace=True)
                    master_dfs.append(df)
                    
    except Exception as e:
        print(f"[ERROR] DB error during Online Learning phase: {e}")
        return

    if not master_dfs:
        print("[ERROR] Failed to fetch any historical data for training.")
        return
        
    # Concatenate them for generic structural learning (the Env triggers random indexing inside boundaries)
    combined_df = pd.concat(master_dfs, ignore_index=True)
    env.set_data(combined_df)
    
    print(f"\n[ENV] Configured master environment with {len(combined_df)} pooled sequence bars.")
    
    model_path = "f:/mt5/models/ppo_position_manager.zip"
    
    if os.path.exists(model_path) or os.path.exists(model_path.replace('.zip', '')):
        print(f"[PPO] Loading existing model from {model_path} to augment learning...")
        model = PPO.load(model_path, env=env)
    else:
        print(f"[PPO] Initializing brand new PPO architecture...")
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        model = PPO("MlpPolicy", env, verbose=1, tensorboard_log="./tensorboard_logs/")
        
    print(f"\n[TRAIN] Commencing specific PPO optimization for {total_timesteps} steps.")
    print("[TRAIN] Learning target: Maximize Reward via optimal Sharpe mid-trade Exits.")
    
    try:
        model.learn(total_timesteps=total_timesteps, progress_bar=True)
        model.save(model_path)
        print(f"\n✅ [SUCCESS] Model weights saved securely to {model_path}")
    except Exception as e:
        print(f"\n❌ [ERROR] Optimization interrupted: {e}")

if __name__ == "__main__":
    import asyncio
    
    # Wrapper to handle async loader compatibility
    loop = asyncio.get_event_loop()
    if loop.is_running():
        train_ppo()
    else:
        loop.run_until_complete(asyncio.sleep(0.1)) # warm up
        train_ppo()
