# Digest: генерация и отправка

## ИИ-редактор выпуска

Для категории `articles` после отбора и публикации может запускаться отдельный редактор: он сравнивает
статьи выпуска с лучшими статьями остатка пула и советует админу замены, отмечает дубли и дополнения.
Советы не меняют выпуск, сайт и канал. Они приходят пользователям `ADMIN_USERS` отдельным личным
сообщением после статистики. Сообщение оформлено в Telegram HTML: заголовки статей — ссылки, причины —
цитаты, итог свёрнут. Весь текст модели и статей экранируется, короткие ID (`S1`, `C19`) в причинах
заменяются на начало заголовка. Если Telegram отвергнет разметку, совет уйдёт обычным текстом.

Флаг `digest_templates.ai_value.editor_review.enabled` по умолчанию `false`. Рядом настраиваются
`candidates` (20) и `text_chars` (600). Полный сырой ответ, разобранные рекомендации, модель,
версия промпта и соответствие коротких ID статьям хранятся в `data/editor_reviews/YYYY-MM-DD-articles.json`.
Для замера на старом выпуске: `python tests/eval_editor_review.py --digest-id N`. Ожидаемая стоимость
одного разбора на `gpt-6.1-sol` — около $0.02; фактическая цена зависит от длины статей и ответа.

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
- **мягкая квота (23.10, `quota_min_score`, дефолт `null` — как раньше; в `settings.json` — 8):** practical и
  tools_research резервируют места только статьям с оценкой не ниже порога; слабее — соревнуются за свободные
  места по общему рейтингу. Нет сильных статей группы — место уходит другой теме. Hype и crypto порог не касается
  (у них нет добора). Причина — `specs/reports/editor-review-eval.md`: резерв tools_research почти всегда
  доставался семёркам, восьмёрки ждали ~5 дней.

С И4 у `ai_value` свой промпт и рендер (ниже). В `settings.json` шаблон включён 27.09.

## Template: ai_value (ТЗ #4, И4)

Порядок в `generate_digest` после отбора по квотам:

1. `DIGEST_PROMPT_AI_VALUE` → `complete_json(task="digest")`. Статьи — в разделителях `<<<ARTICLE N>>>` … `<<<END ARTICLE N>>>` (`knowledge_publisher.frame_article`, обрезка `text_max_chars`); ответ `{items: [{source_id, title, takeaway, summary}]}` на русском.
2. База знаний: `knowledge_publisher.publish_selected` → `md_map` (source_id → blob URL в GitHub). Любой сбой — дайджест уходит без ссылки «разбор (md)».
3. Пункты с неизвестным `source_id` выбрасываются; `content_type` берётся из БД, а не из ответа LLM.
4. `render_digest(..., "ai_value", template_cfg, source_map, md_map)` → Telegram HTML: заголовок «🤖 AI-радар — 27 сентября», у пункта эмодзи и метка из `digest_templates.ai_value.types`, строка takeaway, `<blockquote expandable>` со сжатой идеей и ссылками «источник · разбор (md)».

`source_map` для `ai_value` — `messages.url` (для Telegram — `post_url`).

## База знаний: `analyzer/knowledge_publisher.py` (ТЗ #4, И4)

- `knowledge.prompt_version` выбирает полный промпт при `format: "full"`: `knowledge-v2` (дефолт `DEFAULT_CONFIG`) или `knowledge-v3` (в `settings.json` с 23.10). Неизвестная версия → WARNING и v2. Формат `brief` не меняется; сохранённые разборы переиспользуются.
- **v3:** читатель пользуется ИИ, но не специалист в теме. «Коротко» передаёт одну мысль и пользу без жаргона; «Главное» — 3–6 идей; «Как сделано» — как в v2, с дословными формулировками и лимитами автора; «Результаты» — цифры без повторов. Порядок: Коротко → Контекст → Главное → Таблица → Как сделано → Результаты → Ограничения → Что взять себе → Читать оригинал, если…. Схема = поля v2 + обязательный массив строк `table` (шапка и данные, 2–9 строк, 2–5 колонок; пустая ячейка данных → «—»). Пустая таблица не выводится, неверная отбрасывается с WARNING, разбор сохраняется. Блоков «Термины» (убран владельцем 23.10) и «Спросить у ИИ» нет. Модель — `knowledge` из каталога (gpt-6-luna).
- **Сравнение:** `tests/eval_knowledge_prompts.py` принимает повторяемый `--message-id`, `--variants version:model ...`, `--db` (дефолт `DB_PATH` либо `data/news.db`). SQLite открывается через `mode=ro`; публикаций и записи в БД нет. JSON и Markdown — в `tests/golden/knowledge_runs/`. Дефолтные варианты: v2/luna, v3/luna, v3/6.1-sol; tool/text выбирается по возможностям модели в каталоге. Настройки входа и сжатия длинных статей берутся из `knowledge`. Метрики: длина, отношение к v2 (ориентир ≤ 1.2), числа без совпадения в исходнике, наличие таблицы (`glossary_terms` всегда 0 после удаления терминов). Числа сравниваются лексически: округления и преобразования требуют ручной проверки. Прогон платный.

