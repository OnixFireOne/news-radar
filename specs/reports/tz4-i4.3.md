# ТЗ #4 — И4.3: публикация на сайт «Нейронавт» (дайджест + разборы)

**Статус:** 🔧 в работе с 07.10.2026. Вне раздела 10 спеки; меняет р.5 (площадка разборов) и решение
владельца от 28.09 «Нейронавт пока не делаем». Правка спеки (v1.18) — за владельцем.

## Решения владельца 07.10

1. **Лента сайта** (главная, `/posts`, RSS, архив, теги) — посты владельца **и** дайджесты. Разборы ленту не засоряют.
2. **Разборы** — своя страница-список `/reviews` и поиск по сайту.
3. **Старые разборы** из `knowledge/` переносятся на сайт.
4. **Сайт — единственная площадка** для новых разборов. Старые файлы `knowledge/` в news-radar
   **остаются** (на них ведут ссылки уже отправленных выпусков), но больше не пополняются.
5. Оформляется **шагом И4.3**.
6. **Дайджест на сайте — свой шаблон**: красивее и удобнее, без ограничений Telegram.
   **Telegram — короткий анонс**: что вышел выпуск, сводка по рубрикам, что интересного, ссылка на сайт.
7. **Первый живой прогон** — в ветку `radar-preview` блога (без деплоя), после просмотра — на `main`.
8. Макеты дайджеста и анонса (07.10, в чате) — приняты.
9. Сайт: `https://neuronavt.blog/`. Репозиторий: `OnixFireOne/neuronavt`, блог в `blog/`
   (Astro 6, AstroPaper). Локальная копия: `~/Documents/Ai/apps/neuronavt`.

## Что есть в блоге (разведка 07.10)

- Одна коллекция `posts`; главная, `/posts`, архив, теги и RSS берут все посты через `getSortedPosts`.
  Отсюда решение: разборы — **отдельная коллекция `reviews`**, дайджесты — обычные посты в `posts/_digests/`
  (папка с `_` в адрес не попадает, см. `getPostPaths.ts`).
- Схема `posts`: `description` и `pubDatetime` обязательны — ошибка во фронтматтере ломает **всю** сборку.
  В проде пост с `pubDatetime` в будущем скрыт (`postFilter`, запас 15 мин).
- Анимация переходов: `<ClientRouter />` в `Layout.astro` (на всех страницах) + `transition:name` на заголовке
  карточки (`Card.astro`) — заголовок «перелетает» в страницу поста.
- Деплой (`.github/workflows/deploy.yml`): пуш в `main` → `npm run build` (`astro check` + build + pagefind) →
  scp на VPS. Сборка упала — деплоя нет, на сайте старая версия. CI (`ci.yml`) — только на pull request;
  пуш в ветку `radar-preview` сам по себе ничего не собирает.
- Локальная сборка — в докере (`node:22-alpine`, `docker-compose.dev.yml` блога).
- Язык сайта в конфиге `en`, интерфейс (`i18n/lang/en.ts`) — английский, контент — русский.

## План

| Шаг | Где | Что | Статус |
|---|---|---|---|
| 1 | блог | коллекция `reviews`, страницы `/reviews` (список, фильтр по типу) и `/reviews/<slug>`, пункт меню, стили карточек дайджеста, черновики-образцы; главная/RSS/архив/теги не трогаются | ✅ 07.10: Codex (`gpt-6.1-sol`) по брифу, правки Claude Code (оценка — атрибут `data-high` вместо клиентского скрипта, `z.url()`); сборка зелёная, все критерии брифа; коммит `1c698a9` в ветку `radar-preview` блога |
| 2 | news-radar | (бриф: `specs/briefs/tz4-i4.3-step2-site.md`) разборы с фронтматтером Astro; дайджест для сайта (`analyzer/site_digest.py`) из того же черновика; один коммит на выпуск (дайджест + разборы) в `neuronavt`; блок `site` в конфиге за флагом; `NEURONAVT_GITHUB_TOKEN` в `.env.example`; проверка фронтматтера перед коммитом | 🔧 07.10: реализован Codex, ожидает pytest/mypy и сборку блога в Docker |
| 3 | news-radar | Telegram-анонс: режим `telegram: "announce"` шаблона `ai_value` (по умолчанию `full`), ожидание, пока страница выпуска откроется (до ~5 мин), новая версия промпта дайджеста с `lead` / `highlights` | ✅ 08.10: Codex + правки Claude Code (ожидание перенесено в бот) |
| 4 | news-radar | `scripts/migrate_knowledge_to_site.py`: старые md из `knowledge/` → фронтматтер Astro, `--dry-run`, один коммит | — |

