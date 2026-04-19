# 📋 TASKS — от планировщика (архитектурный чат)

> Последняя проверка: 2026-04-19 — всё выполнено ✅

---

## ✅ Приоритет 1: Двусторонняя связь news-radar → OpenClaw

Сейчас связь **односторонняя**: OpenClaw опрашивает news-radar раз в 10 минут.
По плану: когда TrendTracker обнаруживает HOT trend → news-radar сам шлёт событие в OpenClaw.

### Что нужно добавить в `analyzer/trend_tracker.py`:

```python
import httpx

OPENCLAW_WEBHOOK_URL = os.environ.get("OPENCLAW_WEBHOOK_URL", "")

async def _notify_openclaw(trend: Trend):
    """Push hot trend event to OpenClaw gateway."""
    if not OPENCLAW_WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(OPENCLAW_WEBHOOK_URL, json={
                "event": "hot_trend",
                "topic": trend.topic,
                "trend_score": trend.trend_score,
                "unique_sources": trend.unique_sources,
                "status": trend.status,
                "summary": trend.summary,
            })
        logger.info(f"Notified OpenClaw: hot trend '{trend.topic}'")
    except Exception as e:
        logger.warning(f"OpenClaw notification failed: {e}")
```

Вызывать при смене статуса на `hot`:
```python
if trend.status == "hot" and was_emerging:
    await _notify_openclaw(trend)
```

Добавь в `.env`:
```
OPENCLAW_WEBHOOK_URL=http://host.docker.internal:18789/event
```

---

## ✅ Приоритет 2: Подписки (TaskAgent не работает правильно)

TaskAgent сделан как поиск по запросу. Нужны **persistent subscriptions**.

### Нужно добавить в `database/schema.py`:
```sql
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    query TEXT NOT NULL,
    last_notified_at DATETIME,
    active INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Нужно добавить в `bot/telegram_bot.py`:
- Команда `/track <тема>` — добавляет подписку
- Команда `/untrack <тема>` — удаляет
- Команда `/my_tracks` — список активных подписок
- Фоновая задача раз в 30 мин: проверить новые совпадения в `/search`, если есть — пуш пользователю

---

## ✅ Приоритет 3: Проверь заполняется ли `summary` в trends

Запусти:
```
docker-compose logs --tail=50 analyzer | grep -i "trend\|cluster\|summary"
```

Если `summary` в таблице `trends` пустой — нужно добавить LLM-вызов в `trend_tracker.py` который читает 3 лучших поста кластера и генерирует summary.

---

## ✅ Что уже хорошо — не трогать

- `/trends`, `/search`, `/similar`, `/duplicates` — реализованы правильно
- ChromaDB + BGE-m3 embeddings — отлично
- Instant alerts (temp ≥ 9) — работает
- 4 tools для OpenClaw — правильная архитектура

---

## После завершения

Запиши в daily note что сделано и обнови этот файл — добавь `✅` рядом с выполненными задачами.
