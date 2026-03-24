"""
Massive.com Financial Data Client
===================================
Unified client for accessing Massive.com's Crypto + Currencies data via:
  1. REST API  — Historical OHLCV aggregates
  2. S3        — Bulk flat file downloads for AI training
  
Auth: Bearer token via API key.

Endpoints:
  REST:  https://api.massive.com/v2/aggs/ticker/{prefix}:{pair}/range/{mult}/{timespan}/{from}/{to}
  S3:    https://files.massive.com (bucket: flatfiles)
"""

import os
import time
import logging
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Tuple

from config import settings

logger = logging.getLogger(__name__)


# ─── Symbol Mapping ──────────────────────────────────────────────────────

# MT5 symbol → Massive.com ticker mapping
# Crypto uses X: prefix with hyphenated pairs
# Forex uses C: prefix with hyphenated pairs
SYMBOL_MAP_CRYPTO = {
    "BTCUSD":  "X:BTC-USD",
    "BTCUSDm": "X:BTC-USD",
    "BTCUSDc": "X:BTC-USD",
    "ETHUSD":  "X:ETH-USD",
    "ETHUSDm": "X:ETH-USD",
    "ETHUSDc": "X:ETH-USD",
    "LTCUSD":  "X:LTC-USD",
    "LTCUSDm": "X:LTC-USD",
    "LTCUSDc": "X:LTC-USD",
    "XRPUSD":  "X:XRP-USD",
    "XRPUSDm": "X:XRP-USD",
    "XRPUSDc": "X:XRP-USD",
    "BCHUSD":  "X:BCH-USD",
    "BCHUSDm": "X:BCH-USD",
    "BCHUSDc": "X:BCH-USD",
    "BTCJPY":  "X:BTC-JPY",
    "BTCJPYm": "X:BTC-JPY",
    "BTCKRW":  "X:BTC-KRW",
    "BTCKRWm": "X:BTC-KRW",
}

SYMBOL_MAP_FOREX = {
    "EURUSD":  "C:EUR-USD",
    "EURUSDm": "C:EUR-USD",
    "EURUSDc": "C:EUR-USD",
    "GBPUSD":  "C:GBP-USD",
    "GBPUSDm": "C:GBP-USD",
    "GBPUSDc": "C:GBP-USD",
    "USDJPY":  "C:USD-JPY",
    "USDJPYm": "C:USD-JPY",
    "USDJPYc": "C:USD-JPY",
    "USDCHF":  "C:USD-CHF",
    "USDCHFm": "C:USD-CHF",
    "AUDUSD":  "C:AUD-USD",
    "AUDUSDm": "C:AUD-USD",
    "USDCAD":  "C:USD-CAD",
    "USDCADm": "C:USD-CAD",
    "NZDUSD":  "C:NZD-USD",
    "NZDUSDm": "C:NZD-USD",
    "EURGBP":  "C:EUR-GBP",
    "EURGBPm": "C:EUR-GBP",
    "EURJPY":  "C:EUR-JPY",
    "EURJPYm": "C:EUR-JPY",
    "GBPJPY":  "C:GBP-JPY",
    "GBPJPYm": "C:GBP-JPY",
}

# Combined map
SYMBOL_MAP = {**SYMBOL_MAP_CRYPTO, **SYMBOL_MAP_FOREX}

# MT5 timeframe string → Massive.com (multiplier, timespan) mapping
TIMEFRAME_MAP = {
    "M1":  (1, "minute"),
    "M5":  (5, "minute"),
    "M15": (15, "minute"),
    "M30": (30, "minute"),
    "H1":  (1, "hour"),
    "H4":  (4, "hour"),
    "D1":  (1, "day"),
}

# WebSocket event prefixes per asset class
WS_EVENT_MAP = {
    "crypto": "XA",   # Crypto Aggregates
    "forex":  "CA",   # Currency Aggregates
}


def mt5_to_massive(symbol: str) -> Optional[str]:
    """Convert MT5 symbol to Massive.com ticker. Returns None if unmapped."""
    return SYMBOL_MAP.get(symbol)


