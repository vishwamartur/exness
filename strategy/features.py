import pandas as pd
import numpy as np
import ta
import warnings


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
    
    # Microstructure: Volatility of Volatility (Proxy for GARCH)
    df['vol_of_vol'] = df['atr'].rolling(window=14).std() / df['atr']

    # ─── 5. Volume & Order Flow Indicators ───────────────────────────────
    if 'tick_volume' in df.columns:
        # A. Rolling VWAP with +2 / -2 Standard Deviation Bands (VWAP Extremes)
        # We use a 100-bar rolling window to accurately track intraday anchor points
        vwap_window = 100
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        tp_vol = typical_price * df['tick_volume']
        
        # Calculate trailing VWAP
        roll_vol = df['tick_volume'].rolling(window=vwap_window, min_periods=10).sum()
        roll_tp_vol = tp_vol.rolling(window=vwap_window, min_periods=10).sum()
        df['vwap_rolling'] = roll_tp_vol / roll_vol.replace(0, np.nan)
        
        # Calculate Variance for STD Bands
        # Var = E[x^2] - E[x]^2, where x is typical price weighted by volume...
        # A simpler robust approximation is the rolling std of the typical price about the VWAP
        df['vwap_std'] = typical_price.rolling(window=vwap_window, min_periods=10).std()
        
        df['vwap_upper'] = df['vwap_rolling'] + (2 * df['vwap_std'])
        df['vwap_lower'] = df['vwap_rolling'] - (2 * df['vwap_std'])
        
        # Extremes: price distance from VWAP normalized by standard deviation
        df['vwap_zscore'] = (df['close'] - df['vwap_rolling']) / df['vwap_std'].replace(0, np.nan)
        df['vwap_zscore'] = df['vwap_zscore'].replace([np.inf, -np.inf], 0).fillna(0)
        
        # Legacy LSTM compatibility columns
        df['vwap'] = df['vwap_rolling']
        df['dist_vwap'] = (df['close'] - df['vwap']) / df['vwap'].replace(0, np.nan)

        # Volume SMA anomaly detection
        df['vol_sma'] = df['tick_volume'].rolling(window=20).mean()
        df['vol_ratio'] = df['tick_volume'] / df['vol_sma']

        # B. Volume Profile Analysis: Rolling Point of Control (POC)
        # Approximate by finding the closing price with the highest rolling volume sum in a 50 bar window
        df = _add_volume_profile_poc(df, lookback=50)

        # C. Order Flow Imbalance (OFI)
        # Signed delta: positive = bullish candle, negative = bearish candle
        # Approximates buy (uptick) vs sell (downtick) volume pressure on M1
        candle_dir = np.where(df['close'] >= df['open'], 1, -1)
        df['delta_vol'] = df['tick_volume'] * candle_dir
        df['delta_vol_cumsum'] = df['delta_vol'].rolling(window=20).sum()  # 20-bar cumulative delta
        df['delta_vol_ratio'] = df['delta_vol'] / df['tick_volume'].replace(0, np.nan)  # (-1 to +1)
        # Normalised flow: > 0 means net buying, < 0 net selling pressure
        df['delta_vol_ratio'] = df['delta_vol_ratio'].fillna(0)

        # ─── 5b. Institutional Flow Features ─────────────────────────────────
        # These features help ML models learn institutional order flow patterns.

        # i. Volume Z-Score: how unusual is current volume? (>2 = institutional)
        vol_mean_50 = df['tick_volume'].rolling(window=50, min_periods=10).mean()
        vol_std_50 = df['tick_volume'].rolling(window=50, min_periods=10).std()
        df['inst_volume_zscore'] = ((df['tick_volume'] - vol_mean_50) / vol_std_50.replace(0, np.nan)).fillna(0)

        # ii. Absorption Score: high volume + small body = stealth accumulation/distribution
        candle_body = (df['close'] - df['open']).abs()
        candle_range_full = (df['high'] - df['low']).replace(0, np.nan)
        body_range_ratio = candle_body / candle_range_full
        vol_rel = df['tick_volume'] / vol_mean_50.replace(0, np.nan)
        # Absorption = high vol ratio * inverted body ratio (small body = high absorption)
        df['inst_absorption_score'] = (vol_rel.fillna(1) * (1 - body_range_ratio.fillna(0.5))).clip(0, 5)

        # iii. Displacement: large body candles signaling institutional intent
        avg_body = candle_body.rolling(window=20, min_periods=5).mean()
        df['inst_displacement'] = (candle_body > 3.0 * avg_body).astype(int)

        # iv. CVD at 20 and 50 bar windows (Cumulative Volume Delta)
        df['cvd_20'] = df['delta_vol'].rolling(window=20, min_periods=5).sum()
        df['cvd_50'] = df['delta_vol'].rolling(window=50, min_periods=10).sum()

        # v. CVD-Price Divergence: CVD direction vs price direction
        price_change_20 = df['close'] - df['close'].shift(20)
        cvd_sign = np.sign(df['cvd_20'])
        price_sign = np.sign(price_change_20)
        df['cvd_divergence'] = (cvd_sign != price_sign).astype(int)

        # vi. Smart Money Index: weighted composite (0-1 normalized)
        # Combines volume zscore signal + absorption + displacement + CVD strength
        vol_signal = df['inst_volume_zscore'].clip(0, 4) / 4.0  # 0-1
        abs_signal = df['inst_absorption_score'].clip(0, 3) / 3.0  # 0-1
        disp_signal = df['inst_displacement'].astype(float)  # 0 or 1
        cvd_norm = (df['cvd_20'] / df['tick_volume'].rolling(window=20, min_periods=5).sum().replace(0, np.nan)).fillna(0).abs().clip(0, 1)
        df['smart_money_index'] = (vol_signal * 0.25 + abs_signal * 0.30 + disp_signal * 0.20 + cvd_norm * 0.25).clip(0, 1)

        # vii. Institutional Aggression: ratio of displacement candles in last 10 bars
        df['inst_aggression_ratio'] = df['inst_displacement'].rolling(window=10, min_periods=1).mean()

    # ─── 6. Bid-Ask Spread Dynamics ───────────────────────────────────────
    if 'spread' in df.columns:
        df['spread_sma'] = df['spread'].rolling(window=20).mean()
        # Negative value implies tightening spread (institutional absorption/entry)
        df['spread_tightening'] = (df['spread'] - df['spread_sma']) / df['spread_sma'].replace(0, np.nan)
        df['spread_tightening'] = df['spread_tightening'].fillna(0)

    df = _add_market_structure(df)
    df = _add_order_blocks(df)
    df = _add_fair_value_gaps(df)
    df = _add_liquidity_levels(df)
    df = _add_tradezella_patterns(df)

    # ─── 7. Time-of-Day / Day-of-Week Features (Session Encoding) ────────
    # Forex behavior changes dramatically across sessions (Asian/London/NY).
    # Sin/cos encoding preserves cyclical nature (23:59 is close to 00:00).
    if 'time' in df.columns:
        try:
            timestamps = pd.to_datetime(df['time'])
            hour = timestamps.dt.hour + timestamps.dt.minute / 60.0
            day = timestamps.dt.dayofweek  # 0=Mon, 6=Sun

            # Sin/Cos encoding for smooth cyclical features
            df['hour_sin'] = np.sin(2 * np.pi * hour / 24.0)
            df['hour_cos'] = np.cos(2 * np.pi * hour / 24.0)
            df['day_sin'] = np.sin(2 * np.pi * day / 7.0)
            df['day_cos'] = np.cos(2 * np.pi * day / 7.0)

            # Session flags (binary)
            df['is_london'] = ((hour >= 7) & (hour < 16)).astype(int)
            df['is_ny'] = ((hour >= 13) & (hour < 22)).astype(int)
            df['is_overlap'] = ((hour >= 13) & (hour < 16)).astype(int)  # London-NY overlap
            df['is_asian'] = ((hour >= 0) & (hour < 8)).astype(int)
        except Exception:
            pass  # Skip if time column can't be parsed

    # ─── 8. GARCH-Style Volatility Forecast ──────────────────────────────
    # EWMA variance as fast GARCH(1,1) approximation (avoids arch library dependency).
    # Lambda=0.94 matches RiskMetrics standard.
    returns_sq = df['log_ret'] ** 2
    df['garch_var'] = returns_sq.ewm(span=20, adjust=False).mean()
    df['garch_vol'] = np.sqrt(df['garch_var'])
    # Volatility forecast ratio: current vol vs 50-bar average
    df['garch_vol_ratio'] = df['garch_vol'] / df['garch_vol'].rolling(window=50, min_periods=10).mean()
    df['garch_vol_ratio'] = df['garch_vol_ratio'].replace([np.inf, -np.inf], 1.0).fillna(1.0)

    # ─── 9. Mean Reversion Z-Score ───────────────────────────────────────
    # How far price is from its mean in standard deviation terms.
    # Z > 2 = overextended up, Z < -2 = overextended down.
    sma_50_val = df['sma_50'] if 'sma_50' in df.columns else df['close'].rolling(50).mean()
    price_std = df['close'].rolling(window=50, min_periods=10).std()
    df['price_zscore_50'] = ((df['close'] - sma_50_val) / price_std.replace(0, np.nan)).fillna(0)

    # ─── 10. Fractional Differentiation (Memory-Preserving Stationarity) ─
    # Makes price data stationary while retaining memory/trend information.
    # Uses fixed-window fracdiff approximation (d=0.4, window=20).
    try:
        df['frac_diff_close'] = _fracdiff(df['close'].values, d=0.4, window=20)
    except Exception:
        df['frac_diff_close'] = 0.0

    # ─── 11. Lag Features ────────────────────────────────────────────────
    lag_feats = {}
    for lag in [1, 2, 3, 5]:
        lag_feats[f'log_ret_lag_{lag}'] = df['log_ret'].shift(lag)
        lag_feats[f'rsi_lag_{lag}'] = df['rsi'].shift(lag)
        lag_feats[f'macd_diff_lag_{lag}'] = df['macd_diff'].shift(lag)
        
    df = pd.concat([df, pd.DataFrame(lag_feats, index=df.index)], axis=1)

    # Clean NaN values
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df.dropna(inplace=True)

    return df


