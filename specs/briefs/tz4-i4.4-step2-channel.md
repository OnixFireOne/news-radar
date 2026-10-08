# Бриф Codex: И4.4, шаг 2 — канал, алерты админу, `/digest <name> site`

Репозиторий: `~/Documents/Ai/apps/news-radar`. Сначала `AGENTS.md` → `CLAUDE.md` (жёсткие правила), затем
`specs/reports/tz4-i4.4.md` (план, решения владельца, шаг 1 — что уже сделано) и `docs/06_digest.md`
(«Публикация на сайт»). Шаг 1 закоммичен (`50082ad`): у выпуска в `digests` есть `site_url` / `site_status`,
файлы сайта — в `site_files`, таблица `digest_deliveries` создана, но пока никем не пишется.

## Цель

1. Читатели получают выпуск **только в канале**. Выпуск по расписанию с привязанным каналом уходит анонсом
   в канал(ы), а не в личку `ALLOWED_USERS`. Выпуски без канала (крипта и любые другие) — как сейчас.
2. Сбой публикации на сайт → **алерт админу** (`ADMIN_USERS`) в личку, читателям ничего. Два случая:
   коммит не прошёл (`site_status != "ok"`); коммит прошёл, но страница не открылась за `site.wait_for_page_sec`.
3. Команда **`/digest <name> site`**: проверить страницу последнего выпуска; не открывается — перекоммитить
   его файлы из базы, дождаться деплоя; затем прислать анонс спросившему и опубликовать в каналы, куда выпуск
   ещё не уходил. Новой генерации нет.

## Решения владельца (не пересматривать)

- `/digest new` в канал **не** публикует — отвечает только спросившему (как сейчас). В канал публикуют только
  расписание и `/digest <name> site`.
- При `commit_failed` админу — только алерт, без анонса.
- Алерты и все новые сообщения бота — **на английском**, как прочие служебные сообщения бота.
- ID канала не секрет — в `settings.json`, не в `.env`.

## Что сделать

### 1. Конфиг — ключ `channels` (в ДВА места)

- `DEFAULT_CONFIG` (`config/config_watcher.py`): `"channels": []` — поведение прода без изменений.
- `config/settings.json`: `"channels": [{"chat_id": -1003973615006, "digests": ["articles"], "enabled": true}]`.
- Выпуск `name` идёт в каналы, где `enabled` и `name in digests`. Невалидные записи (нет `chat_id`-int,
  `digests` не список) — пропускать с WARNING, не падать. Если `PATCH /settings` валидирует ключи по схеме —
  добавь `channels` туда же, иначе ничего.

### 2. API (`api/main.py`)

- `POST /digest/{digest_id}/delivered?chat_id=<int>` — `INSERT OR IGNORE INTO digest_deliveries`;
  404, если выпуска нет. Идемпотентно.
- В ответы `/digest/generate` (каждая часть) и `/digest/latest` добавить `delivered: list[int]` — chat_id из
  `digest_deliveries` этого выпуска (`DigestResponse` — Optional-поле).
- `POST /digest/site/republish?name=<name>` — перекоммит последнего выпуска с этим именем из базы:
  - выпуск: последняя строка `digests` с этим `name` (как `/digest/latest`); все части того же `run_id`;
  - файлы: все `site_files` с `digest_id` этих частей, независимо от `committed_at` (страница не открылась —
    значит, надо отправить всё ещё раз; содержимое берётся из базы как есть, без LLM и без пересборки);
  - один коммит `GitHubPublisher(... batch=True).commit_files` (репо/ветка/токен — как в extra `site`,
    сообщение `feat(radar): republish AI radar digest <date>`), при успехе — `mark_committed(ids, sha)` и
    `digests.site_status = 'ok'`; при неудаче — `site_status = 'commit_failed'`;
  - ответ: та же форма, что у `/digest/latest` (+ `parts`, как у `/digest/generate`), плюс `site_status` и
    `site_url` — **`site_url` здесь отдаётся всегда, когда он записан**, не только при `live`;
  - 404 — нет выпуска или у него нет файлов сайта; 400 — сайт выключен или нет `NEURONAVT_GITHUB_TOKEN`.
  - Логика коммита из базы — функция в `analyzer/site_store.py` или новом модуле (без импортов из `bot/`),
    эндпоинт только вызывает её.

### 3. Бот (`bot/telegram_bot.py`, `bot/site_wait.py`)

- `wait_for_parts` возвращает результат по каждой части (например, `dict[digest_id, bool]` или список bool) —
  открылась ли страница. Части без `site_url` считаются «не требуют ожидания». Существующие вызовы и тесты
  `tests/test_site_announce.py` не ломать.
