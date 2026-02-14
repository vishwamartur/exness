import pandas as pd
import numpy as np
import ta


def add_technical_features(df):
    """
    Adds institutional-grade technical indicators to the DataFrame.
    Includes market structure, order blocks, FVGs, liquidity levels,
    and standard momentum/volatility indicators.
    """
    df = df.copy()

    # ─── 1. Price Computations ───────────────────────────────────────────
    df['log_ret'] = np.log(df['close'] / df['close'].shift(1))

    # ─── 2. Momentum Indicators ──────────────────────────────────────────
    # RSI
    df['rsi'] = ta.momentum.rsi(df['close'], window=14)
    df['rsi_slope'] = df['rsi'].diff(3) / 3

    # Stochastic RSI for overbought/oversold precision
    stoch_rsi = ta.momentum.StochRSIIndicator(df['close'], window=14)
    df['stoch_rsi_k'] = stoch_rsi.stochrsi_k()
    df['stoch_rsi_d'] = stoch_rsi.stochrsi_d()

    # ─── 3. Volatility Indicators ────────────────────────────────────────
    # Bollinger Bands
    bollinger = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2)
    df['bb_high'] = bollinger.bollinger_hband()
    df['bb_low'] = bollinger.bollinger_lband()
    df['bb_width'] = (df['bb_high'] - df['bb_low']) / df['close']
    df['bb_pos'] = (df['close'] - df['bb_low']) / (df['bb_high'] - df['bb_low'])

    # ATR (core for dynamic SL/TP)
    df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
    df['atr_rel'] = df['atr'] / df['close']

    # ─── 4. Trend Indicators ─────────────────────────────────────────────
    # Moving Averages
    df['sma_20'] = ta.trend.sma_indicator(df['close'], window=20)
    df['sma_50'] = ta.trend.sma_indicator(df['close'], window=50)
    df['ema_9'] = ta.trend.ema_indicator(df['close'], window=9)
    df['ema_21'] = ta.trend.ema_indicator(df['close'], window=21)

    # Distances from MA
    df['dist_sma_20'] = (df['close'] - df['sma_20']) / df['sma_20']
    df['dist_sma_50'] = (df['close'] - df['sma_50']) / df['sma_50']

    # EMA crossover signal
    df['ema_cross'] = np.where(df['ema_9'] > df['ema_21'], 1, -1)

    # MACD
    macd = ta.trend.MACD(df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    df['macd_diff'] = macd.macd_diff()

    # ADX — Trend Strength (institutional must-have)
    adx = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14)
    df['adx'] = adx.adx()
    df['adx_pos'] = adx.adx_pos()  # +DI
    df['adx_neg'] = adx.adx_neg()  # -DI

    # ─── 5. Volume Indicators ────────────────────────────────────────────
    # VWAP approximation (using tick_volume as proxy)
    if 'tick_volume' in df.columns:
        df['vwap'] = (df['close'] * df['tick_volume']).cumsum() / df['tick_volume'].cumsum()
        df['dist_vwap'] = (df['close'] - df['vwap']) / df['vwap']
        # Volume SMA for anomaly detection
        df['vol_sma'] = df['tick_volume'].rolling(window=20).mean()
        df['vol_ratio'] = df['tick_volume'] / df['vol_sma']

    # ─── 6. Market Structure ─────────────────────────────────────────────
    df = _add_market_structure(df)
    df = _add_order_blocks(df)
    df = _add_fair_value_gaps(df)
    df = _add_liquidity_levels(df)

    # ─── 7. Lag Features ─────────────────────────────────────────────────
    for lag in [1, 2, 3, 5]:
        df[f'log_ret_lag_{lag}'] = df['log_ret'].shift(lag)
        df[f'rsi_lag_{lag}'] = df['rsi'].shift(lag)
        df[f'macd_diff_lag_{lag}'] = df['macd_diff'].shift(lag)

    # Clean NaN values
    df.dropna(inplace=True)

    return df


def _add_market_structure(df, lookback=5):
    """
    Detects Higher Highs (HH), Higher Lows (HL), Lower Highs (LH), Lower Lows (LL).
    Also detects Break of Structure (BOS).
    """
    # Swing highs and lows
    df['swing_high'] = df['high'].rolling(window=lookback * 2 + 1, center=True).max()
    df['swing_low'] = df['low'].rolling(window=lookback * 2 + 1, center=True).min()

    df['is_swing_high'] = (df['high'] == df['swing_high']).astype(int)
    df['is_swing_low'] = (df['low'] == df['swing_low']).astype(int)

    # Higher High / Lower Low detection
    df['prev_swing_high'] = df['high'].where(df['is_swing_high'] == 1).ffill()
    df['prev_swing_low'] = df['low'].where(df['is_swing_low'] == 1).ffill()

    df['higher_high'] = (df['prev_swing_high'] > df['prev_swing_high'].shift(1)).astype(int)
    df['lower_low'] = (df['prev_swing_low'] < df['prev_swing_low'].shift(1)).astype(int)

    # Break of Structure: price closes above previous swing high (bullish BOS)
    # or below previous swing low (bearish BOS)
    df['bos_bullish'] = (df['close'] > df['prev_swing_high'].shift(1)).astype(int)
    df['bos_bearish'] = (df['close'] < df['prev_swing_low'].shift(1)).astype(int)

    # Market structure score: +1 bullish, -1 bearish
    df['structure_score'] = df['higher_high'] - df['lower_low'] + df['bos_bullish'] - df['bos_bearish']

    return df


