
import asyncio
import sys
import os
from unittest.mock import MagicMock
import MetaTrader5 as mt5

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

from config import settings
settings.SYMBOLS = ["EURUSD"]

from strategy.pair_agent import PairAgent
from utils.risk_manager import RiskManager
from market_data import loader

# Mock Data
class MockPosition:
    def __init__(self):
        self.ticket = 12345
        self.symbol = "EURUSD"
        self.type = mt5.ORDER_TYPE_BUY
        self.price_open = 1.0000
        self.sl = 0.9900
        self.tp = 1.0500
        self.volume = 1.0

class MockTick:
    def __init__(self):
        self.bid = 1.0200 # Profit = 200 pips
        self.ask = 1.0202

class MockClient:
    def get_positions(self, symbol):
        return [MockPosition()]
    def modify_position(self, ticket, sl, tp):
        print(f"MOCK: Modified Position {ticket} -> SL: {sl}")
    def partial_close(self, ticket, fraction):
        print(f"MOCK: Partial Close {ticket} -> {fraction}")
    def close_position(self, ticket):
        print(f"MOCK: Close Position {ticket}")

async def verify():
    print("Verifying Active Trade Management & Optimizations...")
    
    mock_client = MockClient()
    risk_manager = RiskManager(mock_client)
    
    # Needs dependencies
    quant = MagicMock()
    analyst = MagicMock()
    
    agent = PairAgent("EURUSD", quant, analyst, risk_manager)
    
    # 1. Test Regime Exit
    print("\n--- Test 1: Regime Exit ---")
    agent.is_active = True
    agent.regime = "BEARISH_TREND" # Conflict with BUY
    
    original_tick_func = mt5.symbol_info_tick
    mt5.symbol_info_tick = lambda sym: MockTick()
    
    try:
        await agent.manage_active_trades()
        print("✅ Regime check executed.")
    except Exception as e:
        print(f"❌ Error: {e}")

    # 2. Test ATR Caching
    print("\n--- Test 2: ATR Caching ---")
    agent.latest_atr = 0.0050 # Fake cached ATR
    from datetime import datetime, timezone
    agent.last_atr_time = datetime.now(timezone.utc).timestamp() # Fresh
    
    # Mock loader to ensure it's NOT called if cache works
    original_loader = loader.get_historical_data
    loader.get_historical_data = MagicMock(return_value=None) 
    
    await agent.manage_active_trades()
    
    if not loader.get_historical_data.called:
        print("✅ ATR Cache HIT (Loader not called).")
    else:
        print("❌ ATR Cache MISS (Loader called).")
        
    loader.get_historical_data = original_loader # Restore

    # 3. Test Modification Threshold
    print("\n--- Test 3: Modification Threshold ---")
    
    pos = MockPosition()
    pos.ticket = 999
    pos.sl = 1.00495 # Ultra close
    mock_client.get_positions = MagicMock(return_value=[pos])
    
    agent.latest_atr = 0.0050
    settings.TRAILING_STOP_ATR_ACTIVATE = 1.0
    settings.TRAILING_STOP_ATR_STEP = 3.0
    
    actions = risk_manager.monitor_positions("EURUSD", [pos], MockTick(), atr=0.0050)
    
    if len(actions) == 0:
        print(f"✅ Threshold worked (No action for small diff).")
    else:
        print(f"❌ Threshold failed (Action generated): {actions}")
        
    # 4. Test Profitability Check (Commission Awareness)
    print("\n--- Test 4: Profitability Check ---")
    settings.COMMISSION_PER_LOT = 7.0
    settings.MIN_NET_PROFIT_RATIO = 2.0
    
    # Scenario A: Tight TP (Low Net Profit)
    # Entry 1.0000 -> TP 1.0003 (3 pips). Cost ~2 pips. Net 1 pip. Ratio < 2. Should block.
    # Note: MockTick bid 1.0200, ask 1.0202. Point 0.00001
    
    # We mock tick again for calculation
    class MockTickProfit:
       ask = 1.0002
       bid = 1.0000
       point = 0.00001
       
    mt5.symbol_info_tick = lambda s: MockTickProfit()
    
    # Cost = Spread (2 pips) + Comm (0.7 pips) = 2.7 pips
    # Target = 5 pips gain
    # Net = 2.3 pips. Ratio = 2.3 / 2.7 = 0.85 < 2.0. Block.
    
    allowed, reason = risk_manager.check_execution("EURUSD", "BUY", sl=0.9950, tp=1.0007, active_positions=[])
    if not allowed and "Low Profitability" in reason:
        print(f"✅ Profitability logic worked: {reason}")
    else:
        print(f"❌ Profitability logic failed. Allowed: {allowed}, Reason: {reason}")
    
    # Scenario B: Good TP
    # TP 1.0050 (50 pips). Cost 2.7. Net 47.3. Ratio > 17. Pass.
    allowed, reason = risk_manager.check_execution("EURUSD", "BUY", sl=0.9950, tp=1.0050, active_positions=[])
    if allowed:
         print(f"✅ High-quality trade allowed.")
    else:
         print(f"❌ High-quality trade blocked: {reason}")

    mt5.symbol_info_tick = original_tick_func
    print("\nVerification Complete.")

if __name__ == "__main__":
    asyncio.run(verify())