def _fracdiff(series: np.ndarray, d: float = 0.4, window: int = 20) -> np.ndarray:
    """
    Fixed-window fractional differentiation (López de Prado).
    
    Makes the series stationary while preserving memory/trend information.
    d=0.4 is a good default for most financial time series.
    
    Parameters
    ----------
    series : np.ndarray — raw price series
    d : float — fractional differentiation order (0 < d < 1)
    window : int — number of lags to use in the weights
    
    Returns
    -------
    np.ndarray — fractionally differentiated series
    """
    # Compute weights using the binomial series expansion
    weights = [1.0]
    for k in range(1, window):
        w = -weights[-1] * (d - k + 1) / k
        weights.append(w)
    weights = np.array(weights[::-1])  # Reverse for convolution order
    
    # Apply the weights as a filter
    n = len(series)
    result = np.full(n, np.nan)
    for i in range(window - 1, n):
        result[i] = np.dot(weights, series[i - window + 1: i + 1])
    
    # Fill initial NaN values with 0
    result = np.nan_to_num(result, nan=0.0)
    return result


def _add_volume_profile_poc(df, lookback=50):
    """
    Computes a Rolling Point of Control (POC) for Volume Profile Analysis.
    Finds the dominant price level over the last `lookback` bars where max volume traded.
    """
    poc_series = np.full(len(df), np.nan)
    
    # We slice backwards from the end to build up rolling distributions efficiently.
    # To save time on large dataframes, we can round prices to buckets.
    # Average ATR gives a good dynamic bucket size.
    mean_atr = df['atr'].mean() if 'atr' in df.columns else (df['close'].mean() * 0.001)
    bucket_size = mean_atr * 0.5 if mean_atr > 0 else 0.0001
    
    closes = df['close'].values
    vols = df['tick_volume'].values
    
    # We only apply POC computation to rows where lookback is satisfied.
    for i in range(lookback, len(df)):
        window_closes = closes[i-lookback:i]
        window_vols = vols[i-lookback:i]
        
        # Round prices to bin them for distribution mapping
        binned_prices = np.round(window_closes / bucket_size) * bucket_size
        
        # Sum volume by binned price level utilizing pandas groupby or dictionary
        # Dictionary is faster for small lookback windows inside a loop
        vol_profile = {}
        for price, v in zip(binned_prices, window_vols):
            vol_profile[price] = vol_profile.get(price, 0) + v
            
        # The Point of Control is the price bin with the highest volume sum
        if vol_profile:
            poc = max(vol_profile, key=vol_profile.get)
            poc_series[i] = poc
            
    df['vp_poc'] = poc_series
    df['vp_poc'] = df['vp_poc'].ffill().bfill()
    df['dist_to_poc'] = (df['close'] - df['vp_poc']) / df['vp_poc'].replace(0, np.nan)
    
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


