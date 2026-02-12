import pandas as pd
import numpy as np
import ta

def add_technical_features(df):
    """
    Adds technical indicators to the DataFrame.
    """
    df = df.copy()
    
    # 1. Price Computations
    df['log_ret'] = np.log(df['close'] / df['close'].shift(1))
    
    # 2. Momentum Indicators
    # RSI
    df['rsi'] = ta.momentum.rsi(df['close'], window=14)
    # RSI Slope (Momentum of Momentum)
    df['rsi_slope'] = df['rsi'].diff(3) / 3
    
    # 3. Volatility Indicators
    # Bollinger Bands
    bollinger = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2)
    df['bb_high'] = bollinger.bollinger_hband()
    df['bb_low'] = bollinger.bollinger_lband()
    df['bb_width'] = (df['bb_high'] - df['bb_low']) / df['close']
    # Position within BB (0 to 1)
    df['bb_pos'] = (df['close'] - df['bb_low']) / (df['bb_high'] - df['bb_low'])
    
    # ATR
    df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
    # Normalized ATR
    df['atr_rel'] = df['atr'] / df['close']

    # 4. Trend Indicators
    # Moving Averages
    df['sma_20'] = ta.trend.sma_indicator(df['close'], window=20)
    df['sma_50'] = ta.trend.sma_indicator(df['close'], window=50)
    
    # Distances from MA (Mean Reversion / Trend Strength)
    df['dist_sma_20'] = (df['close'] - df['sma_20']) / df['sma_20']
    df['dist_sma_50'] = (df['close'] - df['sma_50']) / df['sma_50']
    
    # MACD
    macd = ta.trend.MACD(df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    df['macd_diff'] = macd.macd_diff()
    
    # 5. Lag Features (Time Series Context)
    # Return Lags
    for lag in [1, 2, 3, 5]:
        df[f'log_ret_lag_{lag}'] = df['log_ret'].shift(lag)
        df[f'rsi_lag_{lag}'] = df['rsi'].shift(lag)
        df[f'macd_diff_lag_{lag}'] = df['macd_diff'].shift(lag)
    
    # Clean NaN values
    df.dropna(inplace=True)
    
    return df
