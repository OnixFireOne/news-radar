# Бриф Codex: И4.4, шаг 1 — разборы и пост выпуска хранятся в базе

Репозиторий: `~/Documents/Ai/apps/news-radar`. Сначала `AGENTS.md` → `CLAUDE.md` (жёсткие правила), затем
`specs/reports/tz4-i4.4.md` (план и решения), `specs/reports/tz4-i4.3.md` (шаги 2–3 — как устроена публикация
на сайт сейчас) и `docs/06_digest.md` («Публикация на сайт»).

## Цель

Сейчас разборы и пост выпуска собираются в памяти и коммитятся в репозиторий блога (`neuronavt`); при сбое
коммита всё теряется, а следующий выпуск генерирует разбор заново (деньги на LLM). Нужно:

1. Хранить готовые файлы сайта (разборы + пост выпуска) в базе вместе с выпуском, с отметкой, закоммичены ли они.
2. Не генерировать разбор повторно, если он уже есть в базе (закоммичен или нет).
3. Записывать у выпуска URL страницы на сайте и статус публикации — по нему шаг 2 решит, слать ли читателям.
4. Отключить коммит разборов в GitHub news-radar (только `settings.json`).

Шаг 2 (каналы, алерты, команда `/digest <name> site`) — **не в этом брифе**, бот не трогать.

## Что сделать

### 1. Схема (`database/schema.py`)

По образцу `DIGEST_MESSAGES_SCHEMA` — новые таблицы через `CREATE TABLE IF NOT EXISTS` в `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS site_files (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_id    INTEGER REFERENCES digests(id),   -- NULL for migrated legacy reviews / before the digest row exists
    message_id   INTEGER REFERENCES messages(id),  -- NULL for the digest post
    kind         TEXT NOT NULL,                    -- 'review' | 'digest'
    path         TEXT NOT NULL UNIQUE,             -- path in the site repo
    url          TEXT,                             -- public page URL
    content      TEXT NOT NULL,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    committed_at DATETIME,                         -- NULL until a commit containing the file succeeded
    commit_sha   TEXT
);
CREATE TABLE IF NOT EXISTS digest_deliveries (
    digest_id INTEGER NOT NULL REFERENCES digests(id),
    chat_id   INTEGER NOT NULL,
    sent_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (digest_id, chat_id)
);
```

Индексы `site_files(digest_id)`, `site_files(message_id)`. В `MIGRATIONS` (в конец, именованные):
`ALTER TABLE digests ADD COLUMN site_url TEXT`, `ALTER TABLE digests ADD COLUMN site_status TEXT`.
`digest_deliveries` в этом шаге только создаётся (пишет шаг 2). Важно: `digests` в `SCHEMA` создаётся позже
`site_files` в тексте — SQLite это допускает (как у `digest_messages`), проверь на свежей базе.

### 2. Хранилище файлов сайта — новый модуль `analyzer/site_store.py`

Небольшой модуль без зависимостей от бота, функции на `sqlite3.Connection` или `db_path` (на твой выбор, но
единообразно), типизированный под `mypy --strict`. Минимум:

- `save_files(...)` — upsert по `path`: новая запись или замена `content`/`url`/`kind`/`message_id`, при замене
  содержимого `committed_at` и `commit_sha` сбрасываются в NULL. Возвращает id записей.
- `mark_committed(ids, sha)` — `committed_at = now`, `commit_sha`.
- `review_for_message(message_id)` — последняя запись `kind='review'` статьи или None (path, content, url, committed).
- `attach_digest(ids, digest_id, site_url, site_status)` — `site_files.digest_id` для записей выпуска +
  `digests.site_url/site_status`.

### 3. Коммит должен вернуть sha (`analyzer/knowledge_publisher.py`)

`GitHubPublisher.commit_files` сейчас возвращает `bool`. Нужен sha созданного коммита — не ломая вызовы и
существующие тесты (например, отдельный метод/атрибут `last_commit_sha`, который ставится при успехе; на
твой выбор, но существующие тесты не менять). Пустой список файлов — успех без sha.

### 4. Разборы: переиспользовать из базы (`publish_selected`)

Сейчас повторная генерация не происходит, только если у статьи есть `analysis.md_path`. Добавить: перед
генерацией при включённой публикации на сайт (`stage_site`) смотреть `review_for_message(row["id"])`:

- запись есть и закоммичена → как сейчас «reused»: слаг в `site_existing`, ссылка на страницу разбора;
- запись есть, но **не** закоммичена → «reused», без LLM; разбор кладётся в `site_reviews` (тот же `SiteReview`
  с путём и содержимым из базы), чтобы уйти этим коммитом сайта;
- записи нет → генерация как сейчас.

Новые разборы, прошедшие `validate_site_frontmatter`, пишутся в `site_files` (`kind='review'`, `digest_id` NULL,
url = `site_review_url(slug, site)`) **сразу после генерации, до коммита** — чтобы сбой коммита ничего не терял.

