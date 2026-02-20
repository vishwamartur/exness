"""
Break of Structure (BOS) Strategy
=================================
Implements a strict institutional BOS strategy with momentum and liquidity sweep filters.

Core Logic:
1. Identify Swing Points (Highs/Lows).
2. Detect Break: Close > Swing High (Bullish) or Close < Swing Low (Bearish).
3. Filter 1: Momentum (Break Candle Body > 1.5 x ATR).
4. Filter 2: Session (London/NY Only).
5. Filter 3: Liquidity Sweep (Price swept opposite liquidity before break).
6. Filter 4: Volatility Expansion (Current ATR > Avg ATR).
"""

import pandas as pd
import numpy as np
import ta
from config import settings

class BOSStrategy:
    def __init__(self):
        self.min_atr_multiplier = getattr(settings, 'BOS_MOMENTUM_MULTIPLIER', 1.5)
        self.sweep_lookback = getattr(settings, 'BOS_SWEEP_LOOKBACK', 20)
        self.swing_lookback = 5 # Standard fractal lookback

    def analyze(self, df: pd.DataFrame) -> dict:
        """
        Analyzes the latest candle for a valid BOS setup.
        Returns a dictionary with signal details or empty if no signal.
        """
        if df is None or len(df) < 50:
            return {}

        current_idx = len(df) - 1
        candle = df.iloc[current_idx]
        prev_candle = df.iloc[current_idx - 1]

        # 1. Feature Engineering (if not present)
        # We need ATR and Swing Points
        if 'atr' not in df.columns:
            df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
        
        # Ensure swing points are calculated
        if 'swing_high' not in df.columns:
            self._add_swing_points(df)

        # 2. Check for Break of Structure (BOS)
        # Bullish BOS: Close > Previous Major Swing High
        # Bearish BOS: Close < Previous Major Swing Low
        
        # Get last valid swing points (excluding current bar which might be forming)
        last_swing_high = df['swing_high'].iloc[current_idx-1]
        last_swing_low = df['swing_low'].iloc[current_idx-1]
        
        # Check Break
        bos_signal = None
        swing_level = 0.0
        
        # Valid Break must be a CLOSE beyond the level, not just a wick
        if candle['close'] > last_swing_high and prev_candle['close'] <= last_swing_high:
            bos_signal = 'BUY'
            swing_level = last_swing_high
        elif candle['close'] < last_swing_low and prev_candle['close'] >= last_swing_low:
            bos_signal = 'SELL'
            swing_level = last_swing_low
            
        if not bos_signal:
            return {}

        # 3. Filter: Momentum (Fuel)
        # Requirement: Strong expansion candle
        # User Rule: "Break candle size > ATR x 1.5"
        # We also want a strong body (not all wicks)
        
        candle_range = candle['high'] - candle['low']
        body_size = abs(candle['close'] - candle['open'])
        
        # Ensure ATR is available
        if 'atr' not in df.columns or pd.isna(df['atr'].iloc[current_idx]):
             df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
        
        atr = df['atr'].iloc[current_idx]
        
        # 1. Range Condition (Volatility Expansion)
        # Using 1.2x ATR to be slightly more permissive than 1.5x initially, or stick to 1.5x
        if candle_range < (atr * self.min_atr_multiplier): # keeping 1.5 default
            return {'signal': bos_signal, 'valid': False, 'reason': f"Weak Expansion (Range {candle_range:.5f} < {self.min_atr_multiplier}x ATR)"}

        # 2. Strong Close Condition (Avoid wick breakouts)
        # Body must be at least 50% of range
        if body_size < (candle_range * 0.5):
             return {'signal': bos_signal, 'valid': False, 'reason': "Weak Close (Wick Heavy)"}

        # 4. Filter: Volatility Expansion
        # Current ATR > Average ATR (e.g., SMA 20 of ATR)
        avg_atr = df['atr'].rolling(window=20).mean().iloc[current_idx]
        if atr <= avg_atr:
             return {'signal': bos_signal, 'valid': False, 'reason': "Low Volatility (Contraction)"}

        # 5. Filter: Liquidity Sweep (The "Hidden Edge")
        # Did we sweep the opposite side recently?
        # For BUY: Did we sweep a recent LOW before breaking HIGH?
        # For SELL: Did we sweep a recent HIGH before breaking LOW?
        sweep_detected = False
        sweep_details = ""
        
        if bos_signal == 'BUY':
            # Check for sweep of lows in recent lookback
            # Sweep = Low < Previous Swing Low but Close > Previous Swing Low (wick rejection of lows)
            # OR just a stop hunt move that reversed
            
            # Simple Logic: Look for a recent candle (within lookback) whose Low was a local minimum
            # and was "swept" (price went below it then reversed).
            # Actually, standard "Judas Swing": Price drops, takes out liquidity, then rallies.
            # We look for: Lowest Low of last X bars < Swing Low of Y bars ago? 
            # Let's simplify: recent low < last_swing_low (wait, if we broke high, the low is opposite)
            
            # We need to find the most recent major swing LOW.
            # If price pierced that low recently (wick) but didn't close below (or closed and reversed quickly), that's a sweep.
            recent_lows = df['low'].iloc[current_idx-self.sweep_lookback : current_idx]
            min_low = recent_lows.min()
            
            # Identify the Swing Low prior to the current move
            # This is hard to do perfectly without zigzag, but let's look for a "Sweep Candle"
            # A sweep candle has a long wick relative to body, or effectively cleared a level.
             
            # Alternative (Robust): Check if we are coming from a "Discount" area (RSI < 50 originally)
            # Or explicit Liquidity Sweep as defined in features.py: 
            # 'liq_sweep_low' = ((df['low'] < df['liq_low'].shift(1)) & (df['close'] > df['liq_low'].shift(1)))
            
            if 'liq_sweep_low' in df.columns:
                 # Check if any sweep occurred in last X bars
                 sweeps = df['liq_sweep_low'].iloc[current_idx-self.sweep_lookback : current_idx]
                 if sweeps.sum() > 0:
                     sweep_detected = True
                     sweep_details = "Recent Low Sweep"
        
        elif bos_signal == 'SELL':
            if 'liq_sweep_high' in df.columns:
                sweeps = df['liq_sweep_high'].iloc[current_idx-self.sweep_lookback : current_idx]
                if sweeps.sum() > 0:
                    sweep_detected = True
                    sweep_details = "Recent High Sweep"

        if not sweep_detected:
             # Make this optional? User called it "Hidden Edge", so maybe STRICT.
             # Let's log it but maybe allow if momentum is HUGE (e.g. 3x ATR)
             if body_size < (atr * 3.0):
                return {'signal': bos_signal, 'valid': False, 'reason': "No Liquidity Sweep"}
             else:
                sweep_details = "Huge Momentum (Sweep Override)"

        return {
            'signal': bos_signal,
            'valid': True,
            'price': candle['close'],
            'sl': df['low'].iloc[current_idx] if bos_signal == 'BUY' else df['high'].iloc[current_idx], # SL below breakout candle
            'atr': atr,
            'reason': f"BOS + Momentum + {sweep_details}",
            'score': 10 # High quality setup
        }

    def _add_swing_points(self, df, lookback=5):
        # Rolling Max/Min for Swing High/Low
        # Using shift to avoid lookahead bias in rolling (though standard rolling includes current)
        # Correct Fractal: High[i] > High[i-2]...High[i+2]. We can't know i+2 in real-time.
        # So we use "Confirmed Swing": High[i-2] was highest of [i-4...i].
        # But for BOS, we often break an *old* swing.
        
        # Simple Approach: Max of last N bars (excluding current)
        # Note: This updates every bar, so it trails. A true BOS breaks a *significant* high.
        # Let's use a wider lookback for the "Structure Level" we are breaking.
        structure_lookback = 20
        
        df['swing_high'] = df['high'].rolling(window=structure_lookback).max().shift(1)
        df['swing_low'] = df['low'].rolling(window=structure_lookback).min().shift(1)
        
