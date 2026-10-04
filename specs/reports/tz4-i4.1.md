# ТЗ #4 — И4.1: категории и дайджесты (р.3.3) + площадки базы знаний (р.5)

**Статус:** ✅ **принята владельцем 01.10.2026** (в работе с 27.09). Последняя часть — «Ручной запуск, битый JSON разбора, нарезка выпуска (29–30.09)» в конце файла.

## Решения владельца 27.09

- **Расписание — у каждого дайджеста своё, независимое, по времени суток, а не интервалом.** Статьи — 9:10 МСК; крипта — как было (12:00 и 20:00 МСК). Дайджесты — отдельные функции; при желании можно запустить вместе (один дайджест с несколькими категориями).
- **Анализатор и сборка дайджеста — не под категории, а кирпичиками.** Функции анализа и сборки не привязаны к категориям жёстко; категория в конфиге выбирает, какие функции брать и с какими параметрами. Цепочка: таймер → функция дайджеста → функции категорий → нужные шаги. Будущие анализаторы и шаблоны подключаются так же.
- Разрешено переписать 3 теста с `analysis_profile` в `tests/test_analyzer_ai_value.py` под категории (ключ убирается по спеке).
- `knowledge.enabled: true`, `targets: ["local"]` в `settings.json` — md на каждую статью дайджеста с `value_score ≥ 6` (платный вызов gpt-6-sol).

## Архитектура (одобрена владельцем 27.09)

Реестры функций по имени; категория — блок конфига, собирающий их:

```
таймер дайджеста → run_digest(name) → для каждой категории run_category(cat):
    collect (источники категории за окно) → select ("tiers" | "quotas")
    → write ("classic" | "spoiler" | "ai_value") → extras ("knowledge") → отдельное сообщение
цикл анализа → для каждой включённой категории: analyzer ("crypto" | "ai_value") + hooks ("alerts", "trends", "subscriptions")
```

```json
"categories": {
  "articles": { "enabled": true,  "sources": ["rss", "hackernews"], "analyzer": "ai_value", "hooks": [],
                "select": "quotas", "template": "ai_value", "extras": ["knowledge"] },
  "crypto":   { "enabled": false, "sources": ["telegram"], "analyzer": "crypto", "hooks": ["alerts", "trends", "subscriptions"],
                "select": "tiers", "template": "spoiler", "extras": [] }
},
"digests": [
  { "name": "articles", "enabled": true,  "categories": ["articles"], "at": ["09:10"], "tz": "Europe/Moscow" },
  { "name": "crypto",   "enabled": false, "categories": ["crypto"],   "at": ["12:00", "20:00"], "tz": "Europe/Moscow" }
]
```

- Параметры шагов — там же, где сейчас (`digest_templates.<шаблон>`), категория может переопределить полем `params`.
- Без блоков `categories` / `digests` — поведение прода как сейчас (крипто-анализатор на всё, дайджест 12:00/20:00 по `digest_template`).
- Шаги: **1** — `knowledge.targets` (сделано); **2a** — рефакторинг в кирпичики без изменения поведения (classic/spoiler байт-в-байт); **2b** — категории, дайджесты, расписание, фильтр трендов, API/бот по имени, миграция `digests.name`/`category`.

## Найдено в коде при планировании

- Расписание дайджеста — два жёстких `run_daily` в боте (12:00, 20:00 МСК, `bot/telegram_bot.py`); `digest_interval_hours` / `DIGEST_INTERVAL_HOURS` на расписание не влияют.
- `since` дайджеста берётся из последней строки `digests` вообще — при нескольких дайджестах окна сбивали бы друг друга → нужна `digests.name`.
- `analyze_pending` берёт `LIMIT batch_size`: записи выключенной категории надо отсекать в SQL, иначе они забьют батч навсегда.
- `trend_tracker.py` кластеризует **все** сообщения без фильтра по типу источника — статьи RSS/HN уже сейчас могут попадать в крипто-тренды и трендовые алерты. Чинится в 2b (хук `trends` только по источникам своей категории).
- md для `local` писать некуда: дайджест собирает `news-radar-api`, `./knowledge` не был примонтирован.

## Шаг 1 — `knowledge.targets` (27.09)

- **`analyzer/knowledge_publisher.py`** — протокол `KnowledgeTarget`, новый `LocalPublisher` (пишет в `./knowledge/…` от рабочего каталога `/app`, существующий файл не перезаписывает, путь вне корня отклоняет). `publish_selected` строит площадки из `knowledge.targets`: md генерируется один раз, площадки — по очереди; статья опубликована, если её приняла хотя бы одна; `github` без `GITHUB_TOKEN` пропускается с INFO, остальные работают. Явно переданный `publisher` (тесты И4) считается площадкой `github`.
- **Конфиг** — `knowledge.targets`: `DEFAULT_CONFIG` = `["github"]` (поведение И4), `settings.json` = `["local"]`, там же `knowledge.enabled: true`.
- **`docker-compose.yml`** — том `./knowledge:/app/knowledge` у `analyzer` и `news-radar-api`; `knowledge/.gitkeep`.
- **Тесты (+4, существующие не тронуты):** запись в `local` без токена + `md_path` + blob URL; не перезаписывает и не выходит за корень; упавший `github` не блокирует `local`; `github` без токена — ни LLM, ни публикации.
- **docs:** `06_digest.md`, `09_config_hot_reload.md`.
- Проверено: API-контейнер импортируется и пишет в `./knowledge` хоста.

## Шаг 2a — кирпичики без изменения поведения (27.09)

- **Фаза A (Codex, `6df89cc`)** — `tests/test_pipeline_characterization.py`: 8 тестов, фиксирующих текущее поведение `generate_digest` (classic по 4 уровням с точным промптом, spoiler, raw, force/hours/since, эмоциональный баланс, OpenClaw) и `analyze_pending` (crypto и ai_value: строки `analysis`, флаги, Chroma, алерты, подписки). Прогнаны на старом коде до рефакторинга. Оркестратор поправил 2 неверных ожидания Codex (fill-уровень берёт одну запись на тему; `has_outcome` по дефолту 0) и одну ошибку mypy.
- **Фаза B (Codex + правка оркестратора)** — пакет `analyzer/pipeline/`: `registry.py` (реестры `ANALYZERS` / `HOOKS` / `SELECTORS` / `EXTRAS` / `WRITERS`), `context.py` (`AnalyzeContext`, `DigestContext`, `CategorySpec`), `analyzers.py`, `store.py`, `hooks.py`, `selectors.py`, `extras.py`, `writers.py`, `legacy.py`. `analyze_pending` / `generate_digest` — дирижёры (−300 строк в `analyzer.py`). Тест `tests/test_pipeline_registry.py`.
- Правка оркестратора: у Codex extras (база знаний) запускались изнутри writer'а `ai_value` — кирпичики снова сцеплены. Writer разделён на `compose` (LLM-черновик) и `render`; дирижёр зовёт `compose → extras → render`. Порядок вызовов прежний (его закрепляет `test_digest_ai_value`: сначала текст дайджеста, потом md).
- Реэкспорт `LLMValueClassifier` / `publish_selected` в `analyzer.analyzer` помечен явно (strict mypy): существующие тесты подменяют их там.
- `renderer.py`, `prompts.py` и существующие тесты не тронуты.
- **Закреплено как есть (сменится в 2b):** алерты и подписки срабатывают и для записей `ai_value`; после `force=True` запись, помеченная `in_digest=2`, следующим вызовом сбрасывается в 0. Трекер трендов не тронут.
- **Решение владельца 27.09:** имя кирпичика `ai_value` не переименовываем (категория — `articles`, анализатор/шаблон — `ai_value`).
- **Кандидаты в `extras` для 2b:** кросс-дедуп против прошлых дайджестов и эмоциональный баланс — по сути крипто-шаги, пока в дирижёре.

