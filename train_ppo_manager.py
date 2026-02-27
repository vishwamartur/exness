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

def train_ppo():
    print("==================================================")
    print("  INITIALIZING OFFLINE PPO TRAINING RUN")
    print("==================================================")
    
    # Target symbols for robust training
    train_symbols = ['EURUSD', 'GBPUSD', 'BTCUSD', 'XAUUSD']
    total_timesteps = 25000
    
    # Initialize env
    env = MT5TradingEnv()
    
    # Collect all recent bars across symbols and create a master training dataframe
    master_dfs = []
    
    for symbol in train_symbols:
        print(f"[DATA] Fetching history for {symbol}...")
        df = loader.get_historical_data(symbol, settings.TIMEFRAME, 5000) # Deep lookback
        if df is not None and len(df) > 100:
            df = features.add_technical_features(df)
            df.dropna(inplace=True)
            master_dfs.append(df)
            
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