- Хелпер `channels_for(settings, name) -> list[int]` по правилам п.1.
- Хелпер отправки алерта админам `alert_admins(bot, text)` — plain text, в `ADMIN_USERS`; ошибка отправки —
  только лог. (`_notify_users` — существующие сообщения в `ALLOWED_USERS` — не трогать.)
- Хелпер публикации в канал: отправка частей (`split_message`, тот же fallback без разметки, что сейчас), затем
  `POST /digest/{id}/delivered?chat_id=`. Пропускать каналы, уже перечисленные в `delivered` части.
- **`perform_scheduled_digest`**: после ответа `/digest/generate`
  - если у выпуска (`name`) нет каналов → всё как сейчас (личка `ALLOWED_USERS`), без изменений;
  - иначе для каждой части с `site_status`:
    - `site_status != "ok"` → алерт: `⚠️ Site publish failed for digest '<name>' (<status>): readers got
      nothing. Retry: /digest <name> site`; в канал ничего;
    - `ok`, но страница не открылась за `wait_for_page_sec` → алерт `⚠️ Site page did not open in <N>s
      (blog deploy?): <url>. Readers got nothing. Retry: /digest <name> site`; в канал ничего;
    - иначе → публикация в каналы;
  - части без `site_status` (сайт выключен) с каналом → публикуются в каналы как есть;
  - статистика админу — как сейчас; в личку `ALLOWED_USERS` при наличии каналов анонс **не** шлётся.
  - Ответ API, где в части нет `site_url`, но есть `ok` — не бывает при `live`; при `live: false` страницу не
    ждать (как сейчас).
- **`/digest <name> site`**: в разборе аргументов `site` — ключевое слово (как `new`/`force`), не имя.
  `name` обязателен (без него — подсказка `Usage: /digest <name> site`). Только для `ADMIN_USERS`.
  1. `GET /digest/latest?name=` → нет выпуска или нет `site_url` → сообщение об ошибке.
  2. Один GET страницы (`wait_for_site_page(url, 0)` или отдельная функция «одна попытка»); 200 → шаг 4.
  3. Иначе `POST /digest/site/republish?name=` (httpx, таймаут ~60 с; `fetch_api` с его 10 с не годится) →
     сообщение `Republishing...`; не `ok` → алерт с причиной, конец; затем ожидание до `wait_for_page_sec` →
     не открылась → алерт, конец.
  4. Анонс спросившему (как ответ команды) + публикация в каналы выпуска, которых нет в `delivered`;
     итоговое сообщение: `Posted to N channel(s)` или `Already posted to all channels`.
- `/start` и `/help`: строка про `/digest <name> site`.

### 4. Тесты

- `tests/test_channel_delivery.py` — в стиле `tests/test_bot_named.py` (unittest, `skipUnless telegram`,
  моки Bot и `fetch_api`/httpx, без сети). Минимум:
  1. Выпуск по расписанию с каналом, `ok`, страница открылась → отправка только в канал, отметка `delivered`,
     в личку `ALLOWED_USERS` ничего, статистика админу.
  2. `commit_failed` → алерт админу (текст на английском, с `/digest <name> site`), в канал ничего.
  3. `ok`, страница не открылась → алерт, в канал ничего.
  4. Выпуск без канала → прежняя рассылка в личку.
  5. `/digest articles site`: страница открыта → без republish, анонс + канал; повтор → канал не дублируется
     (`delivered` уже содержит chat_id).
  6. `/digest articles site`: страница не открыта → republish → ожидание → публикация; republish не `ok` → алерт.
  7. Разбор аргументов: `site` не становится именем; без имени — подсказка; не админ — отказ.
- API-тесты (pytest, контейнер analyzer) — в `tests/test_site_republish.py`: republish коммитит все файлы
  выпуска из базы одним коммитом (мок `commit_files`), отмечает `committed_at`/`site_status`; 404/400;
  `delivered` идемпотентен и отдаётся в `/digest/latest`.
- Существующие тесты не менять. Новые Python-файлы — в `files=` в `mypy.ini` (strict).

## Документы

`docs/06_digest.md` — каналы, алерты, команда восстановления, порядок действий при сбое.
`docs/09_config_hot_reload.md` — ключ `channels`. Раздел «Шаг 2 — реализация Codex» в
`specs/reports/tz4-i4.4.md` по шаблону отчёта из `CLAUDE.md`.

## Ограничения

- `analyzer/renderer.py`, classic/spoiler и их промпты — не трогать. Генерацию выпуска не менять.
- `_notify_users` и существующие тексты бота не менять (кроме строк `/start`/`/help`).
- Новых зависимостей нет. Секретов в коде и `settings.json` нет (ID канала — не секрет).
- Docker тебе недоступен: pytest/mypy гоняет Claude Code. Статическая проверка (AST, `git diff --check`) и список
  того, что не запускалось, — в отчёте. В `.git` не пишешь; список файлов для коммита — в отчёте.
