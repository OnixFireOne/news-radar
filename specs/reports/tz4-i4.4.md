# ТЗ #4 — И4.4: сайт — главная площадка (разборы в базе, алерты админу, канал)

Исходные решения владельца 08.10 — `specs/STATE.md`, раздел «Решения владельца 08.10 → И4.4».

## План (принят владельцем 08.10)

| Шаг | Что | Бриф | Статус |
|---|---|---|---|
| 1 | Разборы и пост выпуска в базе (`site_files`), статус сайта у выпуска, `knowledge.targets: []` | `specs/briefs/tz4-i4.4-step1-store.md` | 🔧 у Codex |
| 2 | Каналы (`channels` в конфиге), алерты админу, `/digest <name> site`, отметки доставки | — | — |
| 3 | `scripts/migrate_knowledge_to_site.py`: старые разборы из `knowledge/` → `site_files` + один коммит, без LLM | — | — |

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
