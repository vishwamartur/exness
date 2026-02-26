import sqlite3
import pandas as pd
import os

DB_PATH = "f:/mt5/trade_journal.db"

def analyze_history():
    print("==================================================")
    print("  MT5 BOT EXECUTION HISTORY ANALYSIS")
    print("==================================================")
    
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database not found at {DB_PATH}")
        return
        
    conn = sqlite3.connect(DB_PATH)
    
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
    if not cursor.fetchone():
        print("[ERROR] 'trades' table not found in database.")
        conn.close()
        return
        
    df = pd.read_sql_query("SELECT * FROM trades", conn)
    conn.close()
    
    if len(df) == 0:
        print("[INFO] Database exists but no trades have been logged yet.")
        return
        
    print(f"\n[INFO] Total Registered Trades: {len(df)}")
    
    completed = df[df['outcome'].isin(['WIN', 'LOSS'])]
    if len(completed) > 0:
        win_count = len(completed[completed['outcome'] == 'WIN'])
        loss_count = len(completed[completed['outcome'] == 'LOSS'])
        win_rate = win_count / len(completed) * 100
        
        prof_sum = completed['profit'].sum()
        avg_prof = completed[completed['profit'] > 0]['profit'].mean() if win_count > 0 else 0
        avg_loss = completed[completed['profit'] < 0]['profit'].mean() if loss_count > 0 else 0
        
        print("\n[PERFORMANCE METRICS]")
        print(f"  Completed Trades : {len(completed)}")
        print(f"  Win Rate         : {win_rate:.1f}% ({win_count}W / {loss_count}L)")
        print(f"  Net Profit       : ${prof_sum:.2f}")
        print(f"  Average Winner   : ${avg_prof:.2f}")
        print(f"  Average Loser    : ${avg_loss:.2f}")
        
        if avg_loss < 0:
            print(f"  Risk/Reward Ratio: {abs(avg_prof / avg_loss):.2f}")
            
        print("\n[TOP PAIRS BY VOLUME]")
        pair_counts = completed['symbol'].value_counts().head(5)
        for sym, count in pair_counts.items():
            sym_df = completed[completed['symbol'] == sym]
            sym_prof = sym_df['profit'].sum()
            print(f"  {sym:10} | {count:3} trades | Net: ${sym_prof:6.2f}")
            
        print("\n[LAST 5 TRADES]")
        recent = completed.sort_values('entry_time', ascending=False).head(5)
        for _, r in recent.iterrows():
            print(f"  {r['symbol']:8} | {r['direction']:4} | {r['outcome']:4} | ${r['profit']:.2f} | Reason: {str(r.get('researcher_reason'))[:40]}...")
            
    else:
        print("[INFO] Found active trades, but no completed closed trades yet.")
        print("\n[ACTIVE TRADES]")
        active = df[df['outcome'] == 'OPEN'].head(10)
        for _, r in active.iterrows():
            print(f"  {r['symbol']:8} | {r['direction']:4} | Entry: {r['entry_price']} | SL: {r['sl_price']}")


if __name__ == "__main__":
    analyze_history()