### 5. Extra `site` (`analyzer/pipeline/extras.py`)

- Пост выпуска (`build_digest_post`) пишется в `site_files` (`kind='digest'`) до коммита. Повторный выпуск
  в тот же день — тот же path, upsert заменяет содержимое.
- `ctx.artifacts["site_digest_url"]` ставится **всегда**, когда пост собран (URL детерминирован), не только при
  успешном коммите.
- Новый артефакт `ctx.artifacts["site_status"]`: `"ok"` — коммит прошёл; `"commit_failed"` — не прошёл или
  исключение после сборки поста; `"skipped"` — `site.enabled`, но нет `NEURONAVT_GITHUB_TOKEN`. При
  `site.enabled: false` — артефакта нет (сайта у выпуска нет).
- `ctx.artifacts["site_file_ids"]` — id всех записей выпуска (разборы этого коммита + пост).
- При успехе — `mark_committed(ids, sha)`; обновление `analysis.md_path` — как сейчас.
- Никакое исключение по-прежнему не должно ломать выпуск.

### 6. Привязка к выпуску (`analyzer/analyzer.py`, `analyzer/pipeline/categories.py`)

- `DigestPart` — новое поле `site_status: str | None = None`.
- В `run_category` после `INSERT INTO digests` — `attach_digest(site_file_ids, digest_id, site_url, site_status)`,
  если есть `site_status`. `_last_site_status` по образцу `_last_site_url`; `run_digest` передаёт его в `DigestPart`.
- `_last_site_url` — по-прежнему только при `site.live: true` (так бот решает, ждать ли страницу).
- `analyzer/analyzer.py` стоит в mypy baseline: правки минимальные, без рефакторинга.

### 7. Рендер (`analyzer/pipeline/writers.py`)

Анонс собирается, когда есть `site_digest_url` (теперь — и при сбое коммита). Если URL нет — прежний запасной
полный дайджест с WARNING (сайт выключен/пост не собрался). Решение, отправлять ли читателям, — за
`site_status` (шаг 2), не за рендером.

### 8. API (`api/main.py`)

`/digest/generate` и `/digest/latest`: в ответ каждой части — `site_url` и `site_status` из строки `digests`
(в `generate` `site_url` сейчас берётся из `DigestPart` — оставить это поведение для live, добавить
`site_status`). Если `DigestResponse` в `api/models.py` — поля добавить туда как Optional.

### 9. Конфиг

`config/settings.json`: `knowledge.targets: []`. `DEFAULT_CONFIG` не меняется (дефолт `["github"]`).
Проверь, что при `targets: []` и `site.enabled` разборы генерируются и попадают на сайт (сейчас это ветка
`stage_site`), а при `site.enabled: false` и `targets: []` — ничего не генерируется (как сейчас).

## Тесты — новый файл `tests/test_site_store.py` (offline, моки как в `tests/test_site_publish.py`)

1. Свежая база: таблицы и колонки созданы; повторный `init_db` идемпотентен.
2. Выпуск через `NewsAnalyzer.run_digest` (образец — `tests/test_site_end_to_end.py`), коммит **не прошёл**:
   в `site_files` пост + разборы с `committed_at IS NULL`, `digest_id` выпуска; `digests.site_status =
   'commit_failed'`, `site_url` заполнен; рендер — анонс.
3. Следующий выпуск с той же статьёй: LLM разбора **не вызывается**, незакоммиченный разбор входит в коммит;
   после успеха `committed_at` и `commit_sha` заполнены, статус `ok`.
4. Закоммиченный разбор в базе → reused, в коммит не попадает.
5. Нет токена → `skipped`; сайт выключен → `site_status` NULL, поведение как раньше.
6. Upsert поста в тот же день: одна строка на path, содержимое новое, `committed_at` сброшен до нового коммита.

Существующие тесты не менять. Каждый новый файл — в `files=` в `mypy.ini` (strict).

## Документы

`docs/06_digest.md` — раздел «Публикация на сайт»: хранение в базе, статусы, переиспользование разборов,
`targets: []`. `docs/09_config_hot_reload.md` — если описывает `knowledge.targets`. Допиши в
`specs/reports/tz4-i4.4.md` раздел «Шаг 1 — реализация Codex» по шаблону отчёта из `CLAUDE.md`.

## Ограничения

- `analyzer/renderer.py`, шаблоны classic/spoiler и их промпты — не трогать.
- Бот (`bot/`) не трогать — это шаг 2.
- Новых зависимостей нет.
- Docker тебе недоступен: pytest/mypy гоняет Claude Code. Сделай статическую проверку (AST, `git diff --check`)
  и перечисли в отчёте, что не запускалось.
- В `.git` не пишешь; список файлов для коммита — в отчёте.
