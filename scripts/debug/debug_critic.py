
import asyncio
import sqlite3
import os
from datetime import datetime, timezone
from config import settings
from config import settings
from analysis.critic_agent import CriticAgent
from utils.trade_journal import TradeJournal, DB_PATH

# Force Debug
settings.LOG_LEVEL = "DEBUG"

async def debug_critic():
    print("=== DEBUG CRITIC AGENT (SELF-REFLECTION) ===")
    
    # 1. Setup DB and Insert Mock Trade
    journal = TradeJournal()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    mock_ticket = 123456
    
    # Delete if exists
    cursor.execute("DELETE FROM trades WHERE ticket = ?", (mock_ticket,))
    
    # Insert Mock Closed Trade (Loss)
    print("Inserting mock CLOSED trade (Loss)...")
    cursor.execute("""
        INSERT INTO trades (
            ticket, symbol, direction, lot_size, entry_price, 
            exit_price, profit, outcome, 
            entry_time, exit_time,
            confluence_score, researcher_reason, researcher_confidence
        ) VALUES (
            ?, 'MockUSD', 'BUY', 0.1, 1.1000, 
            1.0950, -50.0, 'LOSS', 
            ?, ?,
            8, 'Strong Uptrend detected by H4 ADX', 85
        )
    """, (
        mock_ticket, 
        datetime.now(timezone.utc).isoformat(),
        datetime.now(timezone.utc).isoformat()
    ))
    conn.commit()
    conn.close()
    
    # 2. Run Critic Agent
    critic = CriticAgent()
    print("Running Critic Analysis...")
    
    reviews = await critic.analyze_closed_trades()
    
    if not reviews:
        print("❌ No reviews generated.")
    else:
        for r in reviews:
            print(f"\n✅ REVIEW GENERATED:")
            print(f"   Score: {r['score']}/10")
            print(f"   Lesson: {r['lesson']}")
            print(f"   Analysis: {r['analysis']}")
            
    # 3. Verify DB Update
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    row = cursor.execute("SELECT post_mortem_analysis, grading_score FROM trades WHERE ticket = ?", (mock_ticket,)).fetchone()
    conn.close()
    
    if row and row[0]:
        print("\n✅ DB Updated Successfully.")
    else:
        print("\n❌ DB Update Failed.")

if __name__ == "__main__":
    asyncio.run(debug_critic())
