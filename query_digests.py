import sqlite3
conn = sqlite3.connect('/app/data/news.db')
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT period_start, period_end, substr(content_md, 1, 100) as preview FROM digests ORDER BY id DESC LIMIT 2").fetchall()
if rows:
    print('\n'.join([str(dict(r)) for r in rows]))
else:
    print("NO DIGESTS FOUND")