Код по брифам пишет Codex; Claude Code проверяет диф, собирает блог и гоняет pytest/mypy в докере, коммитит.
**Новые зависимости:** не ожидаются (в блоге всё нужное уже есть; в news-radar — `httpx` и Git Data API).

## Контракт разметки дайджеста (шаг 1 стилизует, шаг 2 генерирует)

Описан в брифе шага 1, раздел «Контракт разметки». Ключевое: карточка `<article class="radar-card">`,
заголовок карточки со `style="view-transition-name: review-<slug>"`, тот же `view-transition-name`
у заголовка страницы разбора — заголовок «перелетает» из дайджеста в разбор.

## Шаг 1 — заметки

- **Ошибка брифа:** черновики (`draft: true`) в блоге скрыты всегда, и в dev тоже (`postFilter`). Для просмотра
  образцов `draft` снимается временно.
- Разборы в поиске: индекс pagefind вырос с 7 до 11 страниц (3 разбора + дайджест-образец).
- Предпросмотр: `.claude/launch.json` → `neuronavt-dev` (docker `node:22-alpine`, порт 4322, 4321 занят другим чатом).

## Шаг 2 — приёмка Claude Code (07.10)

- pytest 341 passed / 12 skipped (анализатор), 73 passed (коллекторы); mypy — без ошибок (80 файлов).
- Сборка блога (`node:22-alpine`, ветка `radar-preview`) на md, сгенерированных кодом news-radar: 0 ошибок,
  разбор не попал в главную и RSS, дайджест — в `/posts/`, `view-transition-name` карточки и разбора совпадают.
- **Правки Claude Code поверх Codex:**
  1. **Инъекция HTML в разборы.** Тело разбора от LLM шло в md как есть, Astro рендерит сырой HTML —
     сборка подтвердила (`<b>` из текста стал тегом). Добавлен `neutralize_html` (экранирует `<` вне кода),
     тест `test_site_review_body_cannot_inject_html`.
  2. Файл кандидатов — только при `site.live`, а не при `site.enabled` (см. выше); тест переписан под три случая.
  3. Ошибка в новом тесте Codex: проверял отсутствие слова `candidate`, которое есть в имени класса.
- **Старый тест изменён с разрешения владельца (07.10):** `test_config_has_flags_in_both_locations` ожидал
  `extras == ["candidates", "knowledge"]`, теперь `["candidates", "knowledge", "site"]` — точность проверки та же.
- **Ограничение предпросмотра:** повторный выпуск в тот же день перезаписывает дайджест на сайте; у статей,
  уже опубликованных в первом прогоне (`md_path` = путь news-radar при `live: false`), кнопки «Разбор» пропадут.
  При `live: true` не воспроизводится.
- **Владельцу для живого прогона:** `NEURONAVT_GITHUB_TOKEN` в `.env` (fine-grained, только `neuronavt`,
  Contents: Read and write), затем `docker compose up -d --force-recreate --no-deps analyzer`.

## Дальше (не в И4.3)

- **Деплой news-radar на VPS** — следующий шаг после И4.3 (решение 07.10), только статьи, крипту не переносим.
  Начать с осмотра сервера (`cts serv`) и плана; без «ок» на сервер не заходить.

## Шаг 2 — реализация Codex (07.10), ✅ приёмка Claude Code 07.10

**Что сделано:**

- `config/config_watcher.py`, `config/settings.json`, `.env.example`: блок `site`, предпросмотр включён
  только в settings, extra после `knowledge`, отдельный fine-grained токен.
- `analyzer/knowledge_publisher.py`: `description`, фронтматтер `reviews`, проверка даты/полей/URL/оценки,
  подготовка `SiteReview` без коммита, генерация одна для всех площадок, переиспользование старых путей.
- `analyzer/site_digest.py`: пост из draft, контракт HTML, порядок секций, русские склонения,
  экранирование текстов и атрибутов, условные разборы/переходы, кандидаты по убыванию оценки.
- `analyzer/pipeline/extras.py`: общий коммит сайта, защита от сбоев, фиксация путей после успеха,
  общий заголовок кандидатов для двух форматов; отдельный md кандидатов выключается флагом сайта.
