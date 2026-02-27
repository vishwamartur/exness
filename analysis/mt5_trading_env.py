import gymnasium as gym
from gymnasium import spaces
import numpy as np
import pandas as pd

class MT5TradingEnv(gym.Env):
    """
    Custom Environment that follows gymnasium interface.
    Optimizes exit timing and position sizing mid-trade for Proximal Policy Optimization (PPO).
    """
    metadata = {"render_modes": ["human"]}

    def __init__(self, df: pd.DataFrame = None, commission_pct=0.0001):
        super(MT5TradingEnv, self).__init__()
        
        self.df = df
        self.commission_pct = commission_pct
        
        # State: [price_change(%), volatility(%), time_in_trade(bars), regime(aligned), portfolio_pnl(%)]
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(5,), dtype=np.float32
        )
        
        # Actions: 0: HOLD, 1: INCREASE (scale in), 2: REDUCE_50% (scale out), 3: EXIT
        self.action_space = spaces.Discrete(4)
        
        self.current_step = 0
        self.entry_index = 0
        self.entry_price = 0.0
        self.position_size = 0.0
        self.direction = 1  # 1 for BUY, -1 for SELL
        self.tracking_commissions = 0.0
        self.pnl_history = []
        self.time_in_trade = 0
        
    def set_data(self, df: pd.DataFrame):
        self.df = df
        
    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        
        # If no dataframe or empty, return default reset
        if self.df is None or len(self.df) < 50:
            return np.zeros(5, dtype=np.float32), {}
            
        # Randomly pick an entry point that leaves enough room for a trade trajectory
        self.entry_index = np.random.randint(20, len(self.df) - 50)
        self.current_step = self.entry_index
        
        self.entry_price = self.df['close'].iloc[self.current_step]
        self.direction = np.random.choice([1, -1])  # Train on both long and short holds
        self.position_size = 1.0
        self.time_in_trade = 0
        self.pnl_history = []
        self.tracking_commissions = self.commission_pct  # Initial entry commission
        
        return self._get_obs(), {}
        
    def _get_obs(self):
        if self.df is None or self.current_step >= len(self.df):
            return np.zeros(5, dtype=np.float32)
            
        current_row = self.df.iloc[self.current_step]
        current_price = current_row['close']
        
        # 1. Price Change (in %) relative to direction
        price_change = ((current_price - self.entry_price) / self.entry_price) * 100.0 * self.direction
        
        # 2. Volatility (ATR normalized to percentage)
        volatility = current_row.get('atr', current_price * 0.001) / current_price * 100.0
        
        # 3. Time in trade
        # Update is executed per `.step()`, we just read it here.
        time_elapsed = float(self.time_in_trade)
        
        # 4. Regime 
        # Attempt to infer trend alignment from basic EMAs or ADX if present; else default 0
        adx = current_row.get('adx', 20)
        sma20 = current_row.get('sma20', current_price)
        sma50 = current_row.get('sma50', current_price)
        
        if adx > 25:
            base_regime = 1.0 if sma20 > sma50 else -1.0
        else:
            base_regime = 0.0
            
        # Align with trade (1.0 = Strong Trend with Trade, -1.0 = Strong Trend Against Trade)
        aligned_regime = base_regime * self.direction
            
        # 5. Portfolio PnL (%)
        current_pnl = (price_change * self.position_size) - self.tracking_commissions
        
        obs = np.array([
            price_change,
            volatility,
            time_elapsed,
            aligned_regime,
            current_pnl
        ], dtype=np.float32)
        
        # Guard against NaN
        obs = np.nan_to_num(obs)
        return obs
        
    def step(self, action):
        terminated = False
        truncated = False
        reward = 0.0
        
        if self.df is None or self.current_step >= len(self.df) - 1:
            truncated = True
            return self._get_obs(), 0.0, terminated, truncated, {}
            
        # Current Unrealized prior to action
        current_price = self.df['close'].iloc[self.current_step]
        unrealized_pnl = ((current_price - self.entry_price) / self.entry_price) * 100.0 * self.direction * self.position_size
        
        # Process discrete action
        if action == 1:  # INCREASE
            if self.position_size < 2.0:
                self.position_size += 0.5
                self.tracking_commissions += self.commission_pct * 0.5
        elif action == 2:  # REDUCE_50%
            if self.position_size > 0.25:
                self.tracking_commissions += self.commission_pct * 0.5
                self.position_size *= 0.5
        elif action == 3:  # EXIT
            terminated = True
            
        # Advance simulation
        self.current_step += 1
        self.time_in_trade += 1
        
        # New Unreazlied
        next_price = self.df['close'].iloc[self.current_step]
        new_unrealized_pnl = ((next_price - self.entry_price) / self.entry_price) * 100.0 * self.direction * self.position_size
        
        step_pnl = new_unrealized_pnl - unrealized_pnl
        self.pnl_history.append(step_pnl)
        
        # Reward Logic: R = (final_pnl * sharpe) - tracking_commissions
        if terminated or self.current_step >= len(self.df) - 1:
            terminated = True
            final_pnl = new_unrealized_pnl - self.tracking_commissions
            
            pnl_array = np.array(self.pnl_history)
            if len(pnl_array) > 2 and np.std(pnl_array) > 0:
                # Approximate pseudo-Sharpe ratio
                sharpe_ratio = np.mean(pnl_array) / np.std(pnl_array) * np.sqrt(len(pnl_array))
            else:
                sharpe_ratio = 0.0
                
            clipped_sharpe = np.clip(sharpe_ratio, -3.0, 3.0)
            
            # The user's specific requested objective mapping:
            # Reward shaping: R = (final_pnl * sharpe) - transaction_costs
            # In our case, final_pnl already subtracted tracking_commissions, so:
            reward = final_pnl * max(0.1, clipped_sharpe)
            
            # Additional penalty if catastrophic drawdown
            if final_pnl < -5.0:
                reward -= 5.0
        else:
            # Small step reward directly reflecting PnL delta to encourage holding profitable trades
            reward = step_pnl * 0.1
            
        obs = self._get_obs()
        return obs, reward, terminated, truncated, {}