def _add_order_blocks(df, lookback=10):
    """
    Detects Order Blocks (OB):
    - Bullish OB: Last bearish candle before a strong bullish impulse move
    - Bearish OB: Last bullish candle before a strong bearish impulse move
    """
    df['ob_bullish'] = 0.0
    df['ob_bearish'] = 0.0

    body = (df['close'] - df['open']).abs()
    avg_body = body.rolling(window=20).mean()

    for i in range(lookback, len(df)):
        # Bullish OB: bearish candle followed by strong bullish move
        if (df['close'].iloc[i-1] < df['open'].iloc[i-1] and   # Previous candle bearish
            df['close'].iloc[i] > df['open'].iloc[i] and       # Current candle bullish
            body.iloc[i] > 1.5 * avg_body.iloc[i]):            # Strong impulse
            df.iloc[i, df.columns.get_loc('ob_bullish')] = df['low'].iloc[i-1]  # OB zone = prev candle low

        # Bearish OB: bullish candle followed by strong bearish move
        if (df['close'].iloc[i-1] > df['open'].iloc[i-1] and   # Previous candle bullish
            df['close'].iloc[i] < df['open'].iloc[i] and       # Current candle bearish
            body.iloc[i] > 1.5 * avg_body.iloc[i]):            # Strong impulse
            df.iloc[i, df.columns.get_loc('ob_bearish')] = df['high'].iloc[i-1]  # OB zone = prev candle high

    # Carry forward the most recent OB levels
    df['ob_bullish'] = df['ob_bullish'].replace(0, np.nan).ffill().fillna(0)
    df['ob_bearish'] = df['ob_bearish'].replace(0, np.nan).ffill().fillna(0)

    # Distance to OB (normalized)
    df['dist_ob_bullish'] = np.where(df['ob_bullish'] > 0,
                                      (df['close'] - df['ob_bullish']) / df['close'], 0)
    df['dist_ob_bearish'] = np.where(df['ob_bearish'] > 0,
                                      (df['ob_bearish'] - df['close']) / df['close'], 0)

    # Near OB flag (within 0.1% of OB)
    df['near_ob_bullish'] = ((df['dist_ob_bullish'] > 0) & (df['dist_ob_bullish'] < 0.001)).astype(int)
    df['near_ob_bearish'] = ((df['dist_ob_bearish'] > 0) & (df['dist_ob_bearish'] < 0.001)).astype(int)

    return df


def _add_fair_value_gaps(df):
    """
    Detects Fair Value Gaps (FVG):
    - Bullish FVG: gap between candle[i-2].high and candle[i].low (price hasn't filled)
    - Bearish FVG: gap between candle[i-2].low and candle[i].high
    """
    df['fvg_bullish'] = 0.0
    df['fvg_bearish'] = 0.0

    for i in range(2, len(df)):
        # Bullish FVG: candle[i] low > candle[i-2] high (gap up)
        if df['low'].iloc[i] > df['high'].iloc[i-2]:
            df.iloc[i, df.columns.get_loc('fvg_bullish')] = df['high'].iloc[i-2]

        # Bearish FVG: candle[i] high < candle[i-2] low (gap down)
        if df['high'].iloc[i] < df['low'].iloc[i-2]:
            df.iloc[i, df.columns.get_loc('fvg_bearish')] = df['low'].iloc[i-2]

    # Carry forward
    df['fvg_bullish'] = df['fvg_bullish'].replace(0, np.nan).ffill().fillna(0)
    df['fvg_bearish'] = df['fvg_bearish'].replace(0, np.nan).ffill().fillna(0)

    # Near FVG (price returning to fill the gap)
    df['near_fvg_bullish'] = np.where(
        (df['fvg_bullish'] > 0) & (df['close'] - df['fvg_bullish']).abs() / df['close'] < 0.001,
        1, 0)
    df['near_fvg_bearish'] = np.where(
        (df['fvg_bearish'] > 0) & (df['close'] - df['fvg_bearish']).abs() / df['close'] < 0.001,
        1, 0)

    return df


def _add_liquidity_levels(df, lookback=20):
    """
    Identifies liquidity pools: equal highs/lows that act as stop-loss clusters.
    Institutions target these levels for liquidity grabs.
    """
    df['liq_high'] = df['high'].rolling(window=lookback).max()
    df['liq_low'] = df['low'].rolling(window=lookback).min()

    # Distance to liquidity levels
    df['dist_liq_high'] = (df['liq_high'] - df['close']) / df['close']
    df['dist_liq_low'] = (df['close'] - df['liq_low']) / df['close']

    # Liquidity sweep detection: price pierces beyond liquidity then reverses
    df['liq_sweep_high'] = ((df['high'] > df['liq_high'].shift(1)) &
                             (df['close'] < df['liq_high'].shift(1))).astype(int)
    df['liq_sweep_low'] = ((df['low'] < df['liq_low'].shift(1)) &
                            (df['close'] > df['liq_low'].shift(1))).astype(int)

    return df


def get_session_info(timestamp):
    """
    Returns the current trading session based on UTC hour.
    Returns: (session_name, is_active)
    """
    hour = timestamp.hour if hasattr(timestamp, 'hour') else pd.Timestamp(timestamp).hour

    if 8 <= hour < 12:
        return "london", True
    elif 13 <= hour < 17:
        return "new_york", True
    elif 13 <= hour < 16:
        return "overlap", True  # Best liquidity
    else:
        return "off_hours", False
