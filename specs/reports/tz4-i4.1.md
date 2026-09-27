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
