# Digest: генерация и отправка

## Priority queue (4 tier)

```python
# 1. ALERTS — hack/scam или temperature >= 9 (всегда первыми)
alerts = [row for row in candidates if is_alert_topic or temp >= 9]

# 2. TRENDS — сообщения из hot трендов (unique_sources >= 3)
trends_tier = [row for row in candidates if row["in_hot_trend"]]

# 3. HIGH — temperature >= min_temp + 2
high_tier = [row for row in candidates if temp >= min_temp + 2]

# 4. FILL — лучшее сообщение на каждый оставшийся topic (разнообразие)
for item in fill_tier:
    if topic not in seen_topics:
        selected.append(item)
```

`max_per_topic = 1`: максимум 1 item на topic. Предотвращает доминацию одной темы.

`always_include_alerts = true`: alerts обходят cap.

## Дедепликация (3 слоя)

```python
# Layer 1: Semantic (ChromaDB cosine similarity)
unique = self._dedup_by_similarity(candidates, threshold=0.85)

# Layer 2: Cross-digest (против предыдущих 2 дайджестов)
selected, ongoing = self._dedup_against_previous_digests(
    selected, lookback=2, threshold=0.75
)
# ongoing = топики которые УЖЕ были в прошлых дайджестах
# Эти топики передаются в LLM как "Продолжение" секция

# Layer 3: Topic cap (1 per topic)
```

**Cross-digest dedup** решает проблему: тренд появляется в дайджесте → потом приходит ещё 5 сообщений о нём → они не попадают в следующий дайджест как новая тема, а маркируются как "Продолжение".

## Emotional balance (проблема из коммита)

```python
# Проблема: если прошлый дайджест начинался с негативных алертов (crash, hack),
# следующий тоже может начаться с негатива → пользователь видит два дайджеста
# подряд с негативным настроением

if prev_was_negative:
    negative_items = [i for i in selected if is_negative(i)]
    non_negative   = [i for i in selected if not is_negative(i)]
    # Первые 2 слота — non-negative, потом negative items, потом остаток
    selected = non_negative[:2] + negative_items + non_negative[2:]
```

Конфиг: `keywords_alert: ["hack", "exploit", "rug", "scam", "SEC", "ban", "liquidat", "crash"]`

## LLM-обогащение для Telegram

```python
async def _enrich_alert_for_telegram(self, event_type, data):
    # breaking_alert: LLM генерирует русский headline из topic+summary
    prompt = ALERT_ENRICH_PROMPT.format(topic=topic, summary=summary)
    result = await self.llm.complete_json(...)
    # Returns: {headline: "BTC упал на $5K после данных по инфляции", summary_ru: "..."}
```

Проблема: topic приходит как английская категория ("regulation"), headline нужен русский. LLM делает перевод и генерирует заголовок за один запрос.

## Template: Classic

LLM возвращает готовый Telegram Markdown. Renderer делает минимальную чистку:

```python
# Fix LLM-hallucinated ** → *
content = content.replace("**", "*")
# Force bold на заголовок
if not lines[0].startswith("*"):
    lines[0] = f"*{lines[0]}*"
```

Проблема: LLM иногда использует GitHub-bold (**), который Telegram не распознаёт.

## Template: Spoiler

LLM возвращает JSON, Python рендерит в HTML. Структура:

```python
DIGEST_PROMPT_SPOILER = """Return ONLY valid JSON:
{{
  "items": [
    {{"title": "Броский заголовок (макс 8 слов)",
      "summary": "Краткое саммари (макс 9 предложений)",
      "source_id": 1}}
  ]
}}"""
```

Python renderer создаёт HTML с `<blockquote expandable>`:

```python
lines = ["🔥 <b>Главное за последнее время:</b>", ""]
for item in items:
    lines.append(f"🔹 <b>{_html_esc(title)}</b>")
    lines.append(f"<blockquote expandable>{_html_esc(summary)}</blockquote>")
    lines.append(f'<a href="{source_url}">источник</a>')
    lines.append("")
```

**Spoiler template** скрывает summary под катом — пользователь разворачивает если интересно. Это решает проблему длинных дайджестов в Telegram.

## URL mapping (проблема из коммита)

```python
# LLM возвращает текст дайджеста с URL как часть текста.
# Проблема: LLM может hallucinate неправильные URL ("t.me/channel/invalid")
# Решение: source_map построен на основе реальных external_id из БД

source_map = {str(i+1): post_url(row) for i, row in enumerate(selected)}
# post_url вычисляет реальную t.me ссылку:
def post_url(row):
    ext_id = row.get("external_id", "")
    src    = row.get("source_name", "")
    if ext_id and src.replace("-", "").isdigit():
        # Числовой channel ID → /c/id format
        clean = src.replace("-100", "").replace("-", "")
        return f"https://t.me/c/{clean}/{ext_id}"
    return f"https://t.me/{src}/{ext_id}" if ext_id else f"https://t.me/{src}"
```

Раньше LLM генерировал URL строкой — мог hallucinate. Теперь source_map гарантирует валидные ссылки из проверенных данных.

## dispatch_log (audit trail)

Каждый отправленный event записывается:

```python
def _log_dispatch(self, event_type, sent_to, status, payload_preview="", http_status=None):
    conn.execute("""
        INSERT INTO dispatch_log (event_type, sent_to, status, payload_preview, http_status)
        VALUES (?, ?, ?, ?, ?)
    """, (event_type, sent_to, status, payload_preview[:300], http_status))
    conn.commit()
```

Позволяет отследить: какой event куда ушёл, когда, с каким результатом.