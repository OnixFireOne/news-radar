# ТЗ #4 — И4.1: категории и дайджесты (р.3.3) + площадки базы знаний (р.5)

**Статус:** в работе с 27.09. Шаги 1, 2a и 2b сделаны; ждёт приёмки владельца и живого прогона дайджеста `articles`.

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
