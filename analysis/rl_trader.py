"""
Reinforcement Learning Trading Agent
=====================================

Uses Deep Q-Network (DQN) to learn optimal trading decisions.
Learns from market state and rewards (PnL) to improve over time.

Key Benefits:
- Learns optimal entry/exit timing
- Adapts to changing market conditions
- Maximizes risk-adjusted returns
- Learns from both wins and losses
"""

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import random
from collections import deque, namedtuple
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional

# Check GPU availability
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"[RL] Using device: {DEVICE}")

# Experience tuple for replay buffer
Experience = namedtuple('Experience', ['state', 'action', 'reward', 'next_state', 'done'])


class DQNNetwork(nn.Module):
    """Deep Q-Network for trading decisions."""
    
    def __init__(self, state_size: int, action_size: int, hidden_size: 256):
        super(DQNNetwork, self).__init__()
        
        self.fc1 = nn.Linear(state_size, hidden_size)
        self.bn1 = nn.BatchNorm1d(hidden_size)
        self.fc2 = nn.Linear(hidden_size, hidden_size)
        self.bn2 = nn.BatchNorm1d(hidden_size)
        self.fc3 = nn.Linear(hidden_size, hidden_size // 2)
        self.bn3 = nn.BatchNorm1d(hidden_size // 2)
        self.fc4 = nn.Linear(hidden_size // 2, action_size)
        
        self.dropout = nn.Dropout(0.2)
        self.relu = nn.ReLU()
        
    def forward(self, x):
        # Handle single sample (batch norm requires batch size > 1)
        if x.size(0) == 1:
            self.eval()  # Switch to eval mode for single sample
            x = self.relu(self.fc1(x))
            x = self.dropout(x)
            x = self.relu(self.fc2(x))
            x = self.dropout(x)
            x = self.relu(self.fc3(x))
            x = self.fc4(x)
            self.train()  # Switch back to train mode
        else:
            x = self.relu(self.bn1(self.fc1(x)))
            x = self.dropout(x)
            x = self.relu(self.bn2(self.fc2(x)))
            x = self.dropout(x)
            x = self.relu(self.bn3(self.fc3(x)))
            x = self.fc4(x)
        return x


class ReplayBuffer:
    """Experience replay buffer for stable learning."""
    
    def __init__(self, capacity: int = 100000):
        self.buffer = deque(maxlen=capacity)
    
    def push(self, state, action, reward, next_state, done):
        """Add experience to buffer."""
        self.buffer.append(Experience(state, action, reward, next_state, done))
    
    def sample(self, batch_size: int) -> List[Experience]:
        """Sample random batch of experiences."""
        return random.sample(self.buffer, min(batch_size, len(self.buffer)))
    
    def __len__(self):
        return len(self.buffer)


class RLTrader:
    """
    Reinforcement Learning Trading Agent using DQN.
    
    Actions:
    - 0: HOLD (do nothing)
    - 1: BUY (open long position)
    - 2: SELL (open short position)
    - 3: CLOSE (close current position)
    
    State Features (40+ dimensions):
    - Price action (returns, volatility)
    - Technical indicators (RSI, MACD, ADX, etc.)
    - Market regime features
    - Position state (if any)
    - Time features (hour, day of week)
    """
    
    def __init__(self, state_size: int = 40, action_size: int = 4, 
                 model_path: str = "models/rl_trader.pth"):
        self.state_size = state_size
        self.action_size = action_size
        self.model_path = model_path
        
        # Hyperparameters
        self.gamma = 0.99  # Discount factor
        self.epsilon = 1.0  # Exploration rate
        self.epsilon_min = 0.01
        self.epsilon_decay = 0.995
        self.learning_rate = 0.001
        self.batch_size = 64
        self.target_update = 1000  # Update target network every N steps
        
        # Networks
        self.policy_net = DQNNetwork(state_size, action_size, 256).to(DEVICE)
        self.target_net = DQNNetwork(state_size, action_size, 256).to(DEVICE)
        self.target_net.load_state_dict(self.policy_net.state_dict())
        self.target_net.eval()
        
        # Optimizer
        self.optimizer = optim.Adam(self.policy_net.parameters(), lr=self.learning_rate)
        
        # Replay buffer
        self.memory = ReplayBuffer(capacity=100000)
        
        # Training stats
        self.steps_done = 0
        self.training_history = []
        
        # Load existing model if available
        self.load_model()
        
        print(f"[RL] Trader initialized: {state_size} states, {action_size} actions")
        print(f"[RL] Epsilon (exploration): {self.epsilon:.3f}")
    
    def extract_state(self, df, position=None, symbol: str = None) -> np.ndarray:
        """
        Extract state vector from market data.
        
        Returns 40-dimensional state vector:
        - Price features (10)
        - Technical indicators (15)
        - Volatility features (5)
        - Time features (4)
        - Position features (6)
        """
        if df is None or len(df) < 20:
            return np.zeros(self.state_size)
        
        state = []
        last = df.iloc[-1]
        
        # 1. Price features (normalized)
        returns_1 = df['close'].pct_change(1).iloc[-1] if len(df) > 1 else 0
        returns_5 = df['close'].pct_change(5).iloc[-1] if len(df) > 5 else 0
        returns_10 = df['close'].pct_change(10).iloc[-1] if len(df) > 10 else 0
        returns_20 = df['close'].pct_change(20).iloc[-1] if len(df) > 20 else 0
        
        # Price relative to moving averages
        sma20 = df['close'].rolling(20).mean().iloc[-1] if len(df) >= 20 else last['close']
        sma50 = df['close'].rolling(50).mean().iloc[-1] if len(df) >= 50 else last['close']
        
        price_vs_sma20 = (last['close'] / sma20 - 1) * 100 if sma20 > 0 else 0
        price_vs_sma50 = (last['close'] / sma50 - 1) * 100 if sma50 > 0 else 0
        
        # High/Low relative position
        high_20 = df['high'].rolling(20).max().iloc[-1]
        low_20 = df['low'].rolling(20).min().iloc[-1]
        range_20 = high_20 - low_20
        price_position = (last['close'] - low_20) / range_20 if range_20 > 0 else 0.5
        
        state.extend([
            returns_1 * 100,  # Scale up small returns
            returns_5 * 100,
            returns_10 * 100,
            returns_20 * 100,
            price_vs_sma20,
            price_vs_sma50,
            price_position,
            (last['close'] - last['open']) / last['close'] * 100 if last['close'] > 0 else 0,
            (last['high'] - last['close']) / last['close'] * 100 if last['close'] > 0 else 0,
            (last['close'] - last['low']) / last['close'] * 100 if last['close'] > 0 else 0,
        ])
        
        # 2. Technical indicators
        rsi = last.get('rsi', 50)
        macd = last.get('macd', 0)
        macd_signal = last.get('macd_signal', 0)
        adx = last.get('adx', 20)
        atr = last.get('atr', 0)
        atr_pct = (atr / last['close'] * 100) if last['close'] > 0 else 0
        
        # Bollinger Bands position
        bb_upper = last.get('bb_upper', last['close'])
        bb_lower = last.get('bb_lower', last['close'])
        bb_position = (last['close'] - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5
        
        # Stochastic
        stoch_k = last.get('stoch_k', 50)
        stoch_d = last.get('stoch_d', 50)
        
        # Volume
        vol_sma = df['tick_volume'].rolling(20).mean().iloc[-1] if 'tick_volume' in df.columns else 1
        vol_ratio = last.get('tick_volume', 1) / vol_sma if vol_sma > 0 else 1
        
        state.extend([
            rsi / 100,  # Normalize to 0-1
            macd,
            macd_signal,
            adx / 100,
            atr_pct,
            bb_position,
            stoch_k / 100,
            stoch_d / 100,
            min(vol_ratio, 5),  # Cap at 5x
            df['close'].rolling(5).std().iloc[-1] / last['close'] * 100 if last['close'] > 0 else 0,
            df['close'].rolling(20).std().iloc[-1] / last['close'] * 100 if last['close'] > 0 else 0,
            (df['high'].rolling(5).max().iloc[-1] - df['low'].rolling(5).min().iloc[-1]) / last['close'] * 100 if last['close'] > 0 else 0,
            (rsi - 50) / 50,  # Centered RSI
            1 if rsi > 70 else (-1 if rsi < 30 else 0),  # RSI signal
            1 if macd > macd_signal else -1,  # MACD signal
        ])
        
        # 3. Volatility regime
        vol_current = df['close'].rolling(20).std().iloc[-1] if len(df) >= 20 else 0
        vol_mean = df['close'].rolling(100).std().mean() if len(df) >= 100 else vol_current
        vol_regime = vol_current / vol_mean if vol_mean > 0 else 1
        
        state.extend([
            min(vol_regime, 3),  # Cap at 3x normal
            1 if vol_regime > 1.5 else 0,  # High vol
            1 if vol_regime < 0.5 else 0,  # Low vol
            1 if adx > 25 else 0,  # Trending
            1 if (rsi > 30 and rsi < 70) else 0,  # Normal RSI
        ])
        
        # 4. Time features
        now = datetime.now(timezone.utc)
        hour = now.hour / 24
        day_of_week = now.weekday() / 7
        is_london = 1 if 7 <= now.hour <= 16 else 0
        is_ny = 1 if 13 <= now.hour <= 22 else 0
        
        state.extend([hour, day_of_week, is_london, is_ny])
        
        # 5. Position features (if in position)
        if position:
            entry_price = position.get('entry_price', last['close'])
            position_type = 1 if position.get('type') == 'BUY' else -1
            pnl_pct = (last['close'] - entry_price) / entry_price * 100 * position_type
            
            state.extend([
                position_type,  # 1 for long, -1 for short
                pnl_pct,
                min(abs(pnl_pct) / 2, 1),  # Normalized profit/loss
                1 if pnl_pct > 0 else 0,  # In profit
                1 if pnl_pct < -1 else 0,  # In loss (>1%)
                min(position.get('duration_bars', 0) / 100, 1),  # Position duration
            ])
        else:
            state.extend([0, 0, 0, 0, 0, 0])  # No position
        
        # Pad or truncate to state_size
        while len(state) < self.state_size:
            state.append(0)
        state = state[:self.state_size]
        
        return np.array(state, dtype=np.float32)
    
    def select_action(self, state: np.ndarray, training: bool = True) -> int:
        """
        Select action using epsilon-greedy policy.
        
        Returns:
            0: HOLD
            1: BUY
            2: SELL
            3: CLOSE
        """
        if training and random.random() < self.epsilon:
            # Random exploration
            return random.randrange(self.action_size)
        
        with torch.no_grad():
            state_tensor = torch.FloatTensor(state).unsqueeze(0).to(DEVICE)
            q_values = self.policy_net(state_tensor)
            return q_values.argmax().item()
    
    def get_trade_signal(self, state: np.ndarray, confidence_threshold: float = 0.6) -> Tuple[str, float]:
        """
        Get trading signal from RL agent for live trading.
        
        Returns:
            (action_name, confidence)
        """
        with torch.no_grad():
            state_tensor = torch.FloatTensor(state).unsqueeze(0).to(DEVICE)
            q_values = self.policy_net(state_tensor).cpu().numpy()[0]
        
        action = q_values.argmax()
        confidence = (q_values[action] - q_values.min()) / (q_values.max() - q_values.min() + 1e-8)
        
        actions = ['HOLD', 'BUY', 'SELL', 'CLOSE']
        
        # Only trade if confidence is high enough
        if confidence < confidence_threshold:
            return 'HOLD', confidence
        
        return actions[action], confidence
    
    def train_step(self) -> Optional[float]:
        """Perform one training step using experience replay."""
        if len(self.memory) < self.batch_size:
            return None
        
        # Sample batch
        batch = self.memory.sample(self.batch_size)
        
        # Prepare batch tensors
        states = torch.FloatTensor([e.state for e in batch]).to(DEVICE)
        actions = torch.LongTensor([e.action for e in batch]).to(DEVICE)
        rewards = torch.FloatTensor([e.reward for e in batch]).to(DEVICE)
        next_states = torch.FloatTensor([e.next_state for e in batch]).to(DEVICE)
        dones = torch.FloatTensor([e.done for e in batch]).to(DEVICE)
        
        # Current Q values
        current_q = self.policy_net(states).gather(1, actions.unsqueeze(1))
        
        # Next Q values (Double DQN)
        with torch.no_grad():
            next_actions = self.policy_net(next_states).argmax(1)
            next_q = self.target_net(next_states).gather(1, next_actions.unsqueeze(1)).squeeze(1)
            target_q = rewards + (1 - dones) * self.gamma * next_q
        
        # Compute loss
        loss = nn.MSELoss()(current_q.squeeze(), target_q)
        
        # Optimize
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.policy_net.parameters(), max_norm=1.0)
        self.optimizer.step()
        
        # Update target network
        self.steps_done += 1
        if self.steps_done % self.target_update == 0:
            self.target_net.load_state_dict(self.policy_net.state_dict())
            print(f"[RL] Target network updated at step {self.steps_done}")
        
        # Decay epsilon
        if self.epsilon > self.epsilon_min:
            self.epsilon *= self.epsilon_decay
        
        return loss.item()
    
    def store_experience(self, state, action, reward, next_state, done):
        """Store experience in replay buffer."""
        self.memory.push(state, action, reward, next_state, done)
    
    def calculate_reward(self, pnl_pct: float, holding_time: int, 
                        max_drawdown: float, action_taken: str) -> float:
        """
        Calculate reward for RL training.
        
        Rewards:
        - Profit: +1 to +3 based on magnitude
        - Loss: -1 to -3 based on magnitude
        - Holding penalty: Small negative for long holds
        - Drawdown penalty: Additional penalty for large drawdowns
        """
        # Base reward from PnL
        if pnl_pct > 0:
            reward = min(pnl_pct / 2, 3.0)  # Cap at 3
        else:
            reward = max(pnl_pct / 2, -3.0)  # Cap at -3
        
        # Holding time penalty (encourage faster decisions)
        if action_taken == 'CLOSE':
            reward -= holding_time * 0.001  # Small penalty per bar
        
        # Drawdown penalty
        reward -= max_drawdown * 0.5  # Penalty for max drawdown experienced
        
        # Bonus for profit taking at good levels
        if pnl_pct > 2:
            reward += 0.5  # Bonus for good profit
        
        return reward
    
    def save_model(self):
        """Save model and training state."""
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        
        torch.save({
            'policy_net': self.policy_net.state_dict(),
            'target_net': self.target_net.state_dict(),
            'optimizer': self.optimizer.state_dict(),
            'epsilon': self.epsilon,
            'steps_done': self.steps_done,
            'training_history': self.training_history,
        }, self.model_path)
        
        print(f"[RL] Model saved to {self.model_path}")
    
    def load_model(self):
        """Load model if exists."""
        if os.path.exists(self.model_path):
            try:
                checkpoint = torch.load(self.model_path, map_location=DEVICE)
                self.policy_net.load_state_dict(checkpoint['policy_net'])
                self.target_net.load_state_dict(checkpoint['target_net'])
                self.optimizer.load_state_dict(checkpoint['optimizer'])
                self.epsilon = checkpoint.get('epsilon', 0.1)  # Lower epsilon for loaded models
                self.steps_done = checkpoint.get('steps_done', 0)
                self.training_history = checkpoint.get('training_history', [])
                print(f"[RL] Model loaded from {self.model_path}")
                print(f"[RL] Steps done: {self.steps_done}, Epsilon: {self.epsilon:.3f}")
            except Exception as e:
                print(f"[RL] Failed to load model: {e}")
    
    def get_stats(self) -> Dict:
        """Get training statistics."""
        return {
            'steps_done': self.steps_done,
            'epsilon': self.epsilon,
            'memory_size': len(self.memory),
            'model_path': self.model_path,
        }


# Global singleton
_rl_trader = None

def get_rl_trader() -> RLTrader:
    """Get or create global RL trader instance."""
    global _rl_trader
    if _rl_trader is None:
        _rl_trader = RLTrader()
    return _rl_trader
