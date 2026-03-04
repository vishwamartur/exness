
import asyncio
import sys
import os
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

from config import settings
# Mock settings.SYMBOLS before importing strategy
settings.SYMBOLS = ["EURUSD", "GBPUSD", "BTCUSD"]

from strategy.institutional_strategy import InstitutionalStrategy

class MockClient:
    def get_account_info_dict(self):
        return {'balance': 10000, 'equity': 10000}
    def get_positions(self, symbol):
        return []
    def get_all_positions(self):
        return []

async def verify():
    print("Verifying InstitutionalStrategy Refactor...")
    
    mock_client = MockClient()
    strategy = InstitutionalStrategy(mock_client)
    
    # 1. Check Agents Initialization
    print(f"Agents Initialized: {len(strategy.agents)}")
    assert len(strategy.agents) == 3
    assert "EURUSD" in strategy.agents
    assert "BTCUSD" in strategy.agents
    print("✅ Agents dictionary populated correctly.")
    
    # 2. Check PairAgent structure
    agent = strategy.agents["EURUSD"]
    print(f"Checking EURUSD Agent: Type={type(agent)}")
    assert agent.symbol == "EURUSD"
    print("✅ PairAgent attributes correct.")
    
    # 3. Dry Run Scan (Stubbed)
    # We won't run full scan because it hits MT5, but we can check if method exists
    assert hasattr(agent, "scan")
    print("✅ PairAgent.scan method exists.")
    
    print("\nVerification Passed!")

if __name__ == "__main__":
    asyncio.run(verify())
