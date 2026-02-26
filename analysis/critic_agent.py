
import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from config import settings
from analysis.llm_advisor import get_advisor
from utils.trade_journal import DB_PATH

class CriticAgent:
    def __init__(self, on_event=None):
        self.advisor = get_advisor()
        self.db_path = DB_PATH
        self.on_event = on_event
        print("[AGENT] CriticAgent initialized.")

    async def analyze_closed_trades(self):
        """
        Scans DB for closed trades without post-mortem.
        Returns list of analyzed trades.
        """
        trades = self._get_unreviewed_trades()
        if not trades:
            return []

        analyzed = []
        print(f"[CRITIC] found {len(trades)} trades to review...")

        for trade in trades:
            try:
                review = await self._conduct_post_mortem(trade)
                self._update_trade_record(trade['ticket'], review)
                review['symbol'] = trade['symbol']
                analyzed.append(review)
                print(f"[CRITIC] Reviewed {trade['symbol']} (Score: {review['score']})")
                
                # Emit Event
                if self.on_event:
                    self.on_event({
                        "type": "CRITIC_REVIEW",
                        "symbol": trade['symbol'],
                        "score": review['score'],
                        "lesson": review['lesson'],
                        "analysis": review['analysis'],
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                
                await asyncio.sleep(1) # Rate limit
            except Exception as e:
                print(f"[CRITIC] Failed to review {trade['ticket']}: {e}")

        return analyzed

    def _get_unreviewed_trades(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Select trades that differ from OPEN (so closed) and have no post_mortem
        cursor.execute("""
            SELECT * FROM trades 
            WHERE outcome != 'OPEN' 
            AND (post_mortem_analysis IS NULL OR post_mortem_analysis = '')
            ORDER BY exit_time DESC
            LIMIT 5
        """)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    async def _conduct_post_mortem(self, trade):
        """
        Asks Mistral to grade the trade.
        """
        system_prompt = """
        You are a Trading Coach and Critic.
        Review this closed trade and provide a brutally honest assessment.
        
        Output Format:
        SCORE | LESSON | ANALYSIS
        
        Rules:
        - START DIRECTLY WITH THE INTEGER SCORE.
        - DO NOT usage Markdown bolding.
        - DO NOT include headers like "Score | Lesson".
        - Example: 
          8 | Wait for candle close | Good trend but early entry.
        """

        user_prompt = f"""
        Symbol: {trade['symbol']}
        Direction: {trade['direction']}
        Outcome: {trade['outcome']}
        Entry: {trade['entry_price']}
        Exit: {trade['exit_price']}
        Profit: {trade.get('profit', 0)}
        
        Thesis: {trade.get('researcher_reason')}
        Confidence: {trade.get('researcher_confidence')}%
        """

        response = await self.advisor.send_prompt(system_prompt, user_prompt)
        
        default = {'score': 0, 'lesson': 'Analysis Failed', 'analysis': 'No response'}
        if not response: return default

        try:
            # Clean up response
            clean = response.replace('*', '').replace('Score:', '').strip()
            
            # Parse Pipe Format
            parts = clean.split('|')
            if len(parts) >= 3:
                # Extract Score
                score_str = parts[0].strip()
                score_digits = ''.join(filter(str.isdigit, score_str))
                score = int(score_digits) if score_digits else 0
                
                return {
                    'score': score,
                    'lesson': parts[1].strip(),
                    'analysis': parts[2].strip()
                }
            return default
        except Exception as e:
            print(f"[CRITIC] Parse Error: {e} | Raw: {response}")
            return default

    def _update_trade_record(self, ticket, review):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trades 
            SET post_mortem_analysis = ?,
                lesson_learned = ?,
                grading_score = ?
            WHERE ticket = ?
        """, (review['analysis'], review['lesson'], review['score'], ticket))
        conn.commit()
        conn.close()
