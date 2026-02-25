"""
Trade Journal — SQLite-based trade logging and performance tracking.

Logs every trade with:
- Entry/exit details
- Confluence score and breakdown
- R:R achieved, profit/loss
- Duration
- Symbol, direction, lot size

Enables post-hoc analysis of which confluences predict winners.
"""

import sqlite3
import os
import json
from datetime import datetime, timezone


DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trade_journal.db")


class TradeJournal:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init_db()

    def log_trade(self, *args, **kwargs):
        """Alias for log_entry to fix legacy calls."""
        return self.log_entry(*args, **kwargs)

    def _init_db(self):
        """Creates tables if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket INTEGER UNIQUE,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                lot_size REAL,
                entry_price REAL,
                sl_price REAL,
                tp_price REAL,
                exit_price REAL,
                profit REAL,
                rr_achieved REAL,
                confluence_score INTEGER,
                confluence_details TEXT,
                rf_probability REAL,
                ai_signal INTEGER,
                asset_class TEXT,
                session TEXT,
                entry_time TEXT,
                exit_time TEXT,
                duration_minutes REAL,
                outcome TEXT,
                notes TEXT,
                researcher_action TEXT,
                researcher_confidence INTEGER,
                researcher_reason TEXT,
                post_mortem_analysis TEXT,
                lesson_learned TEXT,
                grading_score INTEGER
            )
        """)

        # Migration for existing tables
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN researcher_action TEXT")
            cursor.execute("ALTER TABLE trades ADD COLUMN researcher_confidence INTEGER")
            cursor.execute("ALTER TABLE trades ADD COLUMN researcher_reason TEXT")
        except: pass # Columns likely exist
        
        # Phase 4.2: Critic Columns
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN post_mortem_analysis TEXT")
            cursor.execute("ALTER TABLE trades ADD COLUMN lesson_learned TEXT")
            cursor.execute("ALTER TABLE trades ADD COLUMN grading_score INTEGER")
        except: pass
        
        # Pre-trade Analysis Columns
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN pre_trade_confidence REAL")
            cursor.execute("ALTER TABLE trades ADD COLUMN pre_trade_reasoning TEXT")
        except: pass

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                total_profit REAL DEFAULT 0,
                best_trade_profit REAL DEFAULT 0,
                worst_trade_loss REAL DEFAULT 0,
                avg_rr REAL DEFAULT 0,
                avg_confluence REAL DEFAULT 0,
                balance_end REAL DEFAULT 0
            )
        """)

        conn.commit()
        conn.close()

    def log_entry(self, ticket, symbol, direction, lot_size, entry_price,
                  sl_price, tp_price, confluence_score, confluence_details,
                  rf_probability=0, ai_signal=0, asset_class='forex', session='',
                  researcher_action='NONE', researcher_confidence=0, researcher_reason=''):
        """Logs a new trade entry."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR IGNORE INTO trades
                (ticket, symbol, direction, lot_size, entry_price, sl_price, tp_price,
                 confluence_score, confluence_details, rf_probability, ai_signal,
                 asset_class, session, entry_time, outcome,
                 researcher_action, researcher_confidence, researcher_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
            """, (
                ticket, symbol, direction, lot_size, entry_price, sl_price, tp_price,
                confluence_score, json.dumps(confluence_details),
                rf_probability, ai_signal, asset_class, session,
                datetime.now(timezone.utc).isoformat(),
                researcher_action, researcher_confidence, researcher_reason
            ))
            conn.commit()
        except Exception as e:
            print(f"[JOURNAL] Error logging entry: {e}")
        finally:
            conn.close()

    def log_exit(self, ticket, exit_price, profit):
        """Logs trade exit with profit/loss."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # Get entry details
            cursor.execute("SELECT entry_price, sl_price, entry_time FROM trades WHERE ticket = ?", (ticket,))
            row = cursor.fetchone()

            if row:
                entry_price, sl_price, entry_time_str = row
                risk = abs(entry_price - sl_price) if sl_price else 1
                rr_achieved = profit / risk if risk > 0 else 0

                entry_time = datetime.fromisoformat(entry_time_str)
                exit_time = datetime.now(timezone.utc)
                duration = (exit_time - entry_time).total_seconds() / 60

                outcome = 'WIN' if profit > 0 else 'LOSS'

                cursor.execute("""
                    UPDATE trades SET
                        exit_price = ?, profit = ?, rr_achieved = ?,
                        exit_time = ?, duration_minutes = ?, outcome = ?
                    WHERE ticket = ?
                """, (exit_price, profit, rr_achieved,
                      exit_time.isoformat(), duration, outcome, ticket))
                conn.commit()
        except Exception as e:
            print(f"[JOURNAL] Error logging exit: {e}")
        finally:
            conn.close()

    def get_daily_stats(self, date=None):
        """Returns daily trade statistics."""
        if date is None:
            date = datetime.now(timezone.utc).date().isoformat()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END) as losses,
                COALESCE(SUM(profit), 0) as total_profit,
                COALESCE(AVG(rr_achieved), 0) as avg_rr,
                COALESCE(AVG(confluence_score), 0) as avg_confluence
            FROM trades
            WHERE DATE(entry_time) = ?
        """, (date,))

        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                'total': row[0], 'wins': row[1], 'losses': row[2],
                'total_profit': row[3], 'avg_rr': row[4], 'avg_confluence': row[5],
                'win_rate': (row[1] / row[0] * 100) if row[0] > 0 else 0
            }
        return None

    def get_confluence_analysis(self, last_n_trades=100):
        """Analyzes which confluences correlate with winning trades."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT confluence_details, outcome, profit
            FROM trades
            WHERE outcome IS NOT NULL AND outcome != 'OPEN'
            ORDER BY entry_time DESC
            LIMIT ?
        """, (last_n_trades,))

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return {}

        # Count wins per confluence factor
        factor_wins = {}
        factor_total = {}

        for details_json, outcome, profit in rows:
            try:
                details = json.loads(details_json) if details_json else {}
            except Exception:
                continue

            for factor, value in details.items():
                if factor not in factor_total:
                    factor_total[factor] = 0
                    factor_wins[factor] = 0
                if '✓' in str(value):
                    factor_total[factor] += 1
                    if outcome == 'WIN':
                        factor_wins[factor] += 1

        # Win rate per factor
        analysis = {}
        for factor in factor_total:
            total = factor_total[factor]
            wins = factor_wins[factor]
            analysis[factor] = {
                'total': total,
                'wins': wins,
                'win_rate': (wins / total * 100) if total > 0 else 0
            }

        return analysis

    def get_recent_trades(self, symbol, limit=10):
        """Returns the last N trades for a specific symbol."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT outcome, profit, entry_time
            FROM trades
            WHERE symbol = ? AND outcome IN ('WIN', 'LOSS')
            ORDER BY entry_time DESC
            LIMIT ?
        """, (symbol, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [{'outcome': r[0], 'profit': r[1], 'time': r[2]} for r in rows]

    def print_summary(self):
        """Prints a compact performance summary."""
        stats = self.get_daily_stats()
        if stats and stats['total'] > 0:
            print(f"\n[JOURNAL] Today: {stats['total']} trades | "
                  f"{stats['wins']}W / {stats['losses']}L | "
                  f"Win Rate: {stats['win_rate']:.0f}% | "
                  f"P/L: ${stats['total_profit']:.2f}")
