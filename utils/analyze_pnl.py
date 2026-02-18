import sqlite3
import pandas as pd
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import settings

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trade_journal.db")

def analyze_pnl():
    if not os.path.exists(DB_PATH):
        print(f"No database found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    
    # Read trades into DataFrame
    query = "SELECT * FROM trades"
    try:
        df = pd.read_sql_query(query, conn)
    except Exception as e:
        print(f"Error reading database: {e}")
        conn.close()
        return

    conn.close()

    if df.empty:
        print("No trades found in the journal.")
        return

    # Ensure profit is numeric
    df['profit'] = pd.to_numeric(df['profit'], errors='coerce').fillna(0.0)

    # 1. Overall P&L
    total_pnl = df['profit'].sum()
    total_trades = len(df)
    win_trades = df[df['profit'] > 0]
    loss_trades = df[df['profit'] <= 0]
    win_rate = (len(win_trades) / total_trades * 100) if total_trades > 0 else 0

    print("=" * 60)
    print(f"  OVERALL PERFORMANCE")
    print("=" * 60)
    print(f"  Total P&L:      ${total_pnl:.2f}")
    print(f"  Total Trades:   {total_trades}")
    print(f"  Win Rate:       {win_rate:.1f}% ({len(win_trades)}W / {len(loss_trades)}L)")
    print(f"  Avg Win:        ${win_trades['profit'].mean():.2f}" if not win_trades.empty else "  Avg Win:        $0.00")
    print(f"  Avg Loss:       ${loss_trades['profit'].mean():.2f}" if not loss_trades.empty else "  Avg Loss:       $0.00")
    print("-" * 60)
    print("")

    # 2. P&L per Symbol (Agent Performance)
    print("=" * 60)
    print(f"  PERFORMANCE BY PAIR (AGENT)")
    print("=" * 60)
    print(f"  {'Symbol':<10} | {'P&L':>10} | {'Trades':>6} | {'WR%':>6} | {'PF':>5}")
    print("-" * 60)

    # Group by symbol
    symbol_stats = df.groupby('symbol').agg(
        total_profit=('profit', 'sum'),
        count=('profit', 'count'),
        wins=('profit', lambda x: (x > 0).sum()),
        gross_profit=('profit', lambda x: x[x > 0].sum()),
        gross_loss=('profit', lambda x: abs(x[x <= 0].sum()))
    ).sort_values(by='total_profit', ascending=False)

    for symbol, row in symbol_stats.iterrows():
        wr = (row['wins'] / row['count'] * 100) if row['count'] > 0 else 0
        pf = (row['gross_profit'] / row['gross_loss']) if row['gross_loss'] > 0 else 99.99
        
        print(f"  {symbol:<10} | ${row['total_profit']:>9.2f} | {row['count']:>6} | {wr:>5.1f}% | {pf:>5.2f}")

    print("-" * 60)
    
if __name__ == "__main__":
    analyze_pnl()
