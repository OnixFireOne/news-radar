# Telegram Bot: команды и auto-digest

## Команды

| Команда | Что делает |
|---------|-----------|
| `/start` | Приветственное сообщение |
| `/status` | Статистика: messages, analyzed, pending, sources |
| `/hot` | Топ-5 трендов за последние 6 часов |
| `/digest` | Последний сохранённый дайджест |
| `/digest new` | Сгенерировать свежий дайджест |
| `/digest new 6` | Дайджест за последние 6 часов |
| `/digest new 6 force` | С force (bypass in_digest filter) |
| `/track <topic>` | Подписаться на топик |
| `/untrack <topic>` | Отписаться |
| `/my_tracks` | Список активных подписок |
| `/ask <question>` | Задать вопрос AI-агенту |
| `/help` | Справка |

## Auto-digest scheduling

```python
msk_tz = ZoneInfo("Europe/Moscow")

app.job_queue.run_daily(
    callback=_scheduled_digest_job,
    time=time(hour=12, minute=0, tzinfo=msk_tz)   # 12:00 MSK
)
app.job_queue.run_daily(
    callback=_scheduled_digest_job,
    time=time(hour=20, minute=0, tzinfo=msk_tz)   # 20:00 MSK
)
```

Два дайджеста в день по Москве: в обед и вечером.

## Digest generation flow (бот)

```
/digest new
    │
    ├─ route_via_openclaw = true
    │   └── wake_openclaw → "[NEWS-RADAR COMMAND: manual_digest]"
    │       OpenClaw вызывает /digest/raw → получает raw messages
    │       OpenClaw пишет narrative digest → /digest/queue
    │       bot отправляет в Telegram
    │
    └─ route_via_openclaw = false (Legacy)
        └── POST /digest/generate
            API → NewsAnalyzer.generate_digest()
            Результат → сохранение в digests table
            bot отправляет content_md
```

## OpenClaw integration

```python
async def wake_openclaw(text: str) -> bool:
    url = "http://openclaw:18789/v1/chat/completions"
    payload = {
        "model": "main",
        "user": str(uuid.uuid4()),  # ephemeral session per request
        "messages": [
            {"role": "system", "content": "You are the RoutingAgent..."},
            {"role": "user", "content": text}
        ]
    }
    resp = await client.post(url, json=payload, headers=headers)
    return resp.status_code in (200, 202)
```

Agent получает текстовый event (не JSON) — он сам решает что делать.

## /track → Subscription dispatch

```python
async def cmd_track(update, ctx):
    user_id = str(update.effective_user.id)
    query = " ".join(ctx.args or "")

    # 1. Save to DB
    client.post(f"{API_URL}/subscriptions", json={user_id, query})

    # 2. Notify OpenClaw (agent manages subscriptions)
    wake_openclaw(f"[NEWS-RADAR COMMAND: track]\nUser: {user_id}\nQuery: {query}")
```

## Allowed users

```python
# .env: TELEGRAM_ALLOWED_USERS=123456,789012
for uid in allowed_raw.split(","):
    if uid.strip().isdigit():
        ALLOWED_USERS.add(int(uid.strip()))

def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True  # dev mode: allow all
    return user_id in ALLOWED_USERS
```

Если `TELEGRAM_ALLOWED_USERS` не задан — бот работает в dev mode и отвечает всем.