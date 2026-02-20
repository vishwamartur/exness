import sys
import os
import unittest
import asyncio
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

# Adjust path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from strategy.pair_agent import PairAgent
from config import settings
from utils import news_filter

class TestNewsFilter(unittest.TestCase):
    def setUp(self):
        # Mock dependencies
        self.quant = MagicMock()
        self.analyst = MagicMock()
        self.risk_manager = MagicMock()
        
        # Initialize Agent
        with patch('strategy.pair_agent.TradeJournal'):
             self.agent = PairAgent("EURUSD", self.quant, self.analyst, self.risk_manager)
        
        # Enable Filter
        settings.NEWS_FILTER_ENABLE = True

    @patch('utils.news_filter.datetime')
    def test_fomc_blackout(self, mock_datetime):
        # Simulate FOMC: Wednesday 19:00 UTC
        # Jan 3rd 2024 (Wednesday) is a good candidate
        mock_val = datetime(2024, 1, 3, 19, 0, 0, tzinfo=timezone.utc)
        
        # Test direct function logic
        is_blocked, reason = news_filter.is_news_blackout("EURUSD", now_utc=mock_val)
        self.assertTrue(is_blocked, "Should be blocked during FOMC")
        self.assertEqual(reason, "FOMC")

    @patch('utils.news_filter.datetime')
    async def test_agent_scan_blocks_news(self, mock_datetime):
        # Verify Agent Logic using FOMC time to avoid US Open conflict
        mock_val = datetime(2024, 1, 3, 19, 0, 0, tzinfo=timezone.utc)
        
        # Patch is_news_blackout used by agent
        with patch('strategy.pair_agent.is_news_blackout') as mock_check:
            mock_check.return_value = (True, "FOMC")
            
            res, status = await self.agent.scan()
            
            self.assertIsNone(res)
            self.assertEqual(status, "News Blackout (FOMC)")

if __name__ == '__main__':
    unittest.main()
