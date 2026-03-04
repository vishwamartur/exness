import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(__file__))

from utils.risk_manager import RiskManager
from config import settings

print("=== COVARIANCE RISK MATRIX VALIDATOR ===")

# Create a mock MT5 position object class for testing without broker connection
class MockPosition:
    def __init__(self, symbol, type_val, volume):
        self.symbol = symbol
        self.type = type_val # 0 = BUY, 1 = SELL
        self.volume = volume

risk_manager = RiskManager(mt5_client=None)

# Scenario: The bot is currently running 2 trades that are BOTH "Short USD".
# 1. Long EURUSD (Buy EUR, Sell USD)
# 2. Long GBPUSD (Buy GBP, Sell USD)
# Let's say Lot Size is 0.02
settings.LOT_SIZE = 0.02
settings.MAX_PORTFOLIO_CORRELATION = 0.75 # Arbitrary proxy cap (equates to ~0.03 lots max absolute skew)

active_positions = [
    MockPosition("EURUSD", 0, 0.02),
    MockPosition("GBPUSD", 0, 0.02)
]

print(f"\nCurrent Portfolio: [Long EURUSD (0.02), Long GBPUSD (0.02)]")
print(f"Inherent Net Skew: SHORT 0.04 USD\n")

# TEST 1: The ML Scanner wants to BUY AUDUSD (Another Short USD bet)
# This will push our USD exposure to -0.06 (over the ~0.03 limit proxy)
print("TEST 1: Incoming Signal -> BUY AUDUSD")
is_blocked, reason = risk_manager.calculate_portfolio_covariance("AUDUSD", "BUY", active_positions)

if is_blocked:
    print(f"--> [PASSED] Risk Manager BLOCKED Trade! Reason: {reason}")
else:
    print("--> [FAILED] Risk Manager ALLOWED a dangerous correlation stack.")

# TEST 2: The ML Scanner wants to BUY USDCAD (A Long USD bet)
# This will offset our short USD spread, taking us from -0.04 USD to -0.02 USD (safer)
print("\nTEST 2: Incoming Signal -> BUY USDCAD")
is_blocked, reason = risk_manager.calculate_portfolio_covariance("USDCAD", "BUY", active_positions)

if not is_blocked:
    print(f"--> [PASSED] Risk Manager ALLOWED Trade!")
else:
    print(f"--> [FAILED] Risk Manager BLOCKED a safe hedging trade.")
    
print("\n=== VALIDATION COMPLETE ===")
