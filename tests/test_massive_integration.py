import unittest
import os
import time
import pandas as pd
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

# Ensure project root is in path
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings
from market_data.massive_client import (
    mt5_to_massive, get_asset_class, massive_to_ws_pair,
    MassiveRESTClient, MassiveS3Client, get_rest_client, get_s3_client
)
from market_data.massive_feed import MassiveFeed, get_massive_feed
from market_data.loader import get_historical_data

class TestMassiveIntegration(unittest.TestCase):
    
    def setUp(self):
        # Ensure Massive components are enabled
        settings.MASSIVE_ENABLED = True
        settings.MASSIVE_REST_FALLBACK = True
        settings.MASSIVE_API_KEY = "dummy_api_key"
        settings.MASSIVE_ACCESS_KEY_ID = "dummy_access"
        settings.MASSIVE_SECRET_ACCESS_KEY = "dummy_secret"

    # ─── Mapping Tests ───────────────────────────────────────────────────

    def test_symbol_mapping(self):
        # Crypto
        self.assertEqual(mt5_to_massive("BTCUSD"), "X:BTC-USD")
        self.assertEqual(mt5_to_massive("BTCUSDm"), "X:BTC-USD")
        self.assertEqual(get_asset_class("BTCUSD"), "crypto")
        self.assertEqual(massive_to_ws_pair("X:BTC-USD"), "BTC-USD")
        
        # Forex
        self.assertEqual(mt5_to_massive("EURUSD"), "C:EUR-USD")
        self.assertEqual(mt5_to_massive("EURUSDm"), "C:EUR-USD")
        self.assertEqual(get_asset_class("EURUSD"), "forex")
        self.assertEqual(massive_to_ws_pair("C:EUR-USD"), "EUR-USD")
        
        # Unmapped
        self.assertIsNone(mt5_to_massive("UNKNOWN_TICKER"))

    # ─── REST Client Tests ───────────────────────────────────────────────

    @patch('market_data.massive_client.requests.Session.get')
    def test_rest_client_get_aggregates(self, mock_get):
        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"t": 1704067200000, "o": 40000, "h": 40100, "l": 39900, "c": 40050, "v": 100, "vw": 40025},
                {"t": 1704067260000, "o": 40050, "h": 40200, "l": 40000, "c": 40150, "v": 150, "vw": 40100}
            ],
            "status": "OK",
            "count": 2
        }
        mock_get.return_value = mock_response

        client = MassiveRESTClient(api_key="test_key")
        df = client.get_aggregates("BTCUSD", "M1", n_bars=2)
        
        self.assertIsNotNone(df)
        self.assertEqual(len(df), 2)
        self.assertIn("open", df.columns)
        self.assertIn("close", df.columns)
        self.assertIn("tick_volume", df.columns)
        self.assertEqual(df.iloc[0]["open"], 40000)
        self.assertEqual(df.iloc[-1]["close"], 40150)
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(df['time']))
        
        mock_get.assert_called_once()

    @patch('market_data.massive_client.requests.Session.get')
    def test_rest_client_rate_limit(self, mock_get):
        # Mock 429 response
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "2"}
        mock_get.return_value = mock_response

        client = MassiveRESTClient(api_key="test_key")
        
        with patch('time.time', return_value=1000):
            df = client.get_aggregates("EURUSD", "H1", n_bars=10)
            self.assertIsNone(df)
            self.assertEqual(client._rate_limit_until, 1002)

    # ─── Loader Fallback Tests ───────────────────────────────────────────

    @patch('market_data.loader.get_rest_client')
    @patch('market_data.loader.mt5')
    @patch('os.path.exists', return_value=False) # Disable local cache hit
    def test_loader_massive_fallback(self, mock_exists, mock_mt5, mock_get_rest):
        # Arrange
        mock_client = MagicMock()
        mock_df = pd.DataFrame({"time": [datetime.now()], "close": [1.1]})
        mock_client.get_aggregates.return_value = mock_df
        mock_get_rest.return_value = mock_client
        
        # Act
        df, truncated = get_historical_data("EURUSDm", "H1", 100, use_cache=False)
        
        # Assert
        self.assertIsNotNone(df)
        mock_client.get_aggregates.assert_called_once_with("EURUSDm", "H1", 100)
        # Verify MT5 was NOT called since Massive succeeded
        mock_mt5.copy_rates_from_pos.assert_not_called()

    # ─── S3 Client Tests ─────────────────────────────────────────────────

    @patch('market_data.massive_client.MassiveS3Client._get_client')
    def test_s3_client_list_files(self, mock_get_client):
        # Setup mock S3 client
        mock_s3 = MagicMock()
        mock_s3.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "global_crypto/minute_aggs_v1/2024-01-01.csv.gz", "Size": 1024, "LastModified": "2024-01-01"},
                {"Key": "global_crypto/minute_aggs_v1/2024-01-02.csv.gz", "Size": 2048, "LastModified": "2024-01-02"}
            ]
        }
        mock_get_client.return_value = mock_s3

        client = MassiveS3Client()
        files = client.list_files()
        
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0]["key"], "global_crypto/minute_aggs_v1/2024-01-01.csv.gz")

    @patch('market_data.massive_client.MassiveS3Client._get_client')
    @patch('os.makedirs')
    def test_s3_client_download(self, mock_makedirs, mock_get_client):
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3
        
        client = MassiveS3Client()
        result = client.download_file("test/key.csv", "local/path/key.csv")
        
        self.assertTrue(result)
        mock_s3.download_file.assert_called_once_with(client.bucket, "test/key.csv", "local/path/key.csv")

    # ─── WebSocket Feed Tests ────────────────────────────────────────────

    def test_websocket_buffer_processing(self):
        feed = MassiveFeed(api_key="test_key")
        
        # Manually inject a crypto bar message
        crypto_msg = {
            "ev": "XA",
            "pair": "BTC-USD",
            "s": 1704067200000,
            "o": 40000, "h": 40100, "l": 39900, "c": 40050, "v": 100
        }
        
        feed._process_bar(crypto_msg, "XA")
        
        # Verify it got routed to the correct MT5 symbols (BTCUSD, BTCUSDm, etc)
        df = feed.get_latest_bars("BTCUSD", n=1)
        self.assertIsNotNone(df)
        self.assertEqual(df.iloc[0]["close"], 40050)
        self.assertEqual(df.iloc[0]["tick_volume"], 100)
        
        df_m = feed.get_latest_bars("BTCUSDm", n=1)
        self.assertIsNotNone(df_m)
        self.assertEqual(df_m.iloc[0]["close"], 40050)
        
        # Verify forex symbols remain unaffected
        self.assertIsNone(feed.get_latest_bars("EURUSD", n=1))


if __name__ == '__main__':
    unittest.main(verbosity=2)
