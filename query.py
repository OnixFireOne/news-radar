import sqlite3
conn = sqlite3.connect('/app/data/news.db')
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM dispatch_log WHERE event_type LIKE '%digest%' ORDER BY created_at DESC LIMIT 5").fetchall()
if rows:
    print('\n'.join([str(dict(r)) for r in rows]))
else:
    print("NO DIGESTS FOUND IN DISPATCH_LOG")