def _add_tradezella_patterns(df):
    """
    Implements quantifiable TradeZella strategy frameworks as machine learning features.
    
    Strategies implemented:
    1. FVG & Liquidity Sweeps (ICT/SMC)
    2. Bollinger Band Environments (Consolidation/Expansion)
    3. Break & Retest / EMA Pockets
    """
    
    # ─── 1. FVG & Liquidity Sweep (ICT Model) ──────────────────────────────────
    # Valid setup: A recent liquidity sweep followed by price returning to an FVG
    
    # Check if a sweep happened in the last 5 bars
    recent_sweep_high = df['liq_sweep_high'].rolling(window=5, min_periods=1).max()
    recent_sweep_low = df['liq_sweep_low'].rolling(window=5, min_periods=1).max()
    
    # Sell Setup: Swept buy-side liquidity (highs), created bearish FVG, and returning to it
    df['tz_ict_sell_setup'] = ((recent_sweep_high == 1) & (df['near_fvg_bearish'] == 1)).astype(int)
    
    # Buy Setup: Swept sell-side liquidity (lows), created bullish FVG, and returning to it
    df['tz_ict_buy_setup'] = ((recent_sweep_low == 1) & (df['near_fvg_bullish'] == 1)).astype(int)
    

    # ─── 2. Volatility Environment (BB Strategy) ───────────────────────────────
    # Identify whether the market is squeezing, expanding, or reverting
    
    avg_bb_width = df['bb_width'].rolling(window=50, min_periods=10).mean()
    
    # 0 = Normal, 1 = Squeeze (Consolidation), 2 = Expansion (Trend)
    df['tz_bb_env'] = 0
    df.loc[df['bb_width'] < avg_bb_width * 0.8, 'tz_bb_env'] = 1  # Squeeze
    df.loc[df['bb_width'] > avg_bb_width * 1.2, 'tz_bb_env'] = 2  # Expansion
    
    # Trend alignment during expansion (1 = Bullish Trend, -1 = Bearish Trend, 0 = N/A)
    df['tz_bb_trend'] = 0
    df.loc[(df['tz_bb_env'] == 2) & (df['close'] > df['sma_20']), 'tz_bb_trend'] = 1
    df.loc[(df['tz_bb_env'] == 2) & (df['close'] < df['sma_20']), 'tz_bb_trend'] = -1

    
    # ─── 3. Break & Retest (EMA Pocket Strategy) ───────────────────────────────
    # Valid setup: Broke market structure recently, now pulling back to 21 EMA
    
    if 'bos_bullish' in df.columns and 'bos_bearish' in df.columns:
        recent_bos_bullish = df['bos_bullish'].rolling(window=10, min_periods=1).max()
        recent_bos_bearish = df['bos_bearish'].rolling(window=10, min_periods=1).max()
        
        # Near EMA 21 definition (less than 0.1% away)
        near_ema_21 = (df['close'] - df['ema_21']).abs() / df['close'] < 0.001
        
        # Pullback needs to be a retracement, so for buy, price should be touching EMA from above
        df['tz_break_retest_buy'] = ((recent_bos_bullish == 1) & near_ema_21 & (df['close'] >= df['ema_21'])).astype(int)
        df['tz_break_retest_sell'] = ((recent_bos_bearish == 1) & near_ema_21 & (df['close'] <= df['ema_21'])).astype(int)
    else:
        df['tz_break_retest_buy'] = 0
        df['tz_break_retest_sell'] = 0

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
