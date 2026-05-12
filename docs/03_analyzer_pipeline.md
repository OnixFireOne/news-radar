# Analyzer: LLM Processing Pipeline

## Главный цикл

```python
async def run_loop(self):
    while True:
        # LLM analysis (каждые 30 мин или при накоплении >10 pending)
        analyzed = await self.analyze_pending()

        # TrendTracker (каждые 15 мин ИЛИ после 20 обработанных сообщений)
        if time_elapsed or (analyzed >= threshold):
            await trend_tracker.run_cycle()

        # Ранний wake-up если pending ≥ max_pending
        if count >= max_pending:
            break

        await asyncio.sleep(10)
```

Два параллельных цикла в одном процессе: анализ и детекция трендов.

## Приоритизация сообщений

```python
rows = conn.execute("""
    SELECT ...
    FROM messages m
    JOIN sources s ON m.source_id = s.id
    WHERE m.analyzed = 0
      AND length(m.text) >= ?
    ORDER BY COALESCE(m.views, 0) DESC, length(m.text) DESC, m.collected_at DESC
    LIMIT ?
""", (min_len, self.batch_size))
```

**Сортировка**: views DESC → длина DESC → время DESC.

Идея: hot-посты с высоким engagement обрабатываются первыми, даже если пришли недавно.

## Параллельная обработка

```python
concurrency = int(self.cfg.get("llm_concurrency", 3))
sem = asyncio.Semaphore(concurrency)

async def process_row(row):
    async with sem:
        # 1. Heuristic ad filter (без LLM)
        if self._is_heuristic_ad(row["text"]):
            return row, {"__heuristic_ad": True}

        # 2. Semantic dedup (ChromaDB)
        embedding = await loop.run_in_executor(None, self.embedder.encode, text)
        if matches := self.chroma.search(query_embedding=embedding, limit=1):
            if matches[0]["similarity"] > 0.90:
                return row, cloned_result  # дубликат — клонируем AI response

        # 3. LLM analysis
        return row, await self._analyze_message(row["id"], text, source)

tasks = [process_row(r) for r in pending_batch]
results = await asyncio.gather(*tasks)
```

**LLM concurrency = 3**: одновременно 3 LLM-запроса. Qwen3 35B на GPU тянет 3 параллельных запроса к Oobabooga без перегрузки.

## Ad Detection: два слоя

```python
# Layer 1: heuristic (мгновенно, до LLM)
def _is_heuristic_ad(self, text: str) -> bool:
    keywords = cfg.get("ad_filter.heuristic_keywords", [])
    return any(kw.lower() in text.lower() for kw in keywords)
    # "#реклама", "на правах рекламы", "спонсорский", "реферальная ссылка"...

# Layer 2: LLM (в complete_json prompt)
"is_ad": <true if this is an ad, sponsored content, giveaway...>
```

Heuristic ловит явные кейсы и экономит LLM-токены. LLM ловит неявные ("этот токен изменит твою жизнь").

## LLM prompt (single message)

```python
SINGLE_MESSAGE_PROMPT = """You are a crypto/financial news analyst.
CHANNEL: {source_name}
MESSAGE: {text}

Write the summary in the same language as the MESSAGE.
Return ONLY valid JSON with no extra text:
{{
  "temperature": <1-10: 1-3 routine, 4-6 interesting, 7-8 hot, 9-10 BREAKING>,
  "topic": "bitcoin | ethereum | altcoins | defi | nft | macro | regulation | hack/scam | exchange | general",
  "summary": "<...> CRITICAL: If the post contains strong subjective opinions, sarcasm,
             or philosophical takeaways, you MUST capture the author's main point
             and attitude in your summary.",
  "keywords": ["<keyword>", ...],
  "sentiment": "<positive | negative | neutral>",
  "is_ad": <true if advertisement>
}}"""
```

**Ключевой момент**: промпт требует сохранять субъективное мнение и сарказм. Без этого LLM превращал editorial-посты в сухие факты — терялся голос канала.

## Thinking mode

```python
# settings.json
llm_thinking_mode: "full"   # или "off"

# Per-call:
disable_thinking = (thinking_mode == "off")
# → chat_template_kwargs = {"enable_thinking": False}  (Qwen3 / Jinja template)
```

`full`: ~15-20 сек/сообщение, лучшее качество.
`off`: ~2 сек/сообщение, ~8-16x быстрее, но температура менее надёжна.

## Topic normalization

```python
def _normalize_topic(self, raw_topic: str) -> str:
    # topics.json: {"bitcoin": {"aliases": ["btc", "биткоин", "биткойн"]}}
    for canonical, meta in self._topics.items():
        if lower in [canonical.lower()] + [a.lower() for a in meta.get("aliases", [])]:
            return canonical
    return lower
```

Проблема: LLM возвращает разные метки для одного тренда ("BTC", "bitcoin", "биткоин"). Решение — `topics.json` с алиасами.

## Alert dispatch (temperature ≥ 9)

```python
if temp >= breaking_alert_min_temp and self.cfg.get("instant_alerts_temperature"):
    asyncio.create_task(
        self._send_instant_alert(msg_id, source, temp, topic, summary, text)
    )
```

`create_task` (не `await`) — alert уходит в фоне, не блокирует анализ.