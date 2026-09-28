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
## Отбор с квотами `ai_value` (ТЗ #4, И3 шаг 5)

При `digest_template: "ai_value"` четыре уровня (alerts / trends / high / fill) заменяет `analyzer/value_funnel.select_with_quotas` — скрипт без LLM, конфиг `digest_templates.ai_value`:

- отсекается всё с `value_score < min_value_score` (5);
- группы и квоты: `practical` = practical_case + tutorial (5), `tools_research` = tool_release + research (2), `hype` = hype_news (1), `crypto` (0 — крипта на паузе);
- крипта проходит только при `temperature ≥ crypto_min_temperature` (8) или в горячем тренде — и всё равно в пределах квоты;
- свободные слоты до `max_items` (8) добирают practical / tools_research / `opinion` (у opinion своей квоты нет); хайп и крипта сверх квоты — никогда;
- сортировка внутри групп и итоговая — `value_score`, затем `temperature`.

С И4 у `ai_value` свой промпт и рендер (ниже). В `settings.json` шаблон включён 27.09.

## Template: ai_value (ТЗ #4, И4)

Порядок в `generate_digest` после отбора по квотам:

1. `DIGEST_PROMPT_AI_VALUE` → `complete_json(task="digest")`. Статьи — в разделителях `<<<ARTICLE N>>>` … `<<<END ARTICLE N>>>` (`knowledge_publisher.frame_article`, обрезка `text_max_chars`); ответ `{items: [{source_id, title, takeaway, summary}]}` на русском.
2. База знаний: `knowledge_publisher.publish_selected` → `md_map` (source_id → blob URL в GitHub). Любой сбой — дайджест уходит без ссылки «разбор (md)».
3. Пункты с неизвестным `source_id` выбрасываются; `content_type` берётся из БД, а не из ответа LLM.
4. `render_digest(..., "ai_value", template_cfg, source_map, md_map)` → Telegram HTML: заголовок «🤖 AI-радар — 27 сентября», у пункта эмодзи и метка из `digest_templates.ai_value.types`, строка takeaway, `<blockquote expandable>` со сжатой идеей и ссылками «источник · разбор (md)».

`source_map` для `ai_value` — `messages.url` (для Telegram — `post_url`).

## База знаний: `analyzer/knowledge_publisher.py` (ТЗ #4, И4)

- Для каждой отобранной статьи с `value_score ≥ knowledge.min_value_score` — отдельный вызов `KNOWLEDGE_MD_PROMPT_AI_VALUE` (`task="knowledge"`, в каталоге `openai.chat_completions` → gpt-6-sol), вход до `knowledge.max_input_chars`.
- md: фронтматтер по р.5 спеки + `## Идея` / `## Вывод`. Путь `knowledge/YYYY/MM/YYYY-MM-DD-<slug>-<message_id>.md`, slug — транслит заголовка.
- Площадки — список `knowledge.targets` (И4.1), md генерируется один раз и уходит на каждую по очереди:
  - `local` — режим тестов: файл пишется в `./knowledge/` рабочей копии (том `./knowledge:/app/knowledge` у `analyzer` и `news-radar-api`), токен не нужен; существующий файл не перезаписывается. В GitHub попадает обычным коммитом владельца.
  - `github` — прод: GitHub Contents API (`PUT /repos/{repo}/contents/{path}`), токен `GITHUB_TOKEN`; 422 «уже есть» считается успехом. Нет токена → площадка пропускается (INFO), остальные работают.
  - `knowledge.batch_commit` (28.09, дефолт `false`, в `settings.json` — `true`): все новые md одного прогона — **одним коммитом** через Git Data API (ref → commit → tree с `base_tree` → commit → `PATCH` ref без force). Если ветку сдвинули между шагами (422), коммит пересобирается на новой голове, до 3 попыток. Сбой коммита → у статей нет `md_path` и ссылки «разбор (md)», следующий прогон попробует снова. Сообщение: `docs(knowledge): add N article summaries`.
- Статья считается опубликованной, если её приняла хотя бы одна площадка. Путь пишется в `analysis.md_path`; если он уже есть — ни LLM, ни публикации, ссылка переиспользуется. Ссылка «разбор (md)» — всегда blob URL в `knowledge.repo` (для `local` — будущий, после коммита).
- `knowledge.enabled: false` или нет ни одной рабочей площадки → публикации нет, строка `Knowledge ...` в логе. Токен в лог не пишется никогда.
- `github`: каждый файл — отдельный коммит в ветку `knowledge.branch` (по умолчанию `main` этого репозитория): локальную `knowledge/` подтягивать `git pull`.

## Именованные дайджесты (И4.1, шаг 2b)

`digests` задаёт имя, `enabled`, упорядоченный список `categories`, времена `at` и часовой пояс `tz`.
`run_digest(name)` вызывает `run_category` для каждой доступной категории и возвращает отдельные части
`DigestPart(category, result, digest_id)`. Пустая категория пропускается; выключенная, неизвестная или
недоступная без каталога провайдеров — пропускается с WARNING. Без имени выбирается первый включённый
дайджест; неизвестное имя даёт ошибку. Если все дайджесты выключены, генерации нет.

Окно начинается от последнего `digests.period_end` с тем же `name` (или от `hours`); для всех частей одного
запуска начало окна фиксируется заранее. Верхнее ограничение глубины — прежние 24 часа. История
кросс-дедупа также ограничена именем. В БД каждая часть — отдельная строка с `name` и `category`.
`in_digest` остаётся общим флагом записи; обычный повтор в другом дайджесте исключён (`force` сохраняет
смысл явной повторной генерации). Связанные сообщения старых смешанных трендов отмечаются только
внутри источников текущей категории. Глобальный сброс `in_digest=2 → 0` сохранён.
Эмоциональный баланс в именованных дайджестах управляется флагом шаблона `emotional_balance`
(дефолт `true`; у `ai_value` — `false`: крипто-ключевые слова алертов к статьям не относятся).

API: `POST /digest/generate?name=articles` возвращает прежние поля первой части плюс `name`, `category`
и массив `parts` со всеми сообщениями и их `parse_mode`. `POST /digest/raw?name=articles` возвращает
`raw_text` первой части и `parts: [{category, raw_text}]`. Нет новостей — 400, неизвестное имя — 404.
`GET /digest/latest?name=articles` и `GET /digest?name=articles` фильтруют сохранённые строки по имени.

Пустые `categories: {}` / `digests: []` сохраняют старый путь через `resolve_legacy`; в БД
`name`/`category` равны NULL. Совместимый `generate_digest()` при настроенных дайджестах запускает
первый включённый и возвращает результат первой непустой части.
