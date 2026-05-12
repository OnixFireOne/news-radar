# Agent Mode: OpenClaw Routing

## Концепция

Есть два режима работы:

```
Legacy (route_via_openclaw = false):
  News Analyzer → LLM → Digest → Telegram

Agent (route_via_openclaw = true):
  News Analyzer → OpenClaw Agent → Narrative Digest → /digest/queue → Telegram
```

Agent-mode позволяет делать более сложные вещи:
- Narrative digest (Agent пишет связный текст, не просто новости)
- Умный routing alerts
- Context-aware ответы на /ask

## Event dispatch

```python
async def _route_event(self, event_type: str, data: dict):
    webhook_url = os.getenv("OPENCLAW_WEBHOOK_URL")

    if event_type == "breaking_alert":
        text = (
            f"[NEWS-RADAR EVENT: breaking_alert]\n"
            f"Topic: {data.get('topic')}\n"
            f"Temperature: {data.get('temperature')}/10\n"
            f"Source: {data.get('source')} ({data.get('source_url')})\n"
            f"Summary: {data.get('summary')}"
        )
    elif event_type == "digest":
        text = f"[NEWS-RADAR EVENT: digest]\nPeriod: {data.get('period')}\n\n{data.get('text')}"
    # ...

    payload = {
        "model": "main",
        "messages": [
            {"role": "system", "content": "You are the RoutingAgent. Process..."},
            {"role": "user", "content": text}
        ]
    }
    resp = await client.post(webhook_url, json=payload)
```

Agent получает текстовое описание события. Шлёт результат обратно в API.

## Digest Agent flow

```
/digest new
    │
    ▼ API: /digest/raw?force=true&hours=6
         Returns: raw messages text (pre-selected, deduplicated)
    │
    ▼ OpenClaw Agent
         Reads messages
         Writes narrative digest (creative writing)
         Calls /digest/queue with narrative_text
    │
    ▼ API: /digest/queue
         Saves to digests table
         Confirms in_digest=1 на выбранных сообщениях
         Sends to Telegram
```

## LLM Lock (межпроцессная синхронизация)

Проблема: если analyzer генерирует digest и одновременно analyzer запускает цикл анализа — LLM (Oobabooga) перегружается.

```python
LLM_LOCK_FILE = "/app/data/llm.lock"

def is_llm_locked() -> bool:
    if os.path.exists(LLM_LOCK_FILE):
        if time.time() - os.path.getmtime(LLM_LOCK_FILE) < 900:
            return True  # залочено другим процессом
        else:
            os.remove(LLM_LOCK_FILE)  # stale lock
    return False

class LLMLock:
    def __enter__(self):
        with open(LLM_LOCK_FILE, "w") as f:
            f.write("1")
    def __exit__(self, ...):
        os.remove(LLM_LOCK_FILE)
```

```python
# В generate_digest():
with LLMLock():
    result = await self.llm.complete_json(...)
```

```python
# В analyze_pending():
if is_llm_locked():
    return 0  # пропускаем цикл анализа пока digest не готов
```

File-based lock работает между Docker containers (shared volume data/).

## dispatch_log

```python
def _log_dispatch(self, event_type, sent_to, status, payload_preview, http_status):
    conn.execute("""
        INSERT INTO dispatch_log (event_type, sent_to, status, payload_preview, http_status)
        VALUES (?, ?, ?, ?, ?)
    """, ...)
```

Эволюция dispatch_log: сначала просто логирование в файл → потом поняли что нужен structured audit trail в БД → добавили таблицу с event_type, sent_to, status, http_status для отладки routing.