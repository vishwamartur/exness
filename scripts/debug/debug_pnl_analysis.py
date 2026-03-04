"""
Deep P&L Analysis v2 - Fixed formatting
"""
import sqlite3

conn = sqlite3.connect('trade_journal.db')
c = conn.cursor()

total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
print(f"Total Rows in Journal: {total}")

# Trades WITH profit data
c.execute("SELECT COUNT(*) FROM trades WHERE profit IS NOT NULL AND profit != 0")
with_profit = c.fetchone()[0]
print(f"Trades with profit data: {with_profit}")

if with_profit == 0:
    print("No completed trades with profit data.")
else:
    c.execute("SELECT COUNT(*) FROM trades WHERE profit > 0")
    wins = c.fetchone()[0]  
    c.execute("SELECT COUNT(*) FROM trades WHERE profit < 0")
    losses = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM trades WHERE profit = 0")
    be = c.fetchone()[0]
    
    wr = wins / with_profit * 100 if with_profit > 0 else 0
    print(f"Wins: {wins}, Losses: {losses}, Breakeven: {be}")
    print(f"Win Rate: {wr:.1f}%")
    
    c.execute("SELECT ROUND(AVG(profit), 4) FROM trades WHERE profit > 0")
    avg_win = c.fetchone()[0]
    c.execute("SELECT ROUND(AVG(profit), 4) FROM trades WHERE profit < 0")
    avg_loss = c.fetchone()[0]
    
    print(f"Avg Win: ${avg_win if avg_win else 0}")
    print(f"Avg Loss: ${avg_loss if avg_loss else 0}")
    
    c.execute("SELECT ROUND(SUM(profit), 2) FROM trades WHERE profit IS NOT NULL")
    total_pnl = c.fetchone()[0] or 0
    print(f"Total PnL from journal: ${total_pnl}")

# By Symbol
print(f"\n{'='*70}")
print(f"PER-SYMBOL BREAKDOWN:")
print(f"{'='*70}")
c.execute("""
    SELECT symbol, COUNT(*) as cnt, 
           ROUND(SUM(profit),2) as total_pnl,
           ROUND(AVG(profit),4) as avg_pnl,
           SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as w,
           SUM(CASE WHEN profit < 0 THEN 1 ELSE 0 END) as l
    FROM trades 
    WHERE profit IS NOT NULL AND profit != 0
    GROUP BY symbol 
    ORDER BY total_pnl ASC
""")
rows = c.fetchall()
print(f"{'Symbol':<14} {'Trades':>6} {'Wins':>5} {'Loss':>5} {'TotalPnL':>10} {'AvgPnL':>8}")
for r in rows:
    print(f"{r[0]:<14} {r[1]:>6} {r[4]:>5} {r[5]:>5} {r[2]:>10} {r[3]:>8}")

# Recent 30 trades 
print(f"\n{'='*70}")
print("LAST 30 TRADES:")
print(f"{'='*70}")
c.execute("""
    SELECT symbol, direction, ROUND(profit,4), confluence_score, 
           ROUND(COALESCE(rf_probability, 0), 2), entry_time, outcome, lot_size
    FROM trades ORDER BY id DESC LIMIT 30
""")
for r in c.fetchall():
    sym = r[0] or "?"
    dr = r[1] or "?"
    pnl = r[2] if r[2] is not None else 0
    conf = r[3] if r[3] is not None else 0
    ml = r[4] if r[4] is not None else 0
    lot = r[7] if r[7] is not None else 0
    tm = r[5] or "N/A"
    oc = r[6] or "OPEN"
    print(f"{str(tm):<20} {sym:<12} {dr:<5} PnL:{pnl:>8} Conf:{conf:>3} ML:{ml:>5} Lot:{lot:>5} {oc}")

# MT5 deal history
print(f"\n{'='*70}")
print("MT5 ACTUAL COST DATA (last 14 days):")
print(f"{'='*70}")
try:
    import MetaTrader5 as mt5
    from datetime import datetime, timedelta
    mt5.initialize()
    
    now = datetime.now()
    start = now - timedelta(days=14)
    
    deals = mt5.history_deals_get(start, now)
    if deals:
        total_profit = sum(d.profit for d in deals)
        total_commission = sum(d.commission for d in deals)
        total_swap = sum(d.swap for d in deals)
        total_fee = sum(d.fee for d in deals)
        net = total_profit + total_commission + total_swap + total_fee
        
        # Count actual round-trips (exit deals)
        exits = [d for d in deals if d.entry == 1]  # DEAL_ENTRY_OUT
        
        print(f"Total Deals: {len(deals)}")
        print(f"Exit Deals (completed trades): {len(exits)}")
        print(f"Gross Profit: ${total_profit:.2f}")
        print(f"Total Commission: ${total_commission:.2f}")
        print(f"Total Swap: ${total_swap:.2f}")
        print(f"Total Fee: ${total_fee:.2f}")
        print(f"NET P&L: ${net:.2f}")
        
        if total_profit != 0:
            print(f"\nCommission = {abs(total_commission)/max(abs(total_profit),0.01)*100:.1f}% of Gross Profit")
        
        cost_per_trade = abs(total_commission) / max(len(exits), 1)
        print(f"Avg Commission Per Round-Trip: ${cost_per_trade:.2f}")
        
    mt5.shutdown()
except Exception as e:
    print(f"MT5 analysis failed: {e}")

conn.close()
print("\n=== ANALYSIS COMPLETE ===")
