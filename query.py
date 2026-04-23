import sqlite3

def run():
    conn = sqlite3.connect('/app/data/news.db')
    c = conn.cursor()
    c.execute("SELECT payload_preview FROM dispatch_log WHERE event_type='digest' ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    if row:
        print(row[0])
    else:
        print("No digest log found.")

    print("\n--- TRENDS ---")
    c.execute("SELECT id, topic FROM trends ORDER BY created_at DESC LIMIT 10")
    for row in c.fetchall():
        print(row)

run()
