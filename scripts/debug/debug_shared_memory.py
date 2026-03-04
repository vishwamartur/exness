
import asyncio
import os
import time
from utils.shared_state import SharedState
from utils.risk_manager import RiskManager

def test_persistence():
    print("=== TESTING SHARED MEMORY PERSISTENCE ===")
    
    # 1. Write Data
    print("[1] Writing to Shared State...")
    state1 = SharedState()
    state1.set("test_key", {"status": "active", "value": 42})
    state1.set("circuit_breaker", "OPEN")
    
    # 2. Simulate Restart (New Instance)
    print("[2] Simulating Restart...")
    time.sleep(1)
    state2 = SharedState()
    
    # 3. Read Data
    val = state2.get("test_key")
    breaker = state2.get("circuit_breaker")
    
    print(f"   Read 'test_key': {val}")
    print(f"   Read 'circuit_breaker': {breaker}")
    
    if val['value'] == 42 and breaker == "OPEN":
        print("✅ Persistence Confirmed.")
    else:
        print("❌ Persistence Failed.")
        
    # 4. Test Risk Manager Restoration
    print("\n[3] Testing Risk Manager Restoration...")
    # Manually set daily trades
    state2.set("daily_trades", 10)
    
    rm = RiskManager() # Should load 10 from DB
    print(f"   RiskManager.daily_trades: {rm.daily_trades}")
    
    if rm.daily_trades == 10:
        print("✅ Risk Manager Restore Confirmed.")
    else:
        print(f"❌ Risk Manager Restore Failed (Expected 10, got {rm.daily_trades}).")

    # Cleanup
    state2.set("circuit_breaker", "CLOSED") # Reset breaker
    state2.delete("test_key")
    state2.delete("daily_trades")

if __name__ == "__main__":
    test_persistence()
