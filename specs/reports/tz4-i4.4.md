# ТЗ #4 — И4.4: сайт — главная площадка (разборы в базе, алерты админу, канал)

Исходные решения владельца 08.10 — `specs/STATE.md`, раздел «Решения владельца 08.10 → И4.4».

## План (принят владельцем 08.10)

| Шаг | Что | Бриф | Статус |
|---|---|---|---|
| 1 | Разборы и пост выпуска в базе (`site_files`), статус сайта у выпуска, `knowledge.targets: []` | `specs/briefs/tz4-i4.4-step1-store.md` | ✅ `50082ad` |
| 2 | Каналы (`channels` в конфиге), алерты админу, `/digest <name> site`, отметки доставки | `specs/briefs/tz4-i4.4-step2-channel.md` | ✅ принят 08.10 |
| 3 | `scripts/migrate_knowledge_to_site.py`: старые разборы из `knowledge/` → `site_files` + один коммит, без LLM | — (сделал Claude Code, ~150 строк) | ✅ код 08.10; запуск на сервере — владелец |

### Миграции базы

- Таблица `site_files` (id, digest_id, message_id, kind `review`/`digest`, path UNIQUE, url, content, created_at,
  committed_at, commit_sha) — готовые файлы сайта вместе с выпуском; `committed_at IS NULL` — ещё не на сайте.
- Таблица `digest_deliveries` (digest_id, chat_id, sent_at; PK digest_id+chat_id) — куда ушёл выпуск
  (отдельной таблицей: каналов будет много, позже — пользователи).
- `digests.site_url`, `digests.site_status` (`ok` / `commit_failed` / `skipped`; NULL — выпуск без сайта).

### Решения владельца 08.10 (ответы на вопросы плана)

1. `/digest new` в канал **не** публикует — отвечает только спросившему. В канал публикуют расписание и
   `/digest <name> site`. Сорвался выпуск по расписанию → `/digest new articles`, затем `/digest articles site`.
2. Перед шагом 3 на сервере — `git pull`, чтобы папка `knowledge/` была полной; отсутствующие файлы скрипт
   пропускает и перечисляет.
3. При `commit_failed` админу — только алерт, без анонса (страницы нет).
4. Алерты бота — на английском, как прочие служебные сообщения бота.

**Новые зависимости:** не ожидаются.

**Расхождение со спекой:** р.5 (разборы в GitHub news-radar) и рассылка в личку — теперь сайт и канал; правит владелец.

**Выкатка:** шаги 1 и 2 — только вместе (в `main` после шага 2): один шаг 1 при сбое коммита отправил бы анонс
с мёртвой ссылкой.

## Шаг 1 — реализация Codex (08.10.2026)

**Статус:** код готов, ожидает приёмки оркестратором в Docker. Шаг 2 не начат;
выкатка шагов 1 и 2 — только вместе, как принято в плане.

**Что сделано:**

- `database/schema.py`: `site_files`, индексы по выпуску/статье, `digest_deliveries`, именованные
  миграции `digests.site_url` и `digests.site_status`. Новые таблицы включены в `SCHEMA`,
  до создания `digests`, как требует бриф.
- `analyzer/site_store.py`: типизированные файлы/разборы; upsert по пути, сброс отметок коммита
  при изменении контента, поиск последнего разбора статьи, фиксация коммита, привязка к выпуску.
  Функции принимают `sqlite3.Connection`; транзакциями управляет вызывающий код.
- `analyzer/knowledge_publisher.py`: булевый контракт `commit_files` сохранён;
  `last_commit_sha` устанавливается после успешного обновления ветки, сбрасывается перед каждым вызовом.
  Пустой список — успех без sha. Разборы сохраняются сразу после генерации/валидации;
  сохранённые разборы переиспользуются без LLM, неопубликованные возвращаются в коммит сайта.
- `analyzer/pipeline/extras.py`: пост и разборы записываются до сетевого коммита, URL собранного
  поста остаётся при ошибке. Артефакты `site_status` (`ok` / `commit_failed` / `skipped`),
  `site_file_ids`; после успеха сохраняется sha и обновляются прежние `analysis.md_path`.
  Исключения публикации не прерывают выпуск.
- `analyzer/analyzer.py`, `analyzer/pipeline/categories.py`: статус в `DigestPart`, сохранение
  URL/статуса и привязка файлов после INSERT выпуска в той же транзакции;
  URL части по-прежнему доступен только при `site.live: true`.
- `api/main.py`, `api/models.py`: optional-поля сайта; `/digest/generate` сохраняет live-поведение URL,
  `/digest/latest` возвращает сохранённые URL/статус.
