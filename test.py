import sqlite3
import MetaTrader5 as mt5

mt5.initialize()
conn = sqlite3.connect('trade_journal.db')
cursor = conn.cursor()
cursor.execute("SELECT ticket FROM trades WHERE outcome='OPEN'")
db_tickets = set([t[0] for t in cursor.fetchall()])
print("DB Tickets OPEN:", len(db_tickets))

positions = mt5.positions_get()
mt5_tickets = set([p.ticket for p in positions]) if positions else set()
print("MT5 Tickets OPEN:", len(mt5_tickets))

missing = db_tickets - mt5_tickets
print("Missing tickets:", len(missing))

import datetime
deals = mt5.history_deals_get(datetime.datetime.now() - datetime.timedelta(days=30), datetime.datetime.now())
if deals:
    deals_dict = {}
    for d in deals:
        if d.position_id in missing and d.entry == mt5.DEAL_ENTRY_OUT:
            deals_dict.setdefault(d.position_id, []).append(d)
    
    print("Found deals for missing:", len(deals_dict))
else:
    print("No deals in last 30 days")
