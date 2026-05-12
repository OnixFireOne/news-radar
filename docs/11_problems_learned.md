# Проблемы и решения: опыт из коммитов

## 1. Stale Trend Alerts on Cold Start Burst

**Проблема**: при перезапуске collector догонял тысячи пропущенных сообщений. TrendTracker сразу кластеризовал их и.fire() алерты для трендов, которые были hot НЕДЕЛЮ назад.

**Решение**: добавили `alerted_at` в trends table. При старте проверяется `is_fresh = (now - cluster.last_seen) < 2 hours`. Alert.fire() только если свежий AND never alerted.

```python
is_fresh = (datetime.now(timezone.utc) - cluster.last_seen).total_seconds() < 7200
if cluster.unique_sources >= min_sources and not alerted_at_val and is_fresh:
    ...fire alert...
```

---

## 2. Duplicate Trend Detection (Message Overlap)

**Проблема**: HDBSCAN с `cluster_selection_epsilon` мог разбить один тренд на 2 кластера. Или при перезапуске тот же тренд создавался заново с другим именем.

**Коммит**: `feat: deduplicate trends by message overlap, make hdbscan_epsilon configurable`

**Три уровня мержинга**:
1. Exact topic name match (within 24h) — быстрое совпадение
2. ChromaDB semantic similarity — если топик изменился но содержимое похоже
3. Message overlap ≥ 2 — если все предыдущие методы не сработали

---

## 3. Hot Trend URL Rendering

**Проблема**: private channels (числовой ID) генерировали некорректные ссылки. Telegram использует формат `t.me/c/{chat_id}/{msg_id}` для приватных каналов.

**Коммит**: `fix: format numeric private channel ids correctly as t.me/c/id URLs`

```python
def post_url(row):
    ext_id = row.get("external_id", "")
    src    = row.get("source_name", "")
    if ext_id and src.replace("-", "").isdigit():
        # -100123456789 → 123456789
        clean = src.replace("-100", "").replace("-", "")
        return f"https://t.me/c/{clean}/{ext_id}"
```

---

## 4. LLM Hallucinated Digest URLs

**Проблема**: LLM генерировал digest с URL-ссылками как часть текста. Мог hallucinate неправильные ссылки.

**Коммит**: `fix: migrate digest spoiler urls from llm string-echo generation to safe python source_map mapping`

**Решение**: отказаться от LLM-генерированных URL. source_map построен из real DB data:

```python
# LLM получает [1], [2], [3] — индексы, не ссылки
# source_map гарантирует валидные URL
source_map = {str(i+1): post_url(row) for i, row in enumerate(selected)}
```

---

## 5. External_ID в TrendTracker Fetch

**Проблема**: после анализа сообщения TrendTracker не мог построить корректную ссылку — external_id не запрашивался в SELECT.

**Коммит**: `fix: add external_id to TrendTracker message fetch query`

```python
rows = conn.execute("""
    SELECT m.id, m.external_id, m.text, ...
    -- external_id был добавлен для построения source_urls
""", ...).fetchall()
```

---

## 6. ChromaDB Collection Loss on Restart

**Проблема**: если ChromaDB контейнер перезапускался (OOM, crash), collection "news_radar" исчезала. Все subsequent upsert-ы падали.

**Решение**: `_reconnect_if_collection_lost()` — при ошибке "Collection does not exist" делается force reconnect + retry once.

```python
def _reconnect_if_collection_lost(self, e):
    if "does not exist" in str(e) or "404" in str(e):
        self._connect(force=True)
        return True
```

---

## 7. HTML Parse Mode in Telegram

**Проблема**: `parse_mode=HTML` не работал. Причина — `<blockquote expandable>` требует HTML, а бот отправлял Markdown.

**Коммит**: `fix: double quotes in a-href tags for hot trend telegram alert to fix telegram html parser`

```python
# Важно: одинарные кавычки в HTML атрибутах не работают
# Использовать двойные кавычки
f'<a href="{source_url}">источник</a>'
```

---

## 8. Breaking Alert Summary Length

**Проблема**: LLM генерировал очень длинные summary для breaking alerts → Telegram message > 4096 символов.

**Коммит**: `feat: constrain breaking_alert summary to max 10 sentences in LLM prompt`

```python
def _trim_to_sentences(text, max_sentences):
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(sentences[:max_sentences])
# + добавлено в prompt: "не более 10 предложений"
```

---

## 9. SQLite WAL Lock (Concurrent Writers)

**Проблема**: collector пишет в messages, analyzer одновременно делает commit. "database is locked" при одновременном доступе.

**Решения (накопленные)**:
- `busy_timeout=30000` — 30 секунд retry при lock
- `check_same_thread=False` — разрешён multi-threaded access
- Commit metadata в collector ДО fetch_history (release write lock)
- `analyzer` использует read-only операции во время analyze_pending

---

## 10. Prompt Quality: Author Voice

**Проблема**: LLM превращал opinion-driven посты (сарказм, эмоции, субъективное мнение) в сухие факты. Терялся голос канала.

**Коммит**: `feat: update llm prompts to preserve author subjective opinions and sarcasm`

```python
# Добавлено в SINGLE_MESSAGE_PROMPT:
"CRITICAL: If the post contains strong subjective opinions, sarcasm,
or philosophical takeaways, you MUST capture the author's main point
and attitude in your summary. Do not reduce editorial posts to dry facts only."
```

---

## 11. Config Watcher Missing Keys

**Проблема**: при добавлении новых ключей в DEFAULT_CONFIG — они не попадали в settings.json при первом запуске.

**Коммит**: `fix: add missing keys to config_watcher default_config`

```python
DEFAULT_CONFIG = {
    "ad_filter": {...},
    "keywords_alert": [...],
    "digest_rules": {...},
    "trend_hdbscan_epsilon": 0.25,  # добавлен позже
}
# _load() мерджит DEFAULT_CONFIG + файл → отсутствующие ключи заполняются дефолтами
```

---

## 12. Digest Cron Hours Parameter

**Проблема**: scheduled digest отправлялся без параметра hours → использовал period_end из последнего дайджеста (мог быть старым).

**Коммит**: `fix: cron sends digest without hours param — uses DB period_end as since`

```python
# Раньше: since = datetime.fromisoformat(last_digest["period_end"])
# Теперь: explicit hours=12 в cron + safety cap 24h
```

---

## 13. Telegram Bot Parse Mode Fallback

**Проблема**: digest с HTML parse_mode падал при отправке (Telegram API error). Пользователь видел пустоту.

**Решение**: fallback на plain text:

```python
try:
    await update.message.reply_text(content, parse_mode=parse_mode)
except Exception as e:
    # Strip HTML tags → plain text
    plain = re.sub(r"<[^>]+>", "", content)
    plain = plain.replace("*", "").replace("_", "")
    await update.message.reply_text(plain)
```

Никогда не отправлять ошибку пользователю — только fallback контент.