- `config/settings.json`: только `knowledge.targets: []`; `DEFAULT_CONFIG` не менялся.
- `tests/test_site_store.py`: свежая/повторно инициализированная база, upsert, привязка, сохранение
  проваленного выпуска через настоящий `run_digest`, повтор без генерации разбора, исключение коммита,
  успешный sha, опубликованный разбор только ссылкой, отсутствие токена и выключенный сайт.
  `mypy.ini`: новый модуль и тест включены в `files=`, оба strict.
- `docs/06_digest.md`, `docs/09_config_hot_reload.md`: хранение, статусы, переиспользование, targets.
  `docs/11_problems_learned.md`: конфликт прежнего тестового контракта (п.50).
  `specs/STATE.md`: следующий шаг — Docker-приёмка, затем шаг 2.
- `analyzer/pipeline/writers.py` не потребовал правок: существующая ветка уже собирает анонс
  по наличию `site_digest_url`; теперь URL доступен и при провале коммита.

**Коммиты:** нет. Записей в `.git` не было; commit выполняет оркестратор после приёмки.
Список файлов и рекомендуемая команда:

```bash
git add database/schema.py analyzer/site_store.py analyzer/knowledge_publisher.py analyzer/pipeline/extras.py analyzer/pipeline/categories.py analyzer/analyzer.py api/main.py api/models.py config/settings.json tests/test_site_store.py mypy.ini docs/06_digest.md docs/09_config_hot_reload.md docs/11_problems_learned.md specs/STATE.md specs/reports/tz4-i4.4.md
git commit -m "feat(tz4-i4.4): persist site files and publication status"
```

**Новые зависимости:** нет.

**Расхождения со спекой:** принятые решения И4.4 заменяют GitHub news-radar сайтом/базой
(р.5), далее — канал вместо личной рассылки. Спеку не менял, её правит владелец.
Бриф называет `analyzer/analyzer.py` baselined, но текущий `mypy.ini` не содержит такой baseline
(STATE отмечает снятие в И3). Baseline не расширял; изменения минимальные.

**Что НЕ сделано / отложено:** pytest, mypy, миграции и импорт-смоук не запускались:
AGENTS.md запрещает Codex выполнять их вне Docker, Docker недоступен.
Свежая база/идемпотентность покрыты новым тестом, фактический прогон — оркестратору.
Каналы, алерты, команда восстановления и записи доставки — шаг 2; миграция старых файлов — шаг 3.
Сетевых публикаций не выполнял. Бот, `renderer.py`, classic/spoiler и промпты не менял.
`.env` не читал, существующие тесты не менял.

**Побочные находки:** старый
`tests/test_site_publish.py::test_one_site_commit_and_telegram_fallback` при `ok=False`
(два параметризованных случая) ожидает отсутствие `site_digest_url`. Это прямо противоречит
новому брифу (URL всегда после сборки поста). Существующий тест оставлен без изменений;
полный pytest ожидаемо потребует согласованного владельцем обновления этого ожидания.
`ok` означает успешный GitHub-коммит, не завершённый деплой сайта.
При upsert/новом выпуске `site_files.digest_id` у того же пути указывает на последний привязанный
выпуск: исторические версии файлов схема брифа не сохраняет.

**Статическая проверка:** AST всех 9 изменённых/новых Python-файлов валиден,
JSON настроек валиден, `knowledge.targets` пуст; `git diff --check` чистый.
Это не заменяет Docker-приёмку.

**Команды приёмки (оркестратор):**

```bash
docker compose run --rm --no-deps analyzer python -m pytest -q tests/test_site_store.py tests/test_site_end_to_end.py tests/test_knowledge_batch.py
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
docker compose run --rm --no-deps analyzer python -m mypy --strict analyzer/site_store.py tests/test_site_store.py
docker compose run --rm --no-deps news-radar-api python -c "import api.main; print('ok')"
```

Полный pytest запускать с учётом описанного конфликта старого теста; не ослаблять остальные проверки.

## Шаг 1 — приёмка Claude Code (08.10)

- mypy без ошибок (85 файлов). Диф проверен: разбор пишется в `site_files` сразу после генерации,
  незакоммиченный разбор уходит следующим коммитом без LLM, статус/URL выпуска — в транзакции записи выпуска.
- **Плавающий тест Codex** (`test_failed_commit_reuses_review…`, 2 падения из 12 прогонов): повторные
  `run_digest` без `hours` берут окно «с конца прошлого выпуска», статья собрана в ту же секунду — при
  разной точности времени (`… HH:MM:SS` против `isoformat` с микросекундами) статья иногда выпадает из окна.
  Исправлено в тесте: явное окно `hours=24`; после правки 25 из 25 зелёные.
- pytest: 359 passed / 12 skipped, красные только 2 случая старого
  `test_site_publish.py::test_one_site_commit_and_telegram_fallback` (`ok=False`, строка 93 ждёт отсутствия
  `site_digest_url`) — прежний контракт, отменённый решением владельца №2. **Владелец 08.10: обновить ожидание** —
  строка 93 теперь проверяет URL и `site_status = commit_failed`, остальные проверки без изменений.