## Коммиты

- `1752095` feat(tz4-i4.1): knowledge targets with local test mode
- `6df89cc` test(tz4-i4.1): pin current analysis and digest behaviour before refactor
- `abaab16` refactor(tz4-i4.1): split analysis and digest into registered bricks
- `934c301` feat(tz4-i4.1): categories and named digests with time-of-day schedules

## Новые зависимости

Нет.

## Расхождения со спекой

- Р.3.3 / 8.1: расписание дайджеста — `at` (время суток) + `tz` вместо `interval_hours` (решение владельца 27.09).
- Р.3.3: у категории появляются поля `select`, `hooks`, `extras` (архитектура кирпичиков, решение владельца 27.09).
- Р.5 (из И4, остаётся): имя md с суффиксом `-<message_id>`.

## Что НЕ сделано / отложено

- Живой прогон шага 2b (нужен `LLM_PROVIDERS=openai.chat_completions`) и пересборка образа бота (`docker compose build bot`) — за владельцем.
- Кросс-дедуп и эмоциональный баланс не вынесены в `extras`: остаются в дирижёре под флагами шаблона.
- Два анализатора на одну запись (общий флаг `analyzed`, общая таблица `analysis`) — не нужно, пока у записи одна категория; при третьем анализаторе со своими полями — статус «запись × анализатор» и `analysis.extra` (JSON).
- Смесь категорий в одном сообщении (по спеке — в планах).

## Побочные находки

- Трекер трендов без фильтра по источникам (см. выше).

## Команды приёмки

```bash
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
docker compose run --rm --no-deps news-radar-api python -c "import api.main; print('ok')"
```

**Результат шага 1 (27.09):** 199 passed · mypy `Success: no issues found in 38 source files` · импорт API ok.
**Результат шага 2a (27.09):** 214 passed · mypy `Success: no issues found in 49 source files` · импорт API ok.

## Шаг 2b — категории, имена и расписания (27.09, Codex)

### Что сделано

- `analyzer/pipeline/categories.py`: `CategorySpec.sources`, загрузка/валидация категорий,
  владение источниками, `DigestSpec`, `DigestPart`, разрешение имён, чистая функция слотов,
  предупреждения по источникам. `pipeline/context.py`, `hooks.py`: поле sources и маркер trends.
- `analyzer/analyzer.py`: собственный SQL-фильтр и батч категории, её хуки; недоступные без router
  ai_value-категории пропускаются. Счётчик pending учитывает доступные источники. `run_category` /
  `run_digest`, общий снимок начала окна до генерации частей, запись имени/категории и ID части,
  кросс-дедуп по имени, ограничение отметок соседей по тренду источниками категории.
  Старый `generate_digest` сохранён как совместимый вход. Запущен `cfg.watch()` в основном процессе.
- `analyzer/trend_tracker.py`: необязательный фильтр типов источников; дирижёр обновляет его перед
  циклом, отсутствие категорий с trends пропускает цикл.
- `database/schema.py`: nullable name/category в CREATE и идемпотентных ALTER, старые строки остаются NULL.
- `config/config_watcher.py`, `config/settings.json`: пустые дефолты, категории articles/crypto,
  расписания 09:10 / 12:00+20:00 Europe/Moscow, включены только articles. Удалён analysis_profile.
  В `value_funnel.py` менять код не потребовалось: `_DEFAULT_QUOTAS["crypto"]` уже 0; добавлена регрессия.
- `api/main.py`, `api/models.py`: name, части generate/raw, фильтры истории, 404 неизвестного имени,
  полный `/settings` без затеняющего дубликата. `bot/telegram_bot.py`, `bot/digest_schedule.py`:
  команды с именем, все части отдельными сообщениями, задания по слотам и обновление раз в 60 секунд.
  В образе бота analyzer отсутствует, поэтому парсер расписания лёгкий, без дополнительных зависимостей.
- Новые тесты: `test_categories.py`, `test_digests_named.py`, `test_api_named.py`, `test_bot_named.py`.
  Покрывают маршрутизацию, batch starvation, хуки, пропуск трендов, миграцию, окно по имени,
  несколько частей, старые смешанные тренды, параметры категории, HTTP-контракт, задания и отправку бота.
  В `test_analyzer_ai_value.py` изменены только согласованные fixture/два теста выбора профиля;
  без каталога новое ожидаемое поведение — пропуск вместо crypto fallback. Остальные assertions сохранены.
- `mypy.ini`: новые модули/тесты включены в files и strict. `docker-compose.yml`: read-only том bot
  у analyzer для чистых тестов расписания и mypy. Документы: 03/06/07/09 и грабли 34–35.

### Проверки и ограничения

- `git diff --check` выполнен без ошибок; это не замена тестам.
- Новых зависимостей нет. `renderer.py`, `prompts.py`, спеку и остальные существующие тесты не менял.
- Непустой список полностью выключенных дайджестов означает отсутствие генерации/заданий;
  пустой список означает legacy. `params` объединяется поверхностно (вложенное значение заменяется целиком).
- Сохранён предписанный глобальный сброс raw-отметок `in_digest=2 → 0` в каждой категории.
  Существующие смешанные тренды не мигрируются и не удаляются.
- Расхождения со спекой: новых сверх уже согласованных полей и `at`/`tz` нет.

### Команды приёмки шага 2b

```bash
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
docker compose run --rm --no-deps analyzer python -m mypy --strict analyzer/pipeline/categories.py bot/digest_schedule.py tests/test_categories.py tests/test_digests_named.py tests/test_api_named.py tests/test_bot_named.py
docker compose run --rm --no-deps news-radar-api python -c "import api.main; print('ok')"
docker compose run --rm --no-deps -v "$PWD/tests:/app/tests:ro" news-radar-api python -m unittest discover -s /app/tests -p test_api_named.py -v
docker compose run --rm --no-deps -v "$PWD/bot:/app/bot:ro" -v "$PWD/tests:/app/tests:ro" bot python -m unittest discover -s /app/tests -p test_bot_named.py -v
```

Тесты бота и API используют stdlib unittest в своих образах без установки pytest; в analyzer
они пропускаются при отсутствии Telegram/API. В командах тестов смонтирована папка tests через `-v`.
После приёмки кода: `docker compose build bot` (код бота копируется в образ).

### Проверка оркестратора (27.09)

- Диф просмотрен. Правки оркестратора:
  - mypy-ошибка в `tests/test_categories.py` (патч `asyncio.sleep` через реэкспорт модуля);
  - у Codex эмоциональный баланс для именованных дайджестов был привязан к флагу `ongoing_trends` —
    вынесен в собственный флаг шаблона `emotional_balance` (в `ai_value` = `false`, в `DEFAULT_CONFIG` и
    `settings.json`); legacy-путь без изменений; тест `test_emotional_balance_is_a_template_flag`
    (с проверкой, что без баланса первым стоит «негативный» пункт — тест не пустой);
  - из `DEFAULT_CONFIG.digest_templates.ai_value` тоже убраны `quotas.crypto` и `crypto_min_temperature`
    (значения совпадали с дефолтами `value_funnel.py`, поведение не меняется).
- **Изменения поведения, которые стоит знать владельцу:**
  - `GET /settings` отдаёт теперь **весь** `settings.json` (раньше первый из двух одноимённых обработчиков
    отдавал 3 поля и перекрывал второй). Секретов в `settings.json` нет по правилам проекта.
  - Анализатор впервые реально перечитывает `settings.json` на лету (`cfg.watch()`); раньше hot-reload в
    процессе анализатора не работал вовсе — `ConfigWatcher.get()` файл не перечитывает (грабля 34).
  - `GET /digest/latest` и `GET /digest` не отдают `name`/`category`, если они NULL (`exclude_none`).
  - Бот при недоступном API на старте ставит legacy-слоты 12:00/20:00 и через ≤ 60 с переходит на `digests`.
- Результат: **pytest 228 passed, 5 skipped** · mypy `Success: no issues found in 55 source files` ·
  импорт API ok · `test_api_named` (образ API) 1 OK · `test_bot_named` (образ бота) 4 OK.

### Живой прогон шага 2b (28.09)

- Бот пересобран (`docker compose build bot`), стек поднят с `--profile feeds`. Бот взял расписание из
  `/settings`: `digest:articles:09:10` (Europe/Moscow), заданий крипты нет. Миграции `add_digest_name` /
  `add_digest_category` применились на живой базе.
- Анализатор: `openai.chat_completions`, `classify` = gpt-6-luna. 80 из 89 старых статей (11.09) разобраны
  батчами по 10 без ошибок, ~$0.0013 за батч. Трендовый цикл без категорий с `trends` пропускается.
- По решению владельца включены `sources.rss.enabled` и `sources.hackernews.enabled` (`settings.json`):
  в базе были только статьи от 11.09, вне окна дайджеста.
- `/digest new article` → 404 «неизвестное имя» (ожидаемо). Первый `/digest new articles` → 400
  «no news»: свежие статьи ещё не были проанализированы (коллектор перезапущен за 34 с до команды).
- Второй `/digest new articles` → дайджест №1 (`name`/`category` = `articles`), 8 пунктов, HTML
  6378 символов, **видимых 4254 — больше лимита Telegram 4096**. Бот не доставил ни HTML, ни plain text.
- **Исправлено:** `bot/digest_schedule.py::split_message` режет часть по пустым строкам между пунктами
  (HTML не рвётся), крупный блок — по строкам, затем жёстко. Используется в `/digest` и в отправке по
  расписанию. Тест `tests/test_bot_split.py` (3 теста, strict mypy). Повтор `/digest articles` — пришло
  2 сообщения, владелец подтвердил.
- По решению владельца `digest_templates.ai_value`: `max_items` 8 → 7, `quotas.practical` 5 → 4
  (только `settings.json`; `DEFAULT_CONFIG` не менялся — дефолты кода прежние). 7 пунктов ≈ 3700
  видимых символов — одно сообщение; нарезка остаётся страховкой.
- Приёмка после правки: pytest 231 passed, 5 skipped · mypy `Success: no issues found in 56 source files` ·
  `test_bot_*` в образе бота OK.

**Находки живого прогона (одобрено владельцем чинить шагом 2c):**
1. Токен бота в логах: `httpx` на INFO пишет URL `api.telegram.org/bot<токен>/...`.
2. `chromadb:latest` пишет в `/data`, а смонтирован `./data/chroma:/chroma/chroma` — векторы не переживают
   пересоздание контейнера.
3. bge-m3 грузится трижды при старте (3 × «Loading embedding model»), первый старт на чистой машине
   ~25 мин (скачивание ~2.2 ГБ).
4. Эмбеддинг — отдельным кирпичиком-хуком `embeddings` (решение владельца): новостям нужен, статьям спорно.
5. **luna ставит `is_ad=1` 20 из 80 статей (25%)**, в т.ч. ценные (туториал по MCP-шлюзам — 7, Gradio
   Workflow — 7, IBM Granite — 6): вендорский пост о своём продукте считается рекламой. Решение владельца —
   вариант (а): уточнить определение рекламы в промпте, добавить такие примеры в golden set, перемерить luna.
6. Ответ API «no news or LLM error» не различает пустое окно и сбой LLM.
7. Ссылки «разбор (md)» ведут на GitHub (`knowledge.repo`), а `knowledge.targets = ["local"]` — файлы лежат
   только в `knowledge/`, по ссылке 404, пока их не закоммитить и не запушить.

## Шаг 2c — находки живого прогона (28.09, Claude Code)

### Что сделано

1. **Токен бота в логах** — `httpx` → WARNING в `bot/telegram_bot.py::main`, `analyzer/analyzer.py::main`,
   `api/main.py` (на импорте модуля: uvicorn запускает API без `__main__`).
2. **Том Chroma** — `docker-compose.yml`: `./data/chroma:/data` (chroma 1.x пишет в `/data`). Живые векторы
   (3.8 МБ) пока внутри контейнера — **до пересоздания** скопировать: `docker cp news-radar-chroma:/data/. data/chroma/`
   (оркестратору это действие заблокировано, за владельцем). Грабля 38.
3. **bge-m3 грузился трижды** — баг в `analyzer/embedder.py::_load`: под блокировкой была только проверка,
   загрузка шла вне её. Теперь внутри. Грабля 37.
4. **Хук `embeddings`** (`analyzer/pipeline/hooks.py`, маркер) — без него: нет пре-дедупа и `encode` при анализе,
   нет записи в Chroma (`chroma_synced=0`), нет дедупа внутри дайджеста и кросс-дедупа. Проводка:
   `AnalyzeContext.embeddings`, `_preflight(..., embeddings)`, `store.py`, `run_category`. Legacy-набор получил
   `embeddings` (поведение прежнее). `settings.json`: у `crypto` хук добавлен, у `articles` нет. `trends` без
   `embeddings` — WARNING в `load_categories`.
6. **«Пусто» или «сбой»** — `NewsAnalyzer.digest_failures` (`no_news` / `llm` / `db`, по категориям; пустой
   рендер тоже `llm`). `POST /digest/generate`: 400 «No new analyzed news for digest» или 502 «Digest generation
   failed (llm error), see analyzer logs». Бот показывает `detail` как раньше.

- Тесты (с разрешения владельца 28.09): фикстура `tests/test_analyzer_ai_value.py` — у articles
  `hooks: ["embeddings"]` (проверки пути с векторами без изменений); `tests/test_pipeline_registry.py::test_legacy_resolution`
  ожидает `("embeddings", "alerts", "subscriptions")`. Новые: `tests/test_embeddings_hook.py` (6: анализ с хуком и
  без, дедуп дайджеста с хуком и без, `digest_failures`, одна загрузка модели из трёх потоков — без фикса падает),
  `tests/test_api_digest_errors.py` (400/502, образ API). Оба в `mypy.ini` strict.
- Документы: `docs/03_analyzer_pipeline.md` (хук, коды ответа), грабли 37–39.
- Новых зависимостей нет. Расхождений со спекой нет.
- Коммит: `4ee7766` fix(tz4-i4.1): address step 2b live-run findings.

### Результат приёмки

pytest 237 passed, 6 skipped · mypy `Success: no issues found in 58 source files` · импорт API ok ·
`test_api_*` (образ API) 2 OK · `test_bot_*` (образ бота) OK.

### Что НЕ сделано

- **п.5 реклама** — не сделан, нужна разметка владельца (план ниже).
- **п.7 ссылки «разбор (md)» ведут на GitHub при `targets: ["local"]`** — не был в списке одобренных, не трогал.
- Пересборка/пересоздание контейнеров (`docker compose up -d --build`) — за владельцем, после `docker cp` векторов.

### Побочные находки

- Сейчас `is_ad=1` у **47 из 159** разобранных статей RSS/HN (30%), среди них `value_score` 7–8 (Хабр — правила
  для LLM, 8; MCP-шлюзы, 7; Gradio Workflow, 7; OTUS/MLOps, 7). `is_ad=1` исключает статью из дайджеста целиком.
- `chromadb/chroma:latest` — тег плавает; сменил путь данных без предупреждения. Предложение: закрепить версию.

### План п.5 (реклама) — на согласование

1. **Разметка:** `tests/golden/ADS.md` (датированный набор 28.09) — ~16 статей из этих 47: 8 с высокой оценкой
   (вероятно, ложная «реклама»: вендорский технический пост) и 8 явных (курсовые работы, отельные системы, «Top AI tools
   2026» с партнёрскими ссылками). Тексты заморожены в `tests/golden/ads_candidates.jsonl`. Владелец ставит
   `реклама: да/нет`.
2. **Метрика:** `eval_value_scoring.py --ads` считает ложные «да» и пропущенные «да». Порог (уточнён 28.09, число «да»/«нет»
   заранее неизвестно): ложных «да» ≤ 15% от меток «нет», пропущенных ≤ 25% от меток «да»; точность ценности на основном наборе не ниже 88.9%.
3. **Промпт `ai_value-v3`:** `is_ad=true` только если главная цель — продать/привлечь без самостоятельной пользы
   (спонсорский пост, партнёрские ссылки, реклама услуги, купоны). Пост компании о своём продукте с техническими
   деталями, релиз, туториал на своём инструменте — `is_ad=false`, самореклама снижает `value_score`.
4. **Замер luna:** 2 прогона по основному набору и набору рекламы (~$0.02). Правило «не трогать classic/spoiler»
   соблюдается: меняется только `AI_VALUE_MESSAGE_PROMPT`.

### План шага 2d (статистика админу) — на согласование

Сейчас usage есть только в логах (`UsageTracker` в памяти, `classifier.calls`), в БД ничего не хранится.

1. **Учёт:** таблица `llm_usage` (время, задача `classify`/`digest`/`knowledge`/`legacy`, провайдер, модель,
   токены вход/выход, `cost_usd`, `cost_source`, категория). Идемпотентная миграция в `database/schema.py`.
   `llm_core` остаётся без БД (переносимый плагин): у `UsageTracker` — необязательный колбэк-приёмник, анализатор
   подключает `analyzer/usage_store.py`, который пишет в SQLite. Классификатор (`classifier.calls`) пишется туда же.
2. **Статистика дайджеста** (`analyzer/digest_stats.py`, чистая функция по БД и окну): собрано по источникам,
   разобрано, реклама, ошибки/в очереди, прошло порог, попало в выпуск, md-разборов; токены и деньги по задачам
   с прошлого выпуска этого имени.
3. **Доставка:** `POST /digest/generate` добавляет `stats` в ответ; бот отдельным сообщением шлёт их админам.
   Новая переменная `TELEGRAM_ADMIN_USERS` в `.env.example` (ID через запятую; пусто — не слать). Флаг
   `digest_stats.enabled` (по умолчанию `false`) — в `DEFAULT_CONFIG` и `settings.json`. Команда `/stats [имя]` —
   по запросу.
4. **Вопросы владельцу:** (а) только к дайджесту или ещё по каждому циклу анализа (раз в 30 мин — шумно;
   предлагаю только к дайджесту плюс `/stats`); (б) цены GPT-6 взяты из листинга OpenRouter — деньги будут
   оценкой, пока не сверены с биллингом OpenAI.
- Новых зависимостей нет. Исполнитель — Codex по брифу (модуль на ~300–400 строк с тестами).

### Команды приёмки шага 2c

```bash
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
docker compose run --rm --no-deps news-radar-api python -c "import api.main; print('ok')"
docker compose run --rm --no-deps -v "$PWD/tests:/app/tests:ro" news-radar-api python -m unittest discover -s /app/tests -p 'test_api_*.py'
docker compose run --rm --no-deps -v "$PWD/bot:/app/bot:ro" -v "$PWD/tests:/app/tests:ro" bot python -m unittest discover -s /app/tests -p 'test_bot_*.py'
docker cp news-radar-chroma:/data/. data/chroma/   # до пересоздания chroma
docker compose --profile feeds up -d --build
```

## Шаг 2d — статистика дайджеста админам (28.09, Codex + Claude Code)

Решения владельца 28.09: план 2d — ок, **только вместе с дайджестом** (плюс `/stats` по запросу); цены сверить.

### Что сделано (Codex по брифу, диф проверен оркестратором)

- **Учёт LLM в БД:** таблица `llm_usage` (время, задача, категория, провайдер, модель, токены, `cost_usd`,
  `cost_source`), миграции `add_llm_usage*`. `llm_core/usage.py`: у `UsageTracker` слушатели
  (`add_listener`, сбой слушателя не ломает вызов), у `UsageRecord` поля `task`/`category`. `llm_core` по-прежнему
  без SQLite. `analyzer/usage_store.py::install_usage_sink` — запись в SQLite, ставится в анализаторе и на старте
  API (дайджест генерируется в процессе API). Категория — через `contextvar` `usage_category`. Вызовы
  классификатора пишутся в брике `ai_value` по `classifier.calls` (один раз на вызов).
- **Состав выпуска:** таблица `digest_messages (digest_id, message_id)` и `digests.run_id` (части одного запуска).
  Решение Codex: `in_digest` не говорит, в каком выпуске статья; у старых выпусков состав — «н/д».
  `digests.created_at` теперь пишется явно (UTC, с микросекундами), раньше — `CURRENT_TIMESTAMP`.
- **`analyzer/digest_stats.py`:** окно — от предыдущего выпуска с тем же именем до текущего (у первого — его
  `period_start`); собрано по типам источников, разобрано, реклама, в очереди, прошло порог `min_value_score`,
  в выпуске, md-разборов; токены и деньги по задачам (`≈` и «цена неизвестна», если у вызова нет цены).
  Текст ≤ 1500 символов.
- **API:** `POST /digest/generate` добавляет `stats` при `digest_stats.enabled` (сбой статистики — WARNING,
  дайджест отдаётся); `GET /digest/stats?name=` — статистика последнего выпуска, 404 без выпуска.
- **Бот:** `TELEGRAM_ADMIN_USERS` (в `.env.example`), статистика отдельным сообщением только админам — после
  дайджеста по расписанию и после `/digest`; `/stats [имя]` только для админов.
- **Конфиг:** `digest_stats.enabled` — `false` в `DEFAULT_CONFIG`, `true` в `settings.json`.
- **Документы:** `docs/01_database.md`, `07_bot.md`, `08_api.md`, `09_config_hot_reload.md`, `12_llm_providers.md`.
- **Цены GPT-6 сверены** с developers.openai.com/api/docs/pricing 28.09: luna $0.10/$0.50, sol $2/$10,
  astra $10/$50 — совпадают с каталогом. Не моделируется: кэшированный вход в 10 раз дешевле (оценка завышена),
  длинный контекст дороже. Комментарий в `config/providers.json` (коммит `ce7af5e`).
- Коммиты: `138528a` feat(tz4-i4.1): digest statistics for admins · `7f1d190` test(tz4-i4.1): is_ad eval against owner ad labels.
- Тесты: `tests/test_usage_store.py` (5), `tests/test_digest_stats.py` (1, плотный: окно, изоляция имён и
  категорий, границы, цены, формат, длина), `tests/test_api_digest_stats.py`, `tests/test_bot_digest_stats.py` (3).
- Новых зависимостей нет. Расхождений со спекой нет (р.3.3 статистику не описывает — это пожелание владельца
  28.09; при случае внести в спеку).

### Результат приёмки

pytest 245 passed, 10 skipped · mypy `Success: no issues found in 66 source files` · импорт API ok ·
`test_api_*` (образ API) 3 OK · `test_bot_*` (образ бота) 7 OK.

### Что НЕ сделано / за владельцем

- Вписать свой Telegram ID в `TELEGRAM_ADMIN_USERS` в `.env` (пусто — статистику не получает никто).
- Пересборка бота и API: `docker compose build bot` и `docker compose --profile feeds up -d --build`.
- Живая проверка не делалась. Учтите: токены классификатора копятся только с подъёма нового анализатора, поэтому
  первая статистика покажет расходы на разбор неполностью; у старых выпусков (без `run_id`) состав — «н/д».

### п.5 — подготовлено

- `tests/golden/ADS.md` + `ads_candidates.jsonl` (18 статей, коммит `ce7af5e`) — ждут разметки владельца.
- `tests/eval_ad_flags.py` — замер `is_ad` по разметке (ложные «да» ≤ 15% от «нет», пропущенные ≤ 25% от «да»),
  тесты `tests/test_eval_ad_flags.py`. Запуск после разметки:
  `docker compose run --rm --no-deps analyzer python tests/eval_ad_flags.py --provider openai.chat_completions --model gpt-6-luna`

### 28.09, вечер: итог расходов и разметка рекламы

- **Статистика:** добавлена строка «Всего с <дата первой записи>: $…» — все расходы на LLM за всё время учёта
  (`llm_usage` без фильтров, по просьбе владельца). Учёт начался 28.09 с подъёма нового стека; более ранние
  расходы в БД нет. Тест `test_total_spend_covers_all_recorded_usage`.
- **`ADS.md` размечен Claude** по правилу владельца «полезно — значит не реклама», каждая статья прочитана
  целиком; владелец проверяет. Итог: 7 «да», 9 «нет», 2 «пропуск» (a01/a05 — тизеры openai.com без полного
  текста; размечаем только по полному тексту).
- **Контрольный замер v2 (luna, 2 прогона, $0.008):** ложных «да» 5–6 из 9 (56–67%), пропущенных 0–1 из 7.
  Порог — ложных ≤ 15%. Промпт v2 считает рекламой любой пост компании о своём продукте.
- Следующее: после проверки разметки владельцем — промпт `ai_value-v3` + замер `eval_ad_flags.py` и основного eval.

### п.5 — промпт `ai_value-v3` и замер (28.09)

Разметка `ADS.md` принята владельцем. `analyzer/prompts.py`: версия `ai_value-v3`, в конец
`AI_VALUE_MESSAGE_PROMPT` добавлено узкое определение `is_ad`: реклама — только если главная цель продать
или собрать лиды и пользы без покупки нет (спонсорские/партнёрские посты, реклама услуг и курсов,
«свяжитесь с нами», SEO-текст ради ссылки на продукт, пресс-релиз с успехами клиентов одного вендора,
«инструкция», где все шаги — пользоваться своим платным инструментом). Пост компании о своём продукте с
пользой, анонс курса в конце полезной статьи, хайп без продажи — не реклама: такое понижает `value_score`.
Шаблоны `classic`/`spoiler` и их промпты не тронуты.

| Замер (luna) | v2 | v3 |
|---|---|---|
| `eval_ad_flags.py`, ложные «да» из 9 | 5–6 (2 прогона) | 0–1 (3 прогона) |
| `eval_ad_flags.py`, пропущенные «да» из 7 | 0–1 | 1–2; PASS в 2 из 3 прогонов |
| основной golden set (полный текст), точность | 91.0% (4 прогона 27.09) | 91.7% ×3, хайп ≥ 8 — 0 |
| стоимость прогона | — | ~$0.004 реклама, ~$0.009 основной |

- Всегда пропускается a07 (подборка кейсов клиентов Smart Engines) — модель видит в ней пользу; по правилу
  владельца случай спорный. a17 (GenImager) — пропуск в 1 из 3 прогонов.
- Первый черновик v3 (без пунктов про пресс-релиз и «инструкцию через свой инструмент») мерился 2 раза:
  ложные 0–2, пропущенные 1–2 (2 из 7 — FAIL). Файлы `runs/ads-20260928T1604*` / `T160449Z` — это он,
  в поле `prompt` тоже стоит `ai_value-v3`.
- **Оговорка (грабля 26):** промпт подтянут на том же наборе из 16 статей, на котором мерился; честная
  проверка — доля `is_ad=1` на следующих живых статьях (была 30%) и новый датированный набор.
- Уже разобранные статьи не переразбираются: у 47 старых записей `is_ad=1` остаётся. Переразметить их —
  отдельное решение владельца (сбросить `analyzed` → повторная классификация, ~$0.01).
- Новых зависимостей нет. Прогоны — `tests/golden/RESULTS.md`, `tests/golden/runs/`.

### Живой прогон после v3 и 2d (28.09, 21:18 по +05)

- **Анализатор работал на старом процессе** (старт 16:16, раньше коммитов 2d и v3): цикл в 21:01 разбирал по
  `ai_value-v2` (id 217–226: 6 из 10 — «реклама»), `llm_usage` не пополнялась. `up -d --build` контейнер не
  пересоздал; пересоздан с `--force-recreate` в 21:18, версия в процессе — `ai_value-v3`. Грабля 41.
- Первый цикл на v3: 3 статьи (id 225–227), `is_ad=0` у всех, в `llm_usage` появилась запись `classify`
  gpt-6-luna $0.0006. Выборка мала — долю `is_ad` смотреть после нескольких циклов коллектора.
- Всего сейчас `is_ad=1` у 67 из 227 разобранных статей (все, кроме 3, разобраны v2).
- `GET /digest/stats?name=articles` отвечает (выпуск №1: состав «н/д», LLM «нет вызовов» — выпуск старше учёта,
  как и ожидалось). Итог «Всего с 28.09.2026: $0.0006».
- **`TELEGRAM_ADMIN_USERS` в контейнере бота пуст** — статистику после дайджеста бот не пришлёт никому.
  Нужно вписать свой Telegram ID в `.env` и пересоздать бота.
- Новый дайджест оркестратор не генерировал: `POST /digest/generate` помечает статьи `in_digest` и тратит
  sol, а доставку и статистику в Telegram проверить можно только через бота.

### Живой выпуск и вид статистики (28.09, вечер)

- Стек пересоздан целиком (`up -d --build --force-recreate`), векторы Chroma на месте (`./data/chroma:/data`).
- `/digest articles` без `new` показывает сохранённый выпуск и статистику не шлёт — это ожидаемо.
  `/digest new articles` → выпуск из 7 пунктов, статистика пришла админу. Окно 3.8 ч: собрано 82, разобрано 79,
  реклама 24 (в основном разобраны ещё v2), прошло порог 41, в выпуске 7, md-разборов 7, $0.074 за выпуск.
- **v3 на живых статьях:** 12 разобранных — 1 «реклама» (id 229), ~8% против 30% на v2. Выборка мала.
- **Статистика: модель у каждой задачи** (просьба владельца) и вид по строкам с иконками.
  `analyzer/digest_stats.py`: `TaskUsage.models` (`GROUP_CONCAT(DISTINCT model)`, в сравнении не участвует —
  старый тест не менялся), задача — отдельной строкой `• classify (gpt-6-luna): …`. Новый тест
  `test_usage_lists_models_per_task`. pytest 247 passed, 10 skipped · mypy ok · API/бот unittest OK.

### Переразбор старой «рекламы» по v3 (28.09, решение владельца)

- 67 статей с `is_ad=1`, разобранных v2 (`analysis.analyzed_at` до 16:18:47 UTC), ни одна не была в выпуске:
  строки `analysis` удалены, `analyzed=0, is_ad=0`. Копия базы — `data/news.db.bak-before-v3-reclassify`,
  список id — `data/reclassify_v3_ids.txt`. Очередь разобралась за ~3 мин (ранний подъём при ≥ 10 в очереди),
  ~$0.015, ошибок нет.
- **Итог:** рекламой остались 17 из 67 — в основном мусор с оценкой 1–3 (стоматология, курорт, финтех-SEO,
  блокчейн-студия); спорная одна — id 81 (MCP, оценка 6). 50 вернулись в отбор, из них 8 с оценкой 7–8
  (Gradio Workflow, skillmem, GLM → Jev, OTUS/MLOps, DealMind, Keva). По базе теперь `is_ad=1` у 19 из 240 (8%).
- Большинство вернувшихся статей от 11.09 — вне окна дайджеста; в выпуски попадут только свежие.

### Сон мака и один коммит на выпуск (28.09, по решению владельца)

- **Сон:** PTB `run_daily` без `misfire_grace_time` → APScheduler выкидывает задание, опоздавшее больше чем на 1 с;
  если в 09:10 мак спит, выпуск за день пропадал молча. `bot/telegram_bot.py`: `job_kwargs` —
  `misfire_grace_time` 6 ч, `coalesce`. Тест `tests/test_bot_misfire.py` (параметры задания + настоящий
  планировщик догоняет проспанный запуск один раз). Коммит `c75e172`. Не покрыто: перезапуск бота после 09:10.
- **`knowledge.targets: ["github"]`** вместо `["local"]`; `GITHUB_TOKEN` у владельца (fine-grained, Contents: write
  на news-radar), право push проверено.
- **`knowledge.batch_commit`** (дефолт `false`, в `settings.json` — `true`): все md прогона одним коммитом через
  Git Data API, повтор при сдвинутой ветке (422) до 3 раз. Флаг, а не замена: `test_digest_ai_value.py` подменяет
  `GitHubPublisher.publish` и идёт путём из конфига — с флагом по умолчанию тест не менялся. Тесты
  `tests/test_knowledge_batch.py` (порядок вызовов, повтор, сбой дерева, прогон publish_selected: один коммит,
  при сбое — ни ссылок, ни `md_path`). Живой токен: чтение ref и commit — 200; запись проверится первым выпуском.
- pytest 253 passed, 12 skipped · mypy `Success: no issues found in 68 source files` · бот unittest OK.
- Новых зависимостей нет. Расхождение со спекой: р.5 описывает Contents API (`PUT .../contents/{path}`) — при
  `batch_commit` используется Git Data API.

### План: перенос статей и 2–3 сообщения (28.09) — на согласование

**Найдено при планировании (баг потери статей, уже в проде):** окно выпуска — `collected_at >= since`, где `since` —
конец прошлого выпуска. Для RSS/HN `collected_at` — **время публикации** (`poll_runner.py`: `msg.timestamp`), а не
сбора. Статья, опубликованная до выпуска, но собранная/разобранная после него (опрос раз в час, HN набирает очки
часами, догрузка текста, разбор раз в 30 мин), в следующее окно уже не попадает и теряется молча. Сейчас в базе 90
статей за 3 дня с оценкой ≥ 5, не бывших ни в одном выпуске. Перенос ниже закрывает и это.

**1. Пул вместо окна** (только категории с `select: quotas`, т.е. `ai_value`). Кандидаты: разобраны, не реклама,
`in_digest = 0`, `value_score ≥ min_value_score`, опубликованы не раньше чем `carryover_days` дней назад.
Ранжирование: оценка ↓, при равной — свежее выше, затем «горячесть». `LIMIT 100 ORDER BY temperature` для этого пути
заменить на порядок по оценке. Старше срока — выпадают из пула (не удаляются).

**2. Части.** Часть 1 — как сейчас: квоты 4/2/1, до 7 пунктов, порог 5. Части 2 и 3 — из остатка пула с теми же
квотами, но только из статей с оценкой ≥ `extra_parts_min_score` и только если таких набирается хотя бы
`extra_parts_min_items`. Без этого порога при пуле в 90 статей каждый день уходило бы 3 части по 7, включая «пятёрки».
Статья не попадает в две части. Не вошедшее остаётся в пуле на следующий выпуск.

**3. Разборы (md) — для всех частей** (решение владельца), одним коммитом на весь запуск: сначала отбор всех частей,
затем один `publish_selected` по объединению, затем рендер каждой части.

**4. Выдача.** Каждая часть — отдельная запись `digests` с общим `run_id` (уже есть) и своим `digest_messages`;
отдельный вызов LLM на текст части; заголовок «AI-радар — 29 сентября (2/3)». Бот уже шлёт части подряд.
Проверить и при нужде поправить `/digest/latest?name=` — должен отдавать все части последнего запуска.

**5. Статистика** — одно сообщение на запуск: «в выпуске 7+7+5», добавить «из прошлых дней N», «в очереди M»,
«истекло без выпуска K».

**6. Конфиг** (`DEFAULT_CONFIG` + `settings.json`, дефолты сохраняют текущее поведение):

| Ключ (`digest_templates.ai_value`) | Дефолт в коде | `settings.json` |
|---|---|---|
| `carryover_days` | 0 — старое окно | 3 |
| `max_parts` | 1 | 3 |
| `extra_parts_min_score` | 7 | 7 |
| `extra_parts_min_items` | 4 | 4 |

**7. Стоимость:** дополнительная часть ≈ $0.016 текст + до 7 × ~$0.008 md ≈ $0.07; максимум ~$0.20 в день.

**8. Тесты:** статья до прошлого выпуска, но не бывшая в нём, попадает в пул; старше срока — нет; часть 2 не
создаётся при < 4 статьях ≥ 7; нет статьи в двух частях; квоты внутри каждой части; один коммит md на запуск;
статистика; при дефолтах — прежний результат; `classic`/`spoiler` не тронуты.

**9. Исполнитель:** Codex по брифу (крупная правка `analyzer.py` + `value_funnel.py`), Claude проверяет диф, гоняет
приёмку, коммитит. Новых зависимостей нет. **Расхождение со спекой:** р.3.2 (окно и один выпуск) — правит владелец.

### Разбор md — структурированный пересказ (28.09, решение владельца)

- Владелец: «Идея/Вывод» слишком коротки, разбор должен заменять чтение статьи. `analyzer/prompts.py` —
  `KNOWLEDGE_MD_PROMPT_FULL` (`knowledge-v2`), старый промпт не тронут. `knowledge_publisher.py`: `build_full_body`
  (8 разделов, пустые пропускаются; без «Коротко» или < 3 пунктов «Главного» — отказ), `KnowledgeDoc.body`,
  флаг `knowledge.format` (`brief` в `DEFAULT_CONFIG`, `full` в `settings.json`), `max_input_chars` 24000 в
  `settings.json`. Тесты `tests/test_knowledge_publisher_full.py` (6); старые тесты не менялись.
- Образцы на 3 статьях (id 114, 121, 127; `data/knowledge-samples/`, вне git): 612–733 слова, ~$0.03 за разбор
  (оценка по токенам: скрипт образцов не пишет `llm_usage`). По просьбе владельца добавлено округление цифр —
  проверено повторной генерацией 114.
- pytest 260 passed, 12 skipped · mypy `Success: no issues found in 69 source files`. Новых зависимостей нет.
  Спека v1.15 (р.5) — внёс исполнитель по поручению владельца.

### Экономика, перенос 7 дней, luna на разборах (29.09)

- **Поток:** за 28.09 опубликовано 148 статей, оценка ≥ 5 — 98, ≥ 6 — 71, ≥ 7 — 34 (в основном dev.to и Хабр).
  В пуле за 7 дней сейчас 155 статей ≥ 5, из них 54 ≥ 7 и 18 ≥ 8.
- **Замер разбора (sol):** короткий $0.010–0.011, полный $0.024–0.027 (статьи 127 и 114). Текст выпуска ~$0.016.
  Классификация ~$0.0003 на статью (~$0.045/день). Выпуск с 7 полными разборами ~$0.24/день (~$7/мес); 3 части
  с полными разборами ~$0.62/день (~$19/мес).
- **Решение владельца 29.09:** части не делаем; перенос — 7 дней. Спека v1.16.
- **Перенос:** `analyzer/analyzer.py::run_category` — при `carryover_days > 0` и `select: quotas` пул за N дней
  вместо окна, сортировка по оценке, лимит 500, без 24-часового ограничения. `carryover_days`: 0 в `DEFAULT_CONFIG`,
  7 в `settings.json`. Тест `tests/test_digest_carryover.py` (без переноса — только свежая статья; с переносом —
  статья до прошлого выпуска и 6.5-дневная входят, 8-дневная и бывшая в выпуске — нет).
- **luna на полных разборах (образцы `data/knowledge-samples/*-luna.md`):** 796–834 слова, $0.0012–0.0015 за
  разбор (в ~20 раз дешевле sol); факты и цифры совпадают с sol, местами полнее. Нашлось: luna иногда отдаёт теги
  кириллицей → весь разбор отбраковывался. Для `format: full` теги теперь чинятся (латинские оставляются,
  недостающие — из темы и `ai`); короткий формат по-прежнему отбраковывает (старый тест). Тест
  `test_full_format_repairs_non_latin_tags`. Модель разборов не переключена — ждёт решения владельца.
- pytest 266 passed, 12 skipped · mypy `Success: no issues found in 70 source files`. Новых зависимостей нет.

### 29–30.09: выпуск 29.09 пропущен сном, разборы на luna

- **Выпуск 29.09 не вышел:** мак проснулся в 17:03 МСК, через 7 ч 53 мин после слота; grace был 6 ч →
  APScheduler: «was missed by 7:53:07», задание пропущено. Ошибка оценки исполнителя (мак спит дольше, чем
  закладывалось). `DIGEST_MISFIRE_GRACE_SECONDS` 6 ч → 20 ч (до следующего суточного слота); тест теперь
  воспроизводит опоздание 7 ч 53 мин. Ночные `ERROR` бота — сеть пропадает во сне (polling), не влияют.
  На сервере вопрос снимается.
- **Разборы на luna (решение 30.09):** `config/providers.json` → `openai.chat_completions.models.knowledge` =
  `gpt-6-luna` (было sol). По 3 образцам факты и цифры совпадают с sol, объём чуть больше, ~$0.0013 вместо ~$0.025.
  Текст выпуска остаётся на sol (~$0.016, приоритет владельца по качеству там ниже, но цена мала). Смотреть на
  живых разборах первую неделю; откат — одна строка.
- **Лимит входа разбора 24000 → 40000 символов** (30.09, по находке luna: статья 127 обрывалась на «Как выбрать»).
  Это обрезка текста кодом до вызова модели, а не инструкция модели. Среди статей с оценкой ≥ 6 длиннее 24000 —
  8, длиннее 40000 — 1 (максимум 42254). Только `settings.json`, читается на каждом выпуске.

### Разбор по всей статье, без комментариев (30.09, решение владельца)

- Владелец: резать статью по символам нельзя, в разбор должна попадать вся статья, а выкидываться — только то, что
  не статья. `trafilatura` уже убирает меню/шапку/рекламу, но 2.2 по умолчанию **включает комментарии**.
- `collectors/fulltext_fetcher.py`: `include_comments` → `trafilatura.extract`; ключ
  `sources.fulltext.include_comments` (`true` в `DEFAULT_CONFIG`, `false` в `settings.json`; оставлен — владелец хочет
  позже разбирать комментарии). Уже собранные статьи не перекачиваются. Тест `test_comments_switch_reaches_trafilatura`.
- `knowledge_publisher.py`: при `format: full` и `split_over_chars` > 0 статья уходит целиком; длиннее порога
  (150000 в `settings.json`, сейчас таких нет — максимум чистой ~55000) — `split_parts` по абзацам без потерь,
  `KNOWLEDGE_CHUNK_PROMPT` сжимает каждую часть в заметки, разбор по заметкам. Тесты: целиком без обрезки,
  через части (конец статьи в заметках и в итоговом промпте), `split_parts` без потерь.
- Живая проверка на luna, статья 122 (46000 символов): целиком — 1 вызов, $0.0019, 718 слов; через части (порог
  занижен до 20000) — 6 вызовов, $0.0044, 787 слов. В обоих разборах есть настройки из конца статьи
  (EMA, clipping 1, warmup 5000 — после 38000-го символа), которые лимит 24000 отрезал бы.
- Побочное: формулы Хабра (картинки) `trafilatura` выбрасывает — в долги. Старые записи 8–11.09 содержат сырой HTML
  (до чистки И3), в пул 7 дней не попадают.
- pytest 269 passed, 12 skipped · коллектор 66 passed · mypy `Success: no issues found in 70 source files`.
  Новых зависимостей нет. Спека v1.17.

### Ручной запуск, битый JSON разбора, нарезка выпуска (29–30.09, Claude Code)

**Что сделано**
- **Как устроен запуск по таймеру** (по вопросу владельца): контейнер `bot`, `job_queue` (APScheduler) раз в минуту
  перечитывает `/settings` и ставит `run_daily` на каждый слот `digests[].at` → `scheduled_digest_job` →
  `perform_scheduled_digest(app, name)` → `POST /digest/generate?name=…` → рассылка `TELEGRAM_ALLOWED_USERS`,
  статистика — `TELEGRAM_ADMIN_USERS`. Выпуск `articles` запущен этой функцией вручную в контейнере бота — 200, дошёл.
  Статистика при ручном запуске не пришла — ошибка исполнителя (в скрипте не заполнен `ADMIN_USERS`; в `main()` он
  заполняется, расписание не затронуто), дослана отдельно.
- **Битый JSON разбора (message 284):** gpt-6-luna сломала синтаксис JSON на ~3600-м символе пересказа, md не
  создался; повтор того же вызова дал валидный JSON — сбой случайный. `analyzer/llm_client.py`: `LLMJSONError`
  (подкласс `ValueError`) и лог текста вокруг места поломки; `analyzer/knowledge_publisher.py`: `_complete_json` —
  один повтор при битом JSON (разбор и нарезка частей). Тесты в `tests/test_knowledge_publisher_full.py`.
  Разбор 284 опубликован (`knowledge/2026/09/2026-09-28-nemotron-uskoril-diarizatsiyu-zvonkov-v-sto-raz-353.md`).
  Грабля 42.
