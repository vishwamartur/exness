import sys
import os
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

# Adjust path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from strategy.pair_agent import PairAgent
from config import settings

class TestRetailFilters(unittest.TestCase):
    def setUp(self):
        # Mock dependencies
        self.quant = MagicMock()
        self.analyst = MagicMock()
        self.risk_manager = MagicMock()
        self.risk_manager.client = MagicMock() # Mock MT5 client inside risk manager
        
        # Initialize Agent
        with patch('strategy.pair_agent.TradeJournal'): # Mock DB
             self.agent = PairAgent("EURUSD", self.quant, self.analyst, self.risk_manager)
        
        # Default Settings
        settings.BOS_MAX_SPREAD_RATIO = 0.15
        settings.BOS_HUNTING_HOURS = [8, 9, 10, 13, 14, 15]

    @patch('strategy.pair_agent.datetime')
    @patch('strategy.pair_agent.mt5.symbol_info_tick')
    def test_spread_filter(self, mock_tick, mock_datetime):
        # Ensure we are in hunting hours for spread test
        mock_datetime.now.return_value = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        
        # Case 1: Good Spread
        # SL = 10 pips, Spread = 1 pip. Ratio = 0.1 <= 0.15. OK.
        mock_tick.return_value = MagicMock(ask=1.10010, bid=1.10000) # 1 pip spread
        candidate = {'sl_distance': 0.00100} # 10 pips
        
        self.assertTrue(self.agent._check_retail_viability(candidate), "Should pass good spread")
        
        # Case 2: Bad Spread
        # SL = 5 pips, Spread = 1 pip. Ratio = 0.2 > 0.15. FAIL.
        candidate = {'sl_distance': 0.00050} # 5 pips
        self.assertFalse(self.agent._check_retail_viability(candidate), "Should fail bad spread")

    @patch('strategy.pair_agent.datetime')
    @patch('strategy.pair_agent.mt5.symbol_info_tick')
    def test_hunting_hours(self, mock_tick, mock_datetime):
        # Setup valid spread to isolate time check
        mock_tick.return_value = MagicMock(ask=1.10001, bid=1.10000) # 0.1 pip
        candidate = {'sl_distance': 0.00100}
        
        # Case 1: London Open (09:00 UTC) -> OK
        mock_datetime.now.return_value = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(self.agent._check_retail_viability(candidate), "Should pass London Open")
        
        # Case 2: Lunch Lull (12:00 UTC) -> FAIL
        mock_datetime.now.return_value = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(self.agent._check_retail_viability(candidate), "Should fail Lunch Lull")

if __name__ == '__main__':
    unittest.main()