- Итог: pytest 361 passed / 12 skipped, mypy чистый.

## Шаг 2 — реализация Codex (08.10.2026)

**Статус:** реализован, ожидает проверки дифа и приёмки оркестратором в Docker.
Выкатка шагов 1 и 2 — вместе; шаг 3 не начат.

**Что сделано:**

- `config/config_watcher.py`, `config/settings.json`, `api/main.py`: `channels` в обоих
  конфигах и схеме PATCH; дефолт пустой, канал `-1003973615006` включён для `articles`.
- `analyzer/site_store.py`: чтение доставок, частей последнего именованного run;
  republish всех сохранённых файлов выбранного run одним batch-коммитом независимо от
  `committed_at`, без LLM. Успех отмечает файлы/sha и статус всех частей; отказ или
  исключение — `commit_failed`. Соединение после запроса закрывает API.
- `api/main.py`, `api/models.py`: идемпотентная отметка доставки с 404 для неизвестного
  выпуска, `delivered` в generate/latest, republish с 400/404 и сохранённым URL даже
  при `live: false`. Latest дополнительно возвращает части именованного run, чтобы
  восстановление открытой страницы доставляло все части, а не только последнюю строку.
- `bot/site_wait.py`: результат ожидания для каждой части; без URL — True;
  timeout=0 выполняет одну HTTP-проверку. Прежние вызовы, игнорирующие результат,
  сохраняются, существующие тесты не изменены.
- `bot/telegram_bot.py`: безопасный выбор каналов с WARNING на неверные записи,
  plain-text алерты ADMIN_USERS, отправка фрагментов с прежним fallback без разметки,
  отметка после полной отправки части и пропуск уже доставленных каналов.
  Расписание с каналом не отправляет выпуск ALLOWED_USERS; неудачный коммит/деплой
  блокирует соответствующую часть и вызывает английский алерт. Без канала прежняя
  ветка рассылки сохранена. Статистика остаётся у админов. `_notify_users` не изменён.
- `/digest <name> site`: admin-only, обязательное имя, `site` — ключевое слово;
  проверка страницы, при необходимости republish с timeout=60 и ожидание деплоя,
  ответ спросившему и доставка в недоставленные каналы. Добавлены строки start/help.
  `/digest new` не публикует в каналы; генерация и renderer не изменены.
- `tests/test_channel_delivery.py`: расписание, commit_failed, таймаут страницы,
  прежняя личная рассылка, статистика админам, восстановление/повтор, republish,
  разбор аргументов и некорректный конфиг. Telegram-моки, skipUnless telegram.
- `tests/test_site_republish.py`: HTTP-контракты, все файлы двух частей включая
  уже committed одним коммитом, статусы/отметки, live=false URL, 400/404,
  идемпотентность delivered и выдача latest. Новые тесты и site_wait — strict в mypy.ini.
- `docs/06_digest.md`, `docs/09_config_hot_reload.md`: каналы, алерты, восстановление,
  hot reload; `specs/STATE.md`: следующий шаг — приёмка шага 2.

**Коммиты:** нет; `.git` не изменял. Оркестратору после приёмки:

```bash
git add analyzer/site_store.py api/main.py api/models.py bot/site_wait.py bot/telegram_bot.py config/config_watcher.py config/settings.json mypy.ini tests/test_channel_delivery.py tests/test_site_republish.py docs/06_digest.md docs/09_config_hot_reload.md specs/STATE.md specs/reports/tz4-i4.4.md
git commit -m "feat(tz4-i4.4): deliver digests to channels and recover site posts"
```

**Новые зависимости:** нет.

**Расхождения со спекой:** ранее принятые решения И4.4 (сайт/база и каналы вместо
GitHub news-radar/личной рассылки); новых расхождений нет, спеку не менял.

**Что НЕ сделано / отложено:** pytest, mypy, импорт-смоук и живые отправки/коммиты
не запускались — Docker недоступен Codex по AGENTS.md. Статически проверены AST
всех изменённых Python-файлов, JSON settings и `git diff --check` — успешно.
Не отмечаю Docker-приёмку пройденной. Миграция старых разборов — шаг 3.

**Побочные находки:** доставка Telegram и запись delivered не являются одной
транзакцией: если отправка успешна, но запись отметки не прошла, ошибка логируется,
повтор восстановления может отправить часть снова. Конкурентные запуски также
не защищены общей блокировкой; бриф предусматривает идемпотентность записи в БД,
а не гарантию exactly-once Telegram. Новых граблей среды не обнаружено.