```bash
docker compose run --rm --no-deps analyzer python tests/eval_knowledge_prompts.py --db data/news.server-2026-10-23.db --message-id <id> --variants knowledge-v2:gpt-6-luna knowledge-v3:gpt-6-luna knowledge-v3:gpt-6.1-sol
```

- Для каждой отобранной статьи с `value_score ≥ knowledge.min_value_score` — отдельный вызов `KNOWLEDGE_MD_PROMPT_AI_VALUE` (`task="knowledge"`, в каталоге `openai.chat_completions` → gpt-6-luna с 30.09), вход до `knowledge.max_input_chars`.
- md: фронтматтер по р.5 спеки + тело по `knowledge.format`: `brief` (дефолт кода) — `## Идея` / `## Вывод` (`KNOWLEDGE_MD_PROMPT_AI_VALUE`); `full` (в `settings.json` с 28.09) — структурированный пересказ `KNOWLEDGE_MD_PROMPT_FULL` (`knowledge-v2`): Коротко · Контекст · Главное · Как сделано · Результаты · Ограничения · Что взять себе · Читать оригинал, если…; пустые разделы пропускаются, без «Коротко» или хотя бы 3 пунктов «Главного» — генерация считается неудачной. Цифры округляются до значимых, версии/цены/настройки — точно.
- **Статья целиком (30.09, `knowledge.split_over_chars` > 0, только `full`):** текст не режется по `max_input_chars`; статья длиннее порога (в `settings.json` 150000) делится по абзацам на части по ½ порога, каждая сжимается в заметки (`KNOWLEDGE_CHUNK_PROMPT`, `task="knowledge"`), разбор пишется по заметкам всех частей. `0` — прежняя обрезка. Путь `knowledge/YYYY/MM/YYYY-MM-DD-<slug>-<message_id>.md`, slug — транслит заголовка.
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
запуска начало окна фиксируется заранее. Верхнее ограничение глубины — прежние 24 часа.

**Пул с переносом (29.09, `digest_templates.<шаблон>.carryover_days` > 0, только `select: quotas`):** вместо окна кандидаты — все разобранные, не реклама, `in_digest = 0`, опубликованные за последние N дней (в `settings.json` — 7); порядок `value_score ↓, collected_at ↓, temperature ↓`, до 500 строк, ограничение 24 часа не действует (в LLM уходят только отобранные). Причина: `collected_at` у RSS/HN — время публикации, окно «с прошлого выпуска» теряло статьи, собранные после выпуска. `?hours=` в ручном вызове по-прежнему задаёт явное окно.

**Порядок очереди и список кандидатов (01.10):** `tie_break` — `"temperature"` по умолчанию (как выше) или `"oldest"` (в `settings.json`): при равной оценке выше более старая статья, в SQL `collected_at ↑`, так очередь идёт по порядку, а свежая статья с большей оценкой всё равно обходит старую. Экстра `candidates` (перед `knowledge` в `categories.articles.extras`, флаг `candidates_list.enabled`, `min_score` — нижняя граница строк) строит `knowledge/candidates/<дата>-<имя>.md`: все строки пула от `min_score` и выше с оценкой, типом, ссылкой, датой и статусом (`explain_selection` в `value_funnel.py`: в выпуске / не вместилось / ниже порога / тип отключён, плюс «последний день»). Файл уходит тем же коммитом, что разборы (`publish_selected(extra_files=…)`); после успешной доставки в конце выпуска строка «📋 Все кандидаты выпуска: выбрано N из M» (M — строк пула не ниже `min_value_score`). Список ничего не помечает в БД. Заголовок — первая строка текста статьи: у части RSS (dev.to) это первый абзац, а не заголовок — отдельного поля заголовка в `messages` нет.
 История
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

## Публикация на сайт (И4.3, шаг 2)

