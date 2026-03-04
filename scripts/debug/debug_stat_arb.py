from analysis.stat_arb_manager import StatArbManager
from execution.mt5_client import MT5Client
from market_data import loader

print("=== STATISTICAL ARBITRAGE OFFLINE VALIDATOR ===")

client = MT5Client()
if not client.connect():
    print("MT5 Connection Failed.")
    exit()

print("Connected to MT5.")
arb = StatArbManager(client)

# Test EURUSD vs GBPUSD
symA, symB = "EURUSD", "GBPUSD"
print(f"\nPulling Data for {symA} / {symB}")

df_a = loader.get_historical_data(symA, "H1", 500)
df_b = loader.get_historical_data(symB, "H1", 500)

print(f"Loaded {len(df_a)} bars for A, {len(df_b)} bars for B.")

is_coint, p = arb.check_cointegration(df_a, df_b)
print(f"Cointegrated: {is_coint} (p-value: {p:.4f})")

if is_coint:
    ratio, z = arb.calculate_spread(df_a, df_b)
    print(f"Hedge Ratio: {ratio:.4f}")
    print(f"Current Spread Z-Score: {z:.2f}")
else:
    print("Pair is not currently cointegrated within the 500 bar window.")

print("\n=== DEBUG COMPLETE ===")