**Команды приёмки** (исправлены Claude Code: в образе анализатора нет `fastapi`, в образе API нет pytest —
API-тесты на unittest гоняются в образе API, тест ожидания страницы — pytest в анализаторе с папкой бота):

```bash
docker compose run --rm --no-deps -v "$PWD/bot:/app/bot" analyzer python -m pytest -q --ignore=tests/collector
docker compose run --rm --no-deps -v "$PWD/bot:/app/bot" analyzer python -m mypy
docker compose run --rm --no-deps -v "$PWD/api:/app/api:ro" -v "$PWD/tests:/app/tests:ro" -v "$PWD/analyzer:/app/analyzer:ro" -v "$PWD/database:/app/database:ro" -v "$PWD/config:/app/config:ro" news-radar-api python -m unittest tests.test_site_republish tests.test_api_named tests.test_api_digest_errors tests.test_api_digest_stats
docker compose run --rm --no-deps -v "$PWD/bot:/app/bot:ro" -v "$PWD/tests:/app/tests:ro" bot python -m unittest tests.test_channel_delivery tests.test_bot_named tests.test_bot_digest_stats tests.test_bot_misfire tests.test_bot_split
```

## Шаг 2 — приёмка Claude Code (08.10)

- Тест republish Codex написал на pytest с импортом `api.main`: в образе анализатора он падал (`fastapi` нет),
  в образе API не запускался (pytest нет) — то есть не проверялся нигде. Переписан на unittest в стиле
  `tests/test_api_named.py` (пропуск без `api`); проверка ожидания страницы вынесена в
  `tests/test_site_wait_parts.py` (pytest, анализатор с папкой бота). Ошибка mypy (`site_wait.httpx` не
  экспортирован) — патч по строковому пути.
- Диф бота и API прочитан: в канал — только при `site_status = ok` и открывшейся странице; оба алерта на
  английском с подсказкой `/digest <name> site`; выпуски без канала — прежняя личка; повтор `site` не дублирует
  канал (`delivered`); republish шлёт из базы все файлы последнего прогона одним коммитом, без LLM.
- Итог: анализатор 362 passed / 17 skipped, API 4 OK, бот 13 OK, mypy чистый (88 файлов).
- Принятый риск (находка Codex): отправка в канал и отметка `delivered` — не одна транзакция; если отметка не
  записалась, повторный `site` отправит выпуск в канал ещё раз. Ошибка логируется.

## Шаг 3 — перенос старых разборов (Claude Code, 08.10)

Код написал Claude Code без Codex (небольшой скрипт, владелец хотел успеть сегодня).

**Что сделано:**
- `scripts/migrate_knowledge_to_site.py`: источник — `analysis.md_path` в `knowledge/` (кроме `candidates/`),
  файл — из `--root`; старый md → фронтматтер сайта без `digest`, `pubDatetime` = дата разбора 06:10 UTC;
  описание — «Идея», иначе «Коротко», иначе первый абзац. `--dry-run` / `--commit`; один коммит; `md_path` на
  путь сайта только после успеха; повторный запуск досылает незакоммиченное и пропускает закоммиченное. LLM нет.
- `analyzer/knowledge_publisher.py`: `build_site_review(doc, digest_slug | None, published=None)` — без `digest`
  поле не пишется; `validate_site_frontmatter(content, require_digest=True)`. Поведение для новых разборов прежнее.
- `tests/test_migrate_knowledge.py`: dry-run ничего не пишет; краткий и полный формат; без `digest`; дата;
  экранирование HTML; сбой коммита → `md_path` прежний; повтор — без коммита. `mypy.ini` — оба файла strict.
- `docs/06_digest.md` — раздел «Перенос старых разборов» с командами для сервера.

**Проверки:**
- pytest 364 passed / 17 skipped, mypy чистый (90 файлов).
- `--dry-run` на копии локальной базы с папкой `knowledge/` из `origin/main`: 56 к переносу, 0 пропусков
  (первый прогон нашёл разбор 360 полного формата без абзаца до «## Коротко» — исправлено).
- Все 63 файла `knowledge/` из `main` сконвертированы и собраны во временном клоне блога (`main` блога
  `567225f`): `astro check` 0 ошибок / 0 предупреждений, 63 страницы `/reviews/<slug>/`.
  Грабля: локальная ветка `main` блога была устаревшей (без коллекции `reviews`) — Astro молча игнорирует
  чужие md; собирать только от свежего `origin/main` блога.

**Новые зависимости:** нет. **Расхождения со спекой:** нет новых.

**Запуск на сервере (владелец, после деплоя `main`):** команды — `docs/06_digest.md`, раздел «Перенос старых
разборов». Сначала `--dry-run`, затем `--commit`. `git pull` на сервере не нужен: папка `knowledge/` приходит
`git archive` из `main` при деплое и содержит все разборы, закоммиченные ботом до этого.