`categories.articles.extras` теперь: `candidates`, `knowledge`, `site`. Между `compose` и extras
черновик сохраняется в `ctx.artifacts["draft"]`. Telegram по умолчанию получает полный шаблон `ai_value` (`telegram: "full"`).

Блок `site`:

```json
{
  "enabled": false,
  "live": false,
  "repo": "OnixFireOne/neuronavt",
  "branch": "radar-preview",
  "base_url": "https://neuronavt.blog",
  "posts_dir": "blog/src/content/posts/_digests",
  "reviews_dir": "blog/src/content/reviews",
  "digest_slug": "{date}-ai-radar",
  "wait_for_page_sec": 300
}
```

В `DEFAULT_CONFIG` сайт выключен; в `settings.json` включён предпросмотр (`enabled: true`, `live: false`).
Нужен env `NEURONAVT_GITHUB_TOKEN`: fine-grained token с Contents: Read and write только на
`OnixFireOne/neuronavt`, создать в GitHub → Settings → Developer settings → Fine-grained tokens.
Нет токена — INFO и пропуск публикации на сайт. После добавления env нужен пересозданный контейнер.

Разбор генерируется один раз, как раньше: `knowledge` публикует прежние площадки и готовит
`site_reviews` в artifacts. Площадка сайта включается блоком `site`, независимо от `knowledge.targets`;
настройки генерации и порог оценки остаются в `knowledge`. Возможна публикация только на сайт с
`knowledge.targets: []` (или `knowledge.enabled: false`). Extra `site` одним Git Data API-коммитом кладёт:

- разборы: `reviews_dir/YYYY/MM/YYYY-MM-DD-<slug>-<message_id>.md`;
- дайджест: `posts_dir/<digest_slug>.md`, повторный выпуск в тот же день заменяет этот файл.

Сообщение коммита: `feat(radar): AI radar digest YYYY-MM-DD (+N reviews)`.
Фронтматтер разборов проверяется перед коммитом: обязательные поля, дата UTC **без кавычек**, тип,
оценка 0–10, источник HTTP(S). Невалидный разбор исключается с WARNING, старая ссылка сохраняется.
Описание — одна строка до 280 символов из «Коротко» (`full`) либо «Идея» (`brief`).
Карточки и кандидаты экранируются; URL с другой схемой не становятся ссылками.
Дайджест получает дату публикации на минуту раньше времени выпуска.

`md_path` обновляется только после успешной доставки. В предпросмотре при успешной площадке
`github` остаётся путь `knowledge/...` и Telegram-ссылка на news-radar. Если старый GitHub не доставил
разбор, а сайт доставил — сохраняется путь `reviews_dir/...`; в этом выпуске `live: false` не добавляет
ссылку сайта в Telegram. При `live: true` успешная доставка сайта заменяет путь и ссылку на
`base_url/reviews/<slug>/`. Сбой сайта не меняет `analysis.md_path`; собранный пост сохраняется в базе, Telegram рендерит анонс.
Уже сохранённый путь сайта переиспользуется как ссылка сайта; старый `knowledge/...` как blob news-radar,
без генерации; перенос на сайт — `scripts/migrate_knowledge_to_site.py` (И4.4, шаг 3, ниже).

При `site.enabled: true` **и** `site.live: true` отдельный md кандидатов не создаётся: список с порогом
`candidates_list.min_score` включён в `<details class="radar-candidates">` дайджеста, по убыванию оценки.
В предпросмотре прежний файл кандидатов и ссылка в Telegram сохраняются.
С И4.4, шаг 1, готовые разборы и пост выпуска сохраняются в `site_files` **до коммита**:
путь уникален, контент и URL сохранены, `digest_id` привязывается после записи выпуска.
`committed_at` и `commit_sha` отмечают успешный коммит; изменение контента по тому же пути
сбрасывает обе отметки. Повторный выпуск в тот же день обновляет одну запись поста.
Перед генерацией разбора проверяется база: неопубликованный разбор повторно включается в коммит
без LLM, опубликованный используется только как ссылка. Старые `analysis.md_path` также переиспользуются.
В `settings.json` теперь `knowledge.targets: []`: новые разборы идут только на сайт, дефолт кода `["github"]`.
При выключенном сайте и пустых targets разборы не генерируются.