- **Выпуск пришёл двумя сообщениями:** `split_message` мерил длину с тегами и URL (5478), Telegram считает видимый
  текст (3821 в UTF-16) при лимите 4096. `bot/digest_schedule.py`: `telegram_len`, `split_message(..., html=)`;
  `bot/telegram_bot.py` передаёт `html=parse_mode == "HTML"` в обоих местах отправки. Markdown — как раньше.
  Тесты в `tests/test_bot_split.py`. Выпуск 29.09 теперь режется в 1 сообщение. Грабля 43.

**Коммиты**
- `db35892` fix(tz4-i4.1): retry knowledge md once on invalid LLM JSON
- `a11692a` docs(tz4-i4.1): add strict JSON mode debt to STATE
- `e860e73` docs(tz4-i4.1): strict JSON mode debt is per-task opt-in
- `031a883` fix(tz4-i4.1): split HTML digests by visible length like Telegram

**Новые зависимости** — нет.

**Расхождения со спекой** — нет.

**Что НЕ сделано / отложено**
- Строгий JSON-режим провайдера (`response_format`) — в долгах `STATE.md`; по решению владельца выборочно по задачам.
- Аварийная повторная отправка простым текстом (если Telegram не принял HTML) шлёт теги как текст — на пограничном
  выпуске может упереться в лимит. Редкий путь, не трогался.
- Запуск по крону вместо постоянных контейнеров — обсуждён, не делается: контейнеры в простое ~255 МБ и ~0% CPU,
  основная память — VM Docker Desktop; постоянно нужны бот (команды), алерты и подписки анализатора.

**Побочные находки**
- Код бота запечён в образ (томов `./bot` нет, в отличие от analyzer/API): после правок в `bot/` —
  `docker compose up -d --build --force-recreate --no-deps bot`, простой `restart` не подхватывает.
- `ModelSpec.supports_json_schema` в `llm_core/providers.py` объявлен, но не используется.

**Приёмка:** pytest 274 passed, 12 skipped · mypy `Success: no issues found in 70 source files`.
Владелец принял И4.1 01.10.2026.

**Команды приёмки**
```
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
```

### После приёмки: порог 7, без «Новости дня», очередь по порядку, список кандидатов (01.10, Codex + Claude Code)

Решения владельца 01.10: порог выпуска `articles` 7 (`c402135`); квота `hype` 0 — «Новость дня» не нужна, место
занимает лучшая статья практики/инструментов/мнений; при равной оценке — более старые (компромисс: оценка важнее
возраста); список всех кандидатов выпуска для проверки выбора ИИ — ссылкой последней строкой выпуска, ничего не
помечает. Цифры на 01.10: статей ≥ 7 приходит ~30 в день, уходит 7, в очереди 87 — порядок решает, какие пропадут,
разгрести очередь он не может; рычаги — `max_items` или порог 8.

- Код — Codex по брифу `~/.codex-bridge/briefs/news-radar/tz4-i4.1-candidates.md`, диф проверен: `value_funnel.py`
  (`rank_candidates`, `explain_selection`, `tie_break` в `select_with_quotas`; дефолтный порядок не изменился),
  `analyzer.py` (SQL-порядок при `oldest`, пул в `artifacts`), `pipeline/extras.py` (экстра `candidates`),
  `knowledge_publisher.py` (`extra_files`/`delivered` — тем же коммитом), `renderer.py` (только ветка `ai_value`),
  `writers.py`, конфиг в обоих местах, `tests/test_digest_candidates.py` (11 тестов), `mypy.ini`.
- Правки оркестратора: типизация в тесте (mypy `--strict`), оценка в таблице без `.0`.
- Сухой прогон на живой базе 01.10 (без LLM и публикации): «выбрано 7 из 87», 197 строк (7 в выпуске, 80 не
  вместилось, 110 ниже порога).
- pytest 285 passed, 12 skipped · mypy `Success: no issues found in 71 source files`. Новых зависимостей нет.
  Расхождений со спекой нет (р.3.3 порядок внутри пула не задаёт).

**Не сделано / побочные находки**
- Заголовок в списке — первая строка текста: у dev.to это первый абзац (поля заголовка в `messages` нет).
- У части RSS-статей резюме не на русском (статья про нарезку видео Kling — на тайском); в RSS просочилась
  реклама-подборка «Recommended Tools — Binance, Ledger…» (`is_ad` не выставлен). В долги.
- Живой выпуск со ссылкой — завтра 09:10; проверить, что файл в GitHub открывается.

### Строгий JSON по задачам (01.10, Codex + Claude Code)

В выпуске 01.10 — 6 разборов из 7: статья 473 сломала JSON 3 раза подряд в одном месте (`how` строкой, модель
закрывала абзацы `"]`), повтор не помог. Решение владельца: не заплатка в промпте, а строгий режим для данных,
которые должны прийти JSON; где не нужно — не включать.

- В `llm_core` уже был механизм схемы-инструмента (классификатор им пользовался), не было только `strict`.
  Codex по брифу `~/.codex-bridge/briefs/news-radar/tz4-strict-json.md`: `JsonSchemaTool.strict`,
  `strict_schema()` (закрытые объекты, все поля обязательные), `ProviderProfile.strict_tools`
  (`providers.json`: только `openai.chat_completions`), `strict` уходит лишь при поддержке провайдером;
  `LLMClient.complete_json(schema=)` + `strict_json_tasks` (из `llm_strict_json_tasks`, на каждом цикле/выпуске);
  схемы в `analyzer/json_schemas.py` — разбор полный/краткий, заметки по частям, выпуск `ai_value`;
  классификатор со `strict` (без `minimum`/`maximum`, диапазон проверяет `_score`). `spoiler`, classic,
  крипто-анализ, алерты, тренды — без изменений. Тесты — `tests/test_strict_json.py`.
- Правки оркестратора: классификатор без `strict=` в вызове по умолчанию (старые тесты проверяют точные
  аргументы — их не меняли), имя переменной в `llm_core/client.py`, типы в тесте.
- Живая проверка на OpenAI: 473 — разбор с первого вызова через строгий инструмент ($0.0017); классификатор на
  3 статьях — путь `tool`, ошибок нет; выпуск — схема принята (sol, $0.0012).
- pytest 296 passed, 12 skipped · mypy `Success: no issues found in 73 source files`. Новых зависимостей нет.
- Повтор при битом JSON в разборе оставлен как страховка для провайдеров без `strict`.

### Очередь выпуска в статистике (04.10, Claude Code)

Владелец: в статистике не видно, сколько статей ждёт выпуска в 7-дневной очереди. «в очереди» означало статьи,
ещё не оценённые классификатором — переименовано в «ждут оценки». Новая строка (`analyzer/digest_stats.py`,
поля `queue`, `queue_expiring`, `queue_expired`; тест `test_carryover_queue_line`):
`🗂 Очередь выпуска: 259 (8: 78 · 7: 181) · выпадет за сутки: 4 · выпало без выпуска: 2` (живой выпуск 04.10).
Восьмёрок в очереди на 11 выпусков — классификатор, похоже, щедр на 8 (поток кейсов dev.to); смотреть по списку
кандидатов. pytest 297 passed · mypy без ошибок. Новых зависимостей нет. `docs/08_api.md` обновлён.
