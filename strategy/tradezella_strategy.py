import pandas as pd
import numpy as np

class TradeZellaStrategy:
    """
    A rule-based trading strategy implementing TradeZella frameworks.
    This operates independently of the ML models, using the pure
    quantifiable setups generated in features.py.
    """
    
    def __init__(self, mode="all"):
        """
        mode: "ict" (FVGs/Sweeps), "bb" (Volatility Environments), "break_retest" (EMA pockets), or "all"
        """
        self.mode = mode

    def get_signal(self, df: pd.DataFrame) -> dict:
        """
        Analyzes the latest bar for TradeZella setups.
        Expected df has already been passed through features.add_technical_features().
        
        Returns:
            dict containing:
            - signal (1 for Buy, -1 for Sell, 0 for None)
            - strategy (Name of the strategy triggered)
            - confidence (Rule-based confidence score 0-1)
        """
        if df is None or df.empty:
            return {'signal': 0, 'strategy': 'none', 'confidence': 0.0}
            
        latest = df.iloc[-1]
        
        # 1. ICT Liquidity Sweep + FVG Strategy
        if self.mode in ('all', 'ict'):
            if latest.get('tz_ict_buy_setup', 0) == 1:
                return {'signal': 1, 'strategy': 'ict_fvg_sweep', 'confidence': 0.85}
            elif latest.get('tz_ict_sell_setup', 0) == 1:
                return {'signal': -1, 'strategy': 'ict_fvg_sweep', 'confidence': 0.85}

        # 2. Break & Retest / EMA Pocket Strategy
        if self.mode in ('all', 'break_retest'):
            if latest.get('tz_break_retest_buy', 0) == 1:
                return {'signal': 1, 'strategy': 'break_retest_ema', 'confidence': 0.80}
            elif latest.get('tz_break_retest_sell', 0) == 1:
                return {'signal': -1, 'strategy': 'break_retest_ema', 'confidence': 0.80}
                
        # 3. Volatility Environment (Bollinger Bands)
        if self.mode in ('all', 'bb'):
            bb_env = latest.get('tz_bb_env', 0)
            bb_trend = latest.get('tz_bb_trend', 0)
            
            # During violent expansions (Trend environment), we just trade the trend pullbacks
            # A simple rule: If bouncing off the 20-SMA in an expansion
            near_sma20 = abs(latest['close'] - latest.get('sma_20', 0)) / latest['close'] < 0.001
            
            if bb_env == 2 and near_sma20:
                if bb_trend == 1 and latest['close'] >= latest['sma_20']:
                    return {'signal': 1, 'strategy': 'bb_trend_expansion', 'confidence': 0.75}
                elif bb_trend == -1 and latest['close'] <= latest['sma_20']:
                    return {'signal': -1, 'strategy': 'bb_trend_expansion', 'confidence': 0.75}

        # No Setup
        return {'signal': 0, 'strategy': 'none', 'confidence': 0.0}

    def scan_historical(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Applies TradeZella rules historically to help visually verify and backtest inside notebooks.
        """
        df = df.copy()
        
        # Initialize output columns
        df['tz_signal'] = 0
        df['tz_strategy'] = 'none'
        
        # Vectorized check for ICT
        buy_idx = df['tz_ict_buy_setup'] == 1
        sell_idx = df['tz_ict_sell_setup'] == 1
        df.loc[buy_idx, 'tz_signal'] = 1
        df.loc[buy_idx, 'tz_strategy'] = 'ict_fvg_sweep'
        df.loc[sell_idx, 'tz_signal'] = -1
        df.loc[sell_idx, 'tz_strategy'] = 'ict_fvg_sweep'
        
        # Vectorized check for Break & Retest (overwrites ICT if conflicts, though unlikely)
        buy_idx = df['tz_break_retest_buy'] == 1
        sell_idx = df['tz_break_retest_sell'] == 1
        df.loc[buy_idx, 'tz_signal'] = 1
        df.loc[buy_idx, 'tz_strategy'] = 'break_retest_ema'
        df.loc[sell_idx, 'tz_signal'] = -1
        df.loc[sell_idx, 'tz_strategy'] = 'break_retest_ema'
        
        # Vectorized check for Bollinger Expansion Pullbacks
        near_sma20 = (df['close'] - df['sma_20']).abs() / df['close'] < 0.001
        buy_idx = (df['tz_bb_env'] == 2) & (df['tz_bb_trend'] == 1) & near_sma20 & (df['close'] >= df['sma_20'])
        sell_idx = (df['tz_bb_env'] == 2) & (df['tz_bb_trend'] == -1) & near_sma20 & (df['close'] <= df['sma_20'])
        df.loc[buy_idx, 'tz_signal'] = 1
        df.loc[buy_idx, 'tz_strategy'] = 'bb_trend_expansion'
        df.loc[sell_idx, 'tz_signal'] = -1
        df.loc[sell_idx, 'tz_strategy'] = 'bb_trend_expansion'
        
        return df