URL собранного поста сохраняется в artifacts `site_digest_url` даже при сбое коммита.
`digests.site_url` хранит этот URL, `site_status` — `ok` (коммит прошёл), `commit_failed`
(ошибка коммита или исключение после сборки), `skipped` (сайт включён, токена нет),
NULL (выпуск без сайта). `ok` подтверждает коммит, а не завершение деплоя.
`DigestPart.site_url` и `/digest/generate` возвращают URL только при `site.live: true`;
`/digest/latest` возвращает сохранённый URL и статус. При наличии URL рендерится анонс,
при отсутствии — прежний полный дайджест с WARNING. Доставку в настроенные каналы контролирует бот по статусу и доступности страницы;
шаги 1 и 2 выкатываются вместе. Таблица `digest_deliveries` хранит доставку каждой части в канал.

### Главное за день и Telegram-анонс (шаг 3)

`digest_templates.ai_value.digest_prompt_version` выбирает пару промпт/JSON-схема:
`ai_value-digest-v1` — прежние `items` (дефолт кода), `ai_value-digest-v2` — дополнительно `lead` и
`highlights`, `ai_value-digest-v3` — ещё и `picks` (включено в settings с 12.10). Неизвестная версия даёт WARNING и v1.
`lead` — 1–2 русских предложения о главном по статьям выпуска, без выдуманных фактов;
сайт показывает его в блоке «Главное за день». `highlights` — 2–3 коротких пункта на русском,
примерно до 8 слов, каждый об отдельной полезной статье. Пустые и неверные значения пропускаются.
В strict JSON все три поля обязательны; отсутствие содержания выражается пустой строкой/массивом.

`digest_templates.ai_value.telegram`: `full` (дефолт в обоих конфигах) или `announce`.
Анонс содержит дату, число статей и разбивку по рубрикам, «Интересное» из highlights
(без них — первые три заголовка статей выпуска) и ссылку «Читать выпуск на сайте →».
Текст и адрес экранируются для HTML; пункты сокращаются, чтобы анонс был короче 1000 символов.
Если коммит сайта не прошёл, анонс заменяется полным дайджестом с WARNING
`announce fallback: no site digest`.

`digest_templates.ai_value.announce_style` (12.10): `points` — анонс выше (дефолт кода), `teaser`
(в settings) — дата, `lead`, «Стоит открыть:» до трёх зацепок из `picks` с эмодзи рубрики, строка
«Ещё N статей: заголовок · заголовок…» и ссылка. `picks` (промпт v3) — модель сама выбирает до 3
статей и к каждой пишет `hook`: одно предложение до ~20 слов, конкретный факт/цифра плюс зачем
открывать, без кликбейта. Writer оставляет только существующие `source_id` без повторов и пустых
хуков. Без `picks` анонс `teaser` собирается как `points`. Порядок урезания под 1000 символов:
зацепки (до 240 знаков, по целым словам) → `lead` (до 320) → строка «Ещё…» (выпадает, если не влезла).
Образцы на выпусках 8 и 9 — `tests/golden/digest_runs/20261012T110802Z*`.

`announce_style: "cards"` (24.10, пока выключен; settings — `teaser`) — те же `lead` и `picks`, но вёрстка другая: `lead` курсивом,
каждая зацепка — карточка «эмодзи + **заголовок-ссылка на разбор**» и хук отдельной строкой, остальные статьи
выпуска — списком ссылок на разборы в свёрнутой цитате `<blockquote expandable>` («Ещё N статей в выпуске»),
без обрезки заголовков. Адреса разборов кладёт экстра `site` в `ctx.artifacts["site_review_urls"]`
(те же слаги, что у кнопок «Разбор» в посте выпуска); нет адреса — заголовок без ссылки. Анонс уходит обычным
сообщением, поэтому лимит не 1000, а `CARDS_LIMIT` = 3500 знаков сырого HTML (у Telegram 4096 видимых);
не влезающие статьи списка отбрасываются, счётчик «Ещё N» остаётся полным. Без `picks` — как `points`.

Ждёт деплоя **бот, а не анализатор** (решение владельца 08.10): генерация не удлиняется и не упирается
в таймаут бота к API (180 с по расписанию). При `site.live: true` анализатор кладёт адрес страницы выпуска
в `DigestPart.site_url`, API отдаёт его полем `site_url` у части выпуска, а бот (`bot/site_wait.py`) перед
отправкой опрашивает страницу раз в ~15 с до HTTP 200, максимум `site.wait_for_page_sec` (300 с).
Для выпуска без привязанного канала таймаут — WARNING, выпуск всё равно уходит.
Для выпуска с каналом таймаут блокирует доставку этой части и вызывает алерт админам. При `live: false` поля `site_url` нет и ожидания нет.