def get_asset_class(symbol: str) -> str:
    """Determine if a symbol is crypto or forex for Massive.com routing."""
    massive_ticker = mt5_to_massive(symbol)
    if massive_ticker and massive_ticker.startswith("X:"):
        return "crypto"
    return "forex"


def massive_to_ws_pair(massive_ticker: str) -> str:
    """Convert Massive.com ticker to WebSocket pair format.
    X:BTC-USD → BTC-USD, C:EUR-USD → EUR-USD
    """
    return massive_ticker.split(":", 1)[1] if ":" in massive_ticker else massive_ticker


# ─── REST Client ─────────────────────────────────────────────────────────

class MassiveRESTClient:
    """
    REST API client for Massive.com financial data.
    
    Fetches historical OHLCV aggregate bars for crypto and forex.
    """
    
    BASE_URL = "https://api.massive.com"
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or getattr(settings, 'MASSIVE_API_KEY', '')
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        })
        self._rate_limit_until = 0
    
    def get_aggregates(self, symbol: str, timeframe: str = "M1",
                       n_bars: int = 500,
                       from_date: str = None, to_date: str = None) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV aggregate bars from Massive.com REST API.
        
        Args:
            symbol: MT5 symbol name (e.g. 'BTCUSDm', 'EURUSD')
            timeframe: MT5 timeframe string ('M1', 'M5', 'H1', 'D1', etc.)
            n_bars: Number of bars to fetch (max 50,000)
            from_date: Start date 'YYYY-MM-DD' (auto-calculated if None)
            to_date: End date 'YYYY-MM-DD' (defaults to today)
            
        Returns:
            DataFrame with columns: time, open, high, low, close, volume, vwap
            or None on failure.
        """
        # Check rate limit
        if time.time() < self._rate_limit_until:
            logger.warning("[MASSIVE] Rate limited, waiting...")
            time.sleep(max(0, self._rate_limit_until - time.time()))
        
        # Map symbol
        massive_ticker = mt5_to_massive(symbol)
        if not massive_ticker:
            logger.debug(f"[MASSIVE] No mapping for symbol: {symbol}")
            return None
        
        # Map timeframe
        if timeframe not in TIMEFRAME_MAP:
            logger.warning(f"[MASSIVE] Unsupported timeframe: {timeframe}")
            return None
        
        multiplier, timespan = TIMEFRAME_MAP[timeframe]
        
        # Calculate date range
        if not to_date:
            to_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        if not from_date:
            # Calculate from_date based on n_bars and timeframe
            tf_minutes = {"minute": 1, "hour": 60, "day": 1440}
            total_minutes = n_bars * multiplier * tf_minutes.get(timespan, 1)
            from_dt = datetime.now(timezone.utc) - timedelta(minutes=total_minutes * 1.5)  # 1.5x buffer for gaps
            from_date = from_dt.strftime("%Y-%m-%d")
        
        # Build URL
        url = (
            f"{self.BASE_URL}/v2/aggs/ticker/{massive_ticker}"
            f"/range/{multiplier}/{timespan}/{from_date}/{to_date}"
        )
        
        params = {
            "adjusted": "true",
            "sort": "asc",
            "limit": min(n_bars, 50000),
        }
        
        try:
            r = self.session.get(url, params=params, timeout=30)
            
            # Handle rate limiting
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 60))
                self._rate_limit_until = time.time() + retry_after
                logger.warning(f"[MASSIVE] Rate limited for {retry_after}s")
                return None
            
            r.raise_for_status()
            data = r.json()
            
            results = data.get("results", [])
            if not results:
                logger.debug(f"[MASSIVE] No data for {symbol} ({massive_ticker})")
                return None
            
            # Convert to DataFrame
            df = pd.DataFrame(results)
            
            # Rename columns: c→close, h→high, l→low, o→open, v→volume, t→time, vw→vwap
            col_map = {
                "c": "close",
                "h": "high",
                "l": "low",
                "o": "open",
                "v": "volume",
                "t": "time",
                "vw": "vwap",
                "n": "num_trades",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            
            # Convert millisecond timestamp to datetime
            if "time" in df.columns:
                df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
            
            # Ensure required columns
            for col in ["open", "high", "low", "close", "volume"]:
                if col not in df.columns:
                    df[col] = 0.0
            
            # Add tick_volume alias (used by existing MT5 code)
            if "tick_volume" not in df.columns:
                df["tick_volume"] = df["volume"].astype(int)
            
            # Sort by time ascending
            df = df.sort_values("time").reset_index(drop=True)
            
            # Trim to requested n_bars
            if len(df) > n_bars:
                df = df.tail(n_bars).reset_index(drop=True)
            
            logger.info(f"[MASSIVE] Fetched {len(df)} bars for {symbol} ({timeframe})")
            return df
            
        except requests.exceptions.ConnectionError:
            logger.warning("[MASSIVE] API connection failed")
            return None
        except requests.exceptions.HTTPError as e:
            logger.error(f"[MASSIVE] HTTP error: {e}")
            return None
        except Exception as e:
            logger.error(f"[MASSIVE] REST error for {symbol}: {e}")
            return None
    
    def get_latest_price(self, symbol: str) -> Optional[Dict]:
        """
        Get the latest price for a symbol (last 1-minute bar).
        
        Returns:
            Dict with open, high, low, close, volume, time or None.
        """
        df = self.get_aggregates(symbol, "M1", n_bars=1)
        if df is not None and len(df) > 0:
            row = df.iloc[-1]
            return {
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "time": row["time"],
            }
        return None
    
    def is_available(self) -> bool:
        """Check if Massive.com API is reachable and authenticated."""
        try:
            # Try a minimal request
            r = self.session.get(
                f"{self.BASE_URL}/v2/aggs/ticker/X:BTC-USD/range/1/day/2024-01-01/2024-01-02",
                params={"limit": 1},
                timeout=10
            )
            return r.status_code == 200
        except Exception:
            return False


# ─── S3 Flat File Client ─────────────────────────────────────────────────

class MassiveS3Client:
    """
    S3-compatible client for downloading Massive.com flat files.
    
    Uses boto3 with custom endpoint for bulk CSV downloads.
    Useful for AI model training with comprehensive historical data.
    """
    
    def __init__(self, access_key: str = None, secret_key: str = None,
                 endpoint: str = None, bucket: str = None):
        self.access_key = access_key or getattr(settings, 'MASSIVE_ACCESS_KEY_ID', '')
        self.secret_key = secret_key or getattr(settings, 'MASSIVE_SECRET_ACCESS_KEY', '')
        self.endpoint = endpoint or getattr(settings, 'MASSIVE_S3_ENDPOINT', 'https://files.massive.com')
        self.bucket = bucket or getattr(settings, 'MASSIVE_S3_BUCKET', 'flatfiles')
        self._client = None
    
    def _get_client(self):
        """Lazy-init boto3 S3 client."""
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config
                
                self._client = boto3.client(
                    "s3",
                    endpoint_url=self.endpoint,
                    aws_access_key_id=self.access_key,
                    aws_secret_access_key=self.secret_key,
                    config=Config(
                        signature_version="s3v4",
                        s3={"addressing_style": "path"},
                    ),
                    region_name="us-east-1",  # Required by boto3 but ignored by Massive
                )
            except ImportError:
                logger.error("[MASSIVE] boto3 not installed. Run: pip install boto3")
                return None
        return self._client
    
    def list_files(self, prefix: str = "global_crypto/minute_aggs_v1/",
                   max_keys: int = 100) -> List[Dict]:
        """
        List available flat files in the S3 bucket.
        
        Args:
            prefix: S3 key prefix (e.g. 'global_crypto/minute_aggs_v1/')
            max_keys: Max files to list.
            
        Returns:
            List of dicts with key, size, last_modified.
        """
        client = self._get_client()
        if not client:
            return []
        
        try:
            response = client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix,
                MaxKeys=max_keys
            )
            
            files = []
            for obj in response.get("Contents", []):
                files.append({
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"],
                })
            
            logger.info(f"[MASSIVE] Listed {len(files)} files under {prefix}")
            return files
            
        except Exception as e:
            logger.error(f"[MASSIVE] S3 list error: {e}")
            return []
    
    def download_file(self, key: str, local_path: str) -> bool:
        """
        Download a single flat file from S3.
        
        Args:
            key: S3 object key (e.g. 'global_crypto/minute_aggs_v1/2024-01-01.csv.gz')
            local_path: Local file path to save to.
            
        Returns:
            True on success.
        """
        client = self._get_client()
        if not client:
            return False
        
        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            client.download_file(self.bucket, key, local_path)
            logger.info(f"[MASSIVE] Downloaded: {key} → {local_path}")
            return True
        except Exception as e:
            logger.error(f"[MASSIVE] S3 download error: {e}")
            return False
    
    def download_date_range(self, asset_type: str = "crypto",
                            data_type: str = "minute_aggs_v1",
                            start_date: str = None, end_date: str = None,
                            output_dir: str = "data/massive") -> List[str]:
        """
        Download flat files for a date range.
        
        Args:
            asset_type: 'crypto' or 'forex'
            data_type: 'minute_aggs_v1', 'day_aggs_v1', etc.
            start_date: 'YYYY-MM-DD'
            end_date: 'YYYY-MM-DD'
            output_dir: Local directory for downloads.
            
        Returns:
            List of downloaded local file paths.
        """
        prefix_map = {
            "crypto": f"global_crypto/{data_type}/",
            "forex": f"global_forex/{data_type}/",
        }
        prefix = prefix_map.get(asset_type, f"global_{asset_type}/{data_type}/")
        
        # List all files
        all_files = self.list_files(prefix, max_keys=1000)
        
        # Filter by date range
        if start_date or end_date:
            filtered = []
            for f in all_files:
                # File names typically contain dates (e.g., 2024-01-01.csv.gz)
                basename = os.path.basename(f["key"])
                date_part = basename.split(".")[0]  # Remove extension
                
                if start_date and date_part < start_date:
                    continue
                if end_date and date_part > end_date:
                    continue
                filtered.append(f)
            all_files = filtered
        
        # Download
        downloaded = []
        out_dir = os.path.join(output_dir, asset_type, data_type)
        
        for f_info in all_files:
            local_path = os.path.join(out_dir, os.path.basename(f_info["key"]))
            if os.path.exists(local_path):
                downloaded.append(local_path)
                continue
            
            if self.download_file(f_info["key"], local_path):
                downloaded.append(local_path)
        
        print(f"[MASSIVE] Downloaded {len(downloaded)} files to {out_dir}")
        return downloaded
    
    def load_flat_file(self, local_path: str) -> Optional[pd.DataFrame]:
        """Load a downloaded flat file CSV into a DataFrame."""
        try:
            if local_path.endswith(".gz"):
                df = pd.read_csv(local_path, compression="gzip")
            else:
                df = pd.read_csv(local_path)
            
            # Standardize column names
            col_map = {
                "c": "close", "h": "high", "l": "low", "o": "open",
                "v": "volume", "t": "time", "vw": "vwap",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            
            if "time" in df.columns:
                df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
            
            if "tick_volume" not in df.columns and "volume" in df.columns:
                df["tick_volume"] = df["volume"].astype(int)
            
            return df
        except Exception as e:
            logger.error(f"[MASSIVE] Error loading {local_path}: {e}")
            return None


# ─── Singleton Accessors ─────────────────────────────────────────────────

_rest_client = None
_s3_client = None

def get_rest_client() -> MassiveRESTClient:
    """Get or create singleton REST client."""
    global _rest_client
    if _rest_client is None:
        _rest_client = MassiveRESTClient()
    return _rest_client

def get_s3_client() -> MassiveS3Client:
    """Get or create singleton S3 client."""
    global _s3_client
    if _s3_client is None:
        _s3_client = MassiveS3Client()
    return _s3_client
