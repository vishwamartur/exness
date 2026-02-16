"""
Unit Tests for RiskManager
"""
import unittest
from unittest.mock import MagicMock, patch
from utils.risk_manager import RiskManager
from config import settings

class TestRiskManager(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.rm = RiskManager(self.mock_client)
        
    def test_daily_limit(self):
        settings.MAX_DAILY_TRADES = 2
        self.rm.daily_trades = 2
        allowed, reason = self.rm.check_pre_scan("EURUSD")
        self.assertFalse(allowed)
        self.assertEqual(reason, "Daily Limit Reached")
        
    def test_cooldown(self):
        settings.COOLDOWN_SECONDS = 180
        self.rm.record_trade("EURUSD")
        # Immediate check should fail
        allowed, reason = self.rm.check_pre_scan("EURUSD")
        self.assertFalse(allowed)
        self.assertIn("Cooldown active", reason)
        
    @patch('utils.risk_manager.mt5.symbol_info_tick')
    def test_spread_check(self, mock_tick):
        # Mock tick with high spread
        tick = MagicMock()
        tick.ask = 1.10040
        tick.bid = 1.10000 # 4 pips spread
        mock_tick.return_value = tick
        settings.MAX_SPREAD_PIPS = 3.0
        
        allowed, reason = self.rm.check_pre_scan("EURUSD")
        self.assertFalse(allowed)
        self.assertIn("Spread High", reason)

    @patch('utils.risk_manager.mt5.symbol_info_tick')
    @patch('utils.risk_manager.is_news_blackout')
    def test_news_filter(self, mock_news, mock_tick):
        # Mock valid tick to pass spread check
        tick = MagicMock()
        tick.ask = 1.10010
        tick.bid = 1.10000 
        mock_tick.return_value = tick
        
        # Simulate active news event
        mock_news.return_value = (True, "Unpacking Test Event")
        
        allowed, reason = self.rm.check_pre_scan("EURUSD")
        self.assertFalse(allowed)
        self.assertEqual(reason, "News Blackout: Unpacking Test Event")
        
    def test_position_sizing(self):
        # Confluence 6 -> Max Risk
        settings.RISK_PERCENT = 2.0
        settings.MAX_RISK_PERCENT = 5.0
        
        self.rm.calculate_position_size("EURUSD", 20, 6)
        # Verify client called with MAX_RISK_PERCENT
        self.mock_client.calculate_lot_size.assert_called_with("EURUSD", 20, 5.0)
        
        # Confluence 3 -> Base Risk
        self.rm.calculate_position_size("GBPUSD", 20, 3)
        self.mock_client.calculate_lot_size.assert_called_with("GBPUSD", 20, 2.0)

if __name__ == '__main__':
    unittest.main()