- `analyzer/analyzer.py`: одна строка сохраняет draft до extras.
- `tests/test_site_digest.py`, `tests/test_site_publish.py`: контракты и публикация без сети с моками;
  тест экспорта пишет готовые Astro md в `tmp_path` для сборки блога.
- `mypy.ini`: новый модуль и тесты в `files=`, `strict=True`; baseline не расширен.
- `docs/06_digest.md`, `docs/09_config_hot_reload.md`, `docs/11_problems_learned.md`: настройки,
  публикация, fallback и YAML-дата. `specs/STATE.md`: следующий шаг — приёмка.

**Разведение `md_path`:**

- После прежней успешной площадки путь сохраняется как раньше. После успешного общего коммита сайта:
  `live: false` + успешный GitHub news-radar сохраняет `knowledge/...`; `live: true` заменяет новым
  путём сайта и ссылкой `https://neuronavt.blog/reviews/<slug>/`.
- Если в preview доставлен только сайт — пишется путь сайта, но текущий `md_map` не подменяется;
  последующий выпуск переиспользует путь сайта (явное правило брифа для уже опубликованных статей).
- Сбой коммита не меняет путь/ссылки. Невалидный разбор не попадает в коммит, старый fallback остаётся.
- Старый `knowledge/...` не переносится; это шаг 4. Поэтому у такого разбора в новом посте нет кнопки
  «Разбор» и имени перехода, пока миграция не выполнена.

**Коммиты:** нет, `.git` не изменялся. После приёмки оркестратору:

```bash
git add .env.example analyzer/analyzer.py analyzer/knowledge_publisher.py analyzer/site_digest.py analyzer/pipeline/extras.py config/config_watcher.py config/settings.json mypy.ini tests/test_site_digest.py tests/test_site_publish.py docs/06_digest.md docs/09_config_hot_reload.md docs/11_problems_learned.md specs/STATE.md specs/reports/tz4-i4.3.md
git commit -m "feat(tz4-i4.3): publish radar digests and reviews to Neuronavt"
```

**Новые зависимости:** нет.

**Расхождения со спекой:** публикация на сайт — принятое владельцем расширение И4.3, изменения
v1.18 остаются за владельцем. Спека не изменена.

**Что НЕ сделано / отложено:** pytest/mypy и сборка Astro не запускались — запрет Docker для Codex.
Не было сетевых публикаций; блог только читался. Telegram-анонс/промпт — шаг 3, миграция — шаг 4.
`renderer.py`, classic/spoiler и их промпты не изменены.

**Побочные находки / спорные моменты:** ~~при `site.enabled: true` файл кандидатов не создаётся~~ → исправлено
Claude Code: файл отключается только при `live: true`, в предпросмотре ссылка в Telegram остаётся. Preview сохраняет GitHub-путь, поэтому автоматически не помнит
параллельную копию сайта при следующем выпуске; миграция/переключение — отдельные шаги.

**Команды приёмки (оркестратор, в Docker):**

```bash
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
# Export schema-valid md fixtures to the mounted data directory for a disposable blog clone:
docker compose run --rm --no-deps analyzer python -m pytest -q tests/test_site_digest.py::test_description_breakdown_and_export_for_blog --basetemp=/app/data/site-acceptance
```

После экспорта файлы находятся в `data/site-acceptance/test_description_breakdown_and_0/blog/`.
Оркестратор копирует их в **отдельный временный клон** блога с веткой `radar-preview`, затем запускает
`npm run build` в `node:22-alpine` (Astro check + build + pagefind). Сам рабочий блог Codex не изменял.

Готовые команды для сборки **временного** клона (после теста экспорта, из news-radar):

```bash
blog_check_root="$(mktemp -d /tmp/news-radar-blog-check.XXXXXX)"
git clone --local --branch radar-preview "$HOME/Documents/Ai/apps/neuronavt" "$blog_check_root/neuronavt"
cp -R data/site-acceptance/test_description_breakdown_and_0/blog/src/content/. "$blog_check_root/neuronavt/blog/src/content/"
docker compose -f "$blog_check_root/neuronavt/docker-compose.dev.yml" run --rm --no-deps blog sh -c 'npm ci && npm run build'
```

**Статическая проверка Codex:** все изменённые Python-файлы прочитаны через `ast.parse`,
`settings.json` — через `json.loads`; `git diff --check` без ошибок. Это не заменяет pytest/mypy/сборку.


