import os
import numpy as np
from stable_baselines3 import PPO
from analysis.mt5_trading_env import MT5TradingEnv

# Centralized path for the RL model
MODEL_PATH = "f:/mt5/models/ppo_position_manager.zip"

class PPOPositionManager:
    """
    Substitutes the outdated DQN agent to utilize Proximal Policy Optimization (PPO).
    Designed to be evaluated live on the 5-dimensional state feature vector:
    [price_change, volatility, time_in_trade, regime, portfolio_pnl]
    """
    def __init__(self):
        self.env = MT5TradingEnv()
        self.model = None
        self._load_model()
        
    def _load_model(self):
        if os.path.exists(MODEL_PATH) or os.path.exists(MODEL_PATH.replace('.zip', '')):
            try:
                self.model = PPO.load(MODEL_PATH, env=self.env, device='cpu')
                print(f"[PPO] Loaded model from {MODEL_PATH}")
            except Exception as e:
                print(f"[PPO] Error loading model: {e}")
                
        if self.model is None:
            # Initialize new empty model
            os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
            self.model = PPO("MlpPolicy", self.env, verbose=0, device='cpu')
            print("[PPO] Initialized new model architecture.")
            
    def get_trade_signal(self, state: np.ndarray) -> str:
        """
        Takes the 5D state array and returns a discrete action string.
        Actions: 0: HOLD, 1: INCREASE, 2: REDUCE_50%, 3: EXIT
        """
        if self.model is None:
            return "HOLD"
            
        # Ensure array integrity
        state = np.nan_to_num(state, nan=0.0, posinf=0.0, neginf=0.0)
            
        action, _states = self.model.predict(state, deterministic=True)
        act_val = int(action)
        
        # Mapped to match user request specifications
        action_map = {
            0: "HOLD",
            1: "INCREASE",
            2: "REDUCE_50%",
            3: "EXIT"
        }
        
        return action_map.get(act_val, "HOLD")

# Singleton Architecture
_ppo_manager = None

def get_ppo_manager():
    global _ppo_manager
    if _ppo_manager is None:
        _ppo_manager = PPOPositionManager()
    return _ppo_manager
