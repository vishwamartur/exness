
"""
Shared State Module
-------------------
Implements a persistent Key-Value store using SQLite.
Acts as a "Whiteboard" for agents to share state (Market Regime, Risk Status)
without tight coupling.

Schema:
- key: TEXT PRIMARY KEY
- value: TEXT (JSON serialized)
- updated_at: TIMESTAMP
"""

import sqlite3
import json
import os
from datetime import datetime, timezone
from config import settings

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "shared_state.db")

class SharedState:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Creates the KV table if it doesn't exist."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    def set(self, key: str, value: any):
        """
        Saves a value to the store.
        value can be a dict, list, string, int, float (JSON serializable).
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        json_val = json.dumps(value)
        timestamp = datetime.now(timezone.utc).isoformat()
        
        cursor.execute("""
            INSERT OR REPLACE INTO kv_store (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, json_val, timestamp))
        
        conn.commit()
        conn.close()

    def get(self, key: str, default=None):
        """Retrieves a value from the store."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT value FROM kv_store WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            try:
                return json.loads(row[0])
            except:
                return row[0]
        return default

    def delete(self, key: str):
        """Removes a key from the store."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM kv_store WHERE key = ?", (key,))
        conn.commit()
        conn.close()

    def get_last_update(self, key: str):
        """Returns the last update timestamp for a key."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT updated_at FROM kv_store WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        
        return row[0] if row else None

    def get_all(self):
        """Returns a dict of all key-values."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM kv_store")
        rows = cursor.fetchall()
        conn.close()
        
        result = {}
        for key, val in rows:
            try:
                result[key] = json.loads(val)
            except:
                result[key] = val
        return result