## Шаг 3 — реализация Codex (08.10), ✅ приёмка Claude Code 08.10

**Что сделано:**
- `analyzer/prompts.py`, `analyzer/json_schemas.py`: новая v2 с `lead` / `highlights`, старые константы сохранены.
- `analyzer/pipeline/writers.py`: реестр пар промпт/схема, выбор по конфигу, WARNING + v1 при неизвестной
  версии, очистка необязательного содержания, анонс только при успешном коммите сайта.
- `analyzer/site_digest.py`: HTML-анонс, общий с постом подсчёт/склонения, экранирование, сокращение пунктов.
- `analyzer/pipeline/extras.py`: GET до 200/таймаута, интервал 15 с, только live после успешного коммита.
- Оба конфига: версия (код v1, settings v2), Telegram full, ожидание 300 с.
- `tests/test_site_announce.py`, `mypy.ini`: новые offline-контракты, strict для нового файла.
  `tests/conftest.py`: HTTP 200 для ожидания в существующих тестах публикации, чтобы они оставались
  без сети; сами тесты и их проверки не изменены.
- `docs/06_digest.md`, `specs/STATE.md`: настройки, включение и статус.

**Коммиты:** нет. После приёмки оркестратору:
```bash
git add analyzer/prompts.py analyzer/json_schemas.py analyzer/pipeline/writers.py analyzer/site_digest.py analyzer/pipeline/extras.py config/config_watcher.py config/settings.json tests/test_site_announce.py tests/conftest.py mypy.ini docs/06_digest.md specs/STATE.md specs/reports/tz4-i4.3.md
git commit -m "feat(tz4-i4.3): add daily leads and Telegram announcements"
```

**Новые зависимости:** нет.

**Расхождения со спекой:** расширение площадки И4.3 уже принято владельцем, изменение спеки за ним.

**Что НЕ сделано / отложено:** pytest/mypy не запускались (запрет Docker); переключение live/main/announce
и реальная публикация не выполнялись. `renderer.py`, classic/spoiler, v1 промпт и схема не изменены.

**Побочные находки / спорное:** ожидание до 300 с добавляется к генерации, а таймаут бота/API клиента
может закончиться раньше (грабля 44). По брифу таймаут страницы не отменяет анонс: ссылка может ещё
возвращать 404. Preview тоже позволяет announce при наличии успешного коммита, как требует бриф;
включать режим следует вместе с live/main. Лимит 1000 достижим сокращением пунктов для штатного URL;
аномально длинный base_url сам может превысить лимит без пунктов.

**Команды приёмки:**
```bash
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
```

**Статическая проверка:** AST изменённых Python-файлов и JSON настроек валидны; `git diff --check` чистый.
Защищённые v1-константы сравнены с HEAD и не изменены; `renderer.py` не затронут.

## Шаг 3 — приёмка Claude Code (08.10)

- Первый запуск Codex 07.10 завис на 13 ч (`Reading additional input from stdin...`), без изменений; перезапуск с `< /dev/null`.
- **Ожидание деплоя перенесено из анализатора в бот (решение владельца 08.10).** У Codex оно было в extra `site`:
  до 300 с сверх генерации при таймауте бота к API 180 с (по расписанию) — выпуск сгенерировался бы, но не ушёл.
  Теперь: `DigestPart.site_url` (только при `live: true`) → поле `site_url` в ответе `/digest/generate` →
  `bot/site_wait.py::wait_for_parts` перед отправкой (и по расписанию, и по `/digest new`).
- Хак Codex в общем `tests/conftest.py` (autouse-фикстура по имени файла) откатан — после переноса не нужен.
- pytest 354 passed / 12 skipped, mypy без ошибок (82 файла). Тест `bot/site_wait.py` идёт в контейнере анализатора
  с подключённой папкой бота (в образе бота нет pytest):
  `docker compose run --rm --no-deps -v "$PWD/bot:/app/bot" analyzer python -m pytest -q tests/test_site_announce.py`
- **Побочная находка:** таймаут бота к API по расписанию — 180 с, по `/digest new` — 300 с. Генерация с разборами
  растёт с их числом; посмотреть фактическую длительность выпуска 08.10 на сервере.
- Включение анонса: `site.branch: "main"`, `site.live: true`, `telegram: "announce"` — вместе; иначе анонс сошлётся на неразвёрнутую ветку.
