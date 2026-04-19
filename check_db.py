import sqlite3
conn = sqlite3.connect('/app/data/news.db')
conn.row_factory = sqlite3.Row

total = conn.execute('SELECT COUNT(*) as cnt FROM messages').fetchone()['cnt']
fwd = conn.execute("SELECT COUNT(*) as cnt FROM messages WHERE forward_from_channel IS NOT NULL AND forward_from_channel != ''").fetchone()['cnt']
print(f'Total messages: {total}')
print(f'With forward_from_channel: {fwd}')
print(f'Forwarded ratio: {fwd/total*100:.1f}%')

print()
print('=== Top channels that forward most ===')
rows = conn.execute("""
    SELECT s.name, COUNT(*) as total,
           SUM(CASE WHEN m.forward_from_channel IS NOT NULL AND m.forward_from_channel != '' THEN 1 ELSE 0 END) as fwd_count
    FROM messages m JOIN sources s ON m.source_id = s.id
    GROUP BY s.name
    HAVING fwd_count > 0
    ORDER BY fwd_count DESC
    LIMIT 10
""").fetchall()
for r in rows:
    pct = r['fwd_count'] / r['total'] * 100
    print(f"  @{r['name']}: {r['fwd_count']}/{r['total']} forwarded ({pct:.0f}%)")

print()
print('=== Sample forwards WEB3_AGGREGATOR ===')
rows = conn.execute("""
    SELECT m.text, m.forward_from_channel, m.forward_from_msg_id
    FROM messages m JOIN sources s ON m.source_id = s.id
    WHERE s.name = 'WEB3_AGGREGATOR' AND m.forward_from_channel IS NOT NULL AND m.forward_from_channel != ''
    LIMIT 5
""").fetchall()
for r in rows:
    print(f"  fwd_from: {r['forward_from_channel']} (msg_id={r['forward_from_msg_id']})")
    print(f"  text: {r['text'][:120]}")
    print()

conn.close()
