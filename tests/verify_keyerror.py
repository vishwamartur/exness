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

class TestKeyErrorFix(unittest.TestCase):
    def setUp(self):
        # Mock dependencies
        self.quant = MagicMock()
        self.analyst = MagicMock()
        self.risk_manager = MagicMock()
        
        # Initialize Agent
        with patch('strategy.pair_agent.TradeJournal'):
             self.agent = PairAgent("EURUSD", self.quant, self.analyst, self.risk_manager)
        
    async def test_analyze_missing_signal(self):
        # Mock Quant Response causing KeyError
        # q_res missing 'signal'
        bad_q_res = {
            'score': 5,
            'ml_prob': 0.6,
            'features': {'atr': 0.001},
            'data': None
        }
        
        # Mock run_in_executor to return bad_q_res
        # We need to mock the call inside _analyze
        # pair_agent.run_in_executor is imported as run_in_executor
        
        with patch('strategy.pair_agent.run_in_executor') as mock_run:
            mock_run.return_value = bad_q_res
            
            # Additional mocks needed for _analyze flow
            self.analyst.analyze_session.return_value = {'regime': 'NEUTRAL'}
            self.agent.bos.analyze.return_value = {} # No BOS
            
            # Execute
            try:
                # We need to pass data_dict
                candidate, status = await self.agent._analyze({'M15': []})
                print(f"Result: {candidate}, Status: {status}")
                
            except KeyError as e:
                self.fail(f"KeyError raised: {e}")
            except Exception as e:
                self.fail(f"Exception raised: {e}")
                
            # Expectation: Should complete, likely return Candidate with NEUTRAL or None depending on logic
            # Currently logic constructs candidate with signal='NEUTRAL'
            # And then returns "OK" (if checks pass) or filtered reason.
            
            self.assertIsNotNone(candidate)
            self.assertEqual(candidate['direction'], 'NEUTRAL')

if __name__ == '__main__':
    # Run async test
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    unittest.main()
