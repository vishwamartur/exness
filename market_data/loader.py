import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta, timezone
import sys
import os
import time

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings

# Timeframe string to MT5 constant map
TF_MAP = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}

# Minutes per timeframe
TF_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}

# MT5 max bars per request
MT5_MAX_BARS = 50000


def initial_connect():
    """Initializes MT5 connection"""
    if not mt5.initialize(path=settings.MT5_PATH):
        print("initialize() failed, error code =", mt5.last_error())
        return False
    
    # Try to login
    authorized = mt5.login(settings.MT5_LOGIN, password=settings.MT5_PASSWORD, server=settings.MT5_SERVER)
    if authorized:
        print(f"Connected to account #{settings.MT5_LOGIN}")
    else:
        print(f"Failed to connect to account #{settings.MT5_LOGIN}, error code: {mt5.last_error()}")
        
    return True


def get_historical_data(symbol, timeframe_str, n_bars):
    """
    Fetches historical bars from MT5.
    For large requests (>50k bars), fetches in chunks.
    """
    if not mt5.terminal_info():
        if not initial_connect():
            return None

    tf = TF_MAP.get(timeframe_str, mt5.TIMEFRAME_M15)
    
    # Ensure symbol is selected/visible
    if not mt5.symbol_select(symbol, True):
        print(f"Failed to select {symbol}")
        return None
    
    # Small request - fetch directly
    if n_bars <= MT5_MAX_BARS:
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, n_bars)
        
        if rates is None or len(rates) == 0:
            print(f"No rates for {symbol}")
            return None
            
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df
    
    # Large request - fetch in chunks using date ranges
    print(f"  Fetching {n_bars:,} bars in chunks...", end=" ", flush=True)
    
    all_data = []
    minutes_per_bar = TF_MINUTES.get(timeframe_str, 15)
    
    # Start from now and go backwards
    end_time = datetime.now(timezone.utc)
    remaining = n_bars
    chunk_count = 0
    
    while remaining > 0:
        chunk_size = min(remaining, MT5_MAX_BARS)
        
        # Fetch chunk
        rates = mt5.copy_rates_from(symbol, tf, end_time, chunk_size)
        
        if rates is None or len(rates) == 0:
            # No more data available
            break
        
        df_chunk = pd.DataFrame(rates)
        all_data.append(df_chunk)
        
        remaining -= len(rates)
        chunk_count += 1
        
        # Move end_time to before the oldest bar we got
        oldest_time = datetime.fromtimestamp(rates[0]['time'], tz=timezone.utc)
        end_time = oldest_time - timedelta(minutes=minutes_per_bar)
        
        # Progress
        if chunk_count % 5 == 0:
            print(f"{n_bars - remaining:,}...", end=" ")
        
        # Small delay to avoid overwhelming MT5
        time.sleep(0.1)
    
    if not all_data:
        print(f"No rates for {symbol}")
        return None
    
    # Combine and sort
    df = pd.concat(all_data, ignore_index=True)
    df = df.drop_duplicates(subset=['time']).sort_values('time').reset_index(drop=True)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    
    print(f"Got {len(df):,} bars")
    return df


def get_multi_timeframe_data(symbol, n_bars_primary=500):
    """
    Fetches M15, H1, and H4 data in one call for multi-timeframe analysis.
    Returns a dict: {"M15": df, "H1": df, "H4": df}
    """
    data = {}
    
    tf_bars = {
        "M15": n_bars_primary,
        "H1": max(200, n_bars_primary // 4),
        "H4": max(100, n_bars_primary // 16),
    }
    
    for tf_str, bars in tf_bars.items():
        df = get_historical_data(symbol, tf_str, bars)
        if df is not None:
            data[tf_str] = df
        else:
            print(f"[{symbol}] Warning: Could not fetch {tf_str} data")
    
    return data
