import sqlite3

try:
    conn = sqlite3.connect('trade_journal.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE trades SET profit = 0 WHERE date(entry_time) = date('now')")
    conn.commit()
    print("Successfully Reset Today's Losses to $0 for the restart.")
except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()