Для включения после проверки предпросмотра: `site.enabled: true`, `site.branch: "main"`,
`site.live: true` и `digest_templates.ai_value.telegram: "announce"`. Перенос старых разборов остаётся шагом 4.


### Каналы и восстановление публикации (И4.4, шаг 2)

`channels` связывает имена дайджестов с Telegram-каналами. Включённый канал получает
все части своего выпуска по расписанию; личная рассылка `ALLOWED_USERS` для этого имени
не выполняется. Без канала прежняя рассылка сохраняется. `/digest new` отвечает только
спросившему и не публикует в каналы. Статистика и алерты идут только `ADMIN_USERS`.

Для каждой части статус сайта, отличный от `ok`, блокирует публикацию в каналы.
При `ok` бот ждёт HTTP 200 по `site_url` до `site.wait_for_page_sec`; таймаут также
блокирует доставку. Админ получает английский алерт с командой восстановления.
При выключенном сайте части без статуса отправляются в каналы обычным текстом выпуска.
После успешной отправки всех фрагментов части бот записывает `digest_deliveries` через
`POST /digest/{id}/delivered?chat_id=...`. Уже доставленные части пропускаются.

При сбое: проверьте алерт и деплой блога, затем выполните `/digest articles site`
(доступно только админам; имя обязательно). Бот проверит страницу последнего выпуска
одним GET. Если страницы нет, `POST /digest/site/republish?name=articles` отправит
все сохранённые файлы всех частей того же `run_id` одним коммитом, включая ранее
отмеченные опубликованными. LLM и пересборки нет. После ожидания деплоя бот отвечает
анонсом и отправляет части в каналы без соответствующей отметки доставки.
При ошибке коммита или таймауте — только алерт, доставки нет.
`/digest/latest` возвращает `delivered` и части именованного выпуска; republish
возвращает сохранённый `site_url` также при `live: false`.

### Перенос старых разборов на сайт (И4.4, шаг 3)

`scripts/migrate_knowledge_to_site.py` переносит разборы из `knowledge/` на сайт **без LLM**. Источник —
`analysis.md_path`, начинающийся с `knowledge/` (кроме `knowledge/candidates/`); файл читается из `--root`.
Старый md переводится во фронтматтер сайта **без поля `digest`** (страниц старых выпусков на сайте нет; в схеме
блога поле необязательное), `pubDatetime` — дата разбора, 06:10 UTC (09:10 МСК), чтобы старые разборы не
встали наверх списка. Описание — раздел «Идея», иначе «Коротко», иначе первый обычный абзац.

- `--dry-run` — список «откуда → куда» и пропуски (нет файла, не читается, не прошёл проверку фронтматтера); ничего не пишет.
- `--commit` — запись в `site_files` (`digest_id` NULL), **один** коммит в репо сайта; после успеха
  `analysis.md_path` переключается на путь сайта (ссылки следующих выпусков ведут на `/reviews/<slug>/`).
  Сбой коммита — файлы остаются в базе незакоммиченными, `md_path` прежний, повторный запуск их досылает.
- Повторный запуск безопасен: закоммиченные разборы пропускаются.
- Сама папка `knowledge/` и ссылки старых Telegram-выпусков на GitHub не трогаются.

На сервере код запечён в образ без `scripts/` и `knowledge/` — монтировать при запуске (папка `knowledge/`
на сервере — из `git archive` ветки `main`, то есть все разборы, закоммиченные ботом до деплоя):

```bash
cd /opt/news-radar && docker compose -f docker-compose.server.yml run --rm --no-deps -v "$PWD/scripts:/app/scripts:ro" -v "$PWD/knowledge:/app/knowledge:ro" analyzer python scripts/migrate_knowledge_to_site.py --dry-run --root /app
cd /opt/news-radar && docker compose -f docker-compose.server.yml run --rm --no-deps -v "$PWD/scripts:/app/scripts:ro" -v "$PWD/knowledge:/app/knowledge:ro" analyzer python scripts/migrate_knowledge_to_site.py --commit --root /app
```
Включён в settings 14.10. Для работы нужны оба: `enabled: true` **и** `"editor"` последним в `categories.articles.extras` (вне списка
шаг не вызывается). Замер 14.10 на выпусках 8 и 9 — `tests/golden/editor_runs/`: ~7.5k токенов на
вход, ~400 на выход, $0.019. Заголовок статьи — первая строка текста (`candidate_title`), у части
статей это первый абзац.
