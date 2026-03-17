import pandas as pd
import numpy as np
import ta.volatility
import ta.momentum

class MeanReversionStrategy:
    def __init__(self, bb_std=2.5, bb_period=20, rsi_period=14):
        self.bb_std = bb_std
        self.bb_period = bb_period
        self.rsi_period = rsi_period

    def analyze(self, df: pd.DataFrame) -> dict:
        """
        Analyzes dataframe for Bollinger Band Mean Reversion setups.
        Condition: Price hits/pierces 2.5 std dev + RSI Divergence
        """
        if df is None or len(df) < max(self.bb_period, self.rsi_period) + 15:
            return {'valid': False, 'reason': 'Insufficient data'}
            
        df = df.copy()
        
        # Calculate Bollinger Bands using `ta` lib
        try:
            indicator_bb = ta.volatility.BollingerBands(close=df['close'], window=self.bb_period, window_dev=self.bb_std)
            df['BBL'] = indicator_bb.bollinger_lband()
            df['BBU'] = indicator_bb.bollinger_hband()
        except Exception:
            return {'valid': False, 'reason': 'BB calc failed'}
        
        # Calculate RSI
        try:
            indicator_rsi = ta.momentum.RSIIndicator(close=df['close'], window=self.rsi_period)
            df['RSI'] = indicator_rsi.rsi()
        except:
            return {'valid': False, 'reason': 'RSI calc failed'}
        
        if df['RSI'].isna().all() or df['BBL'].isna().all():
            return {'valid': False, 'reason': 'Indicators calc failed'}
            
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Wait for price to close back inside the bands after piercing
        
        # Bullish Setup (Price pierced lower band, now closed inside, RSI diverged)
        if prev['low'] <= prev['BBL'] and last['close'] > last['BBL']:
            lookback = df.iloc[-15:-2]
            prev_low_idx = lookback['low'].idxmin()
            prev_low = lookback.loc[prev_low_idx, 'low']
            prev_rsi = lookback.loc[prev_low_idx, 'RSI']
            
            # Simple RSI Bullish Divergence check
            # Lower low in price, higher low in RSI
            if prev['low'] < prev_low and prev['RSI'] > prev_rsi:
                sl_distance = last['close'] - min(prev['low'], last['low'])
                return {
                    'valid': True, 
                    'signal': 'BUY', 
                    'score': 10, 
                    'price': last['close'], 
                    'sl': min(prev['low'], last['low']) - sl_distance * 0.1, # SL bit below recent low
                    'reason': 'Bullish BB Mean Reversion + RSI Div'
                }
                
        # Bearish Setup (Price pierced upper band, now closed inside, RSI diverged)
        if prev['high'] >= prev['BBU'] and last['close'] < last['BBU']:
            lookback = df.iloc[-15:-2]
            prev_high_idx = lookback['high'].idxmax()
            prev_high = lookback.loc[prev_high_idx, 'high']
            prev_rsi = lookback.loc[prev_high_idx, 'RSI']
            
            # Simple RSI Bearish Divergence check
            # Higher high in price, lower high in RSI
            if prev['high'] > prev_high and prev['RSI'] < prev_rsi:
                sl_distance = max(prev['high'], last['high']) - last['close']
                return {
                    'valid': True, 
                    'signal': 'SELL', 
                    'score': 10, 
                    'price': last['close'], 
                    'sl': max(prev['high'], last['high']) + sl_distance * 0.1,
                    'reason': 'Bearish BB Mean Reversion + RSI Div'
                }
                
        return {'valid': False, 'reason': 'No setup'}
