# Бриф Codex: И4.3, шаг 2 — news-radar публикует дайджест и разборы на сайт «Нейронавт»

Репозиторий: `~/Documents/Ai/apps/news-radar`. Сначала прочитай `AGENTS.md` → `CLAUDE.md` (жёсткие правила проекта),
затем `specs/reports/tz4-i4.3.md` (решения владельца) и бриф шага 1 `specs/briefs/tz4-i4.3-step1-blog.md` —
там **контракт разметки** дайджеста и схема коллекции `reviews`, которые блог уже принимает
(ветка `radar-preview` репозитория `OnixFireOne/neuronavt`, локально `~/Documents/Ai/apps/neuronavt`, только читать).

## Цель

После того как дайджест `ai_value` написан, news-radar **одним коммитом** в `OnixFireOne/neuronavt` кладёт:
- разборы статей выпуска — в `blog/src/content/reviews/YYYY/MM/<slug>.md` с фронтматтером коллекции `reviews`;
- пост-дайджест — в `blog/src/content/posts/_digests/<YYYY-MM-DD>-ai-radar.md` по контракту разметки.

Разбор генерируется **один раз** (как сейчас) и может параллельно уходить в старую площадку `github` (news-radar):
это нужно на время предпросмотра, пока сайт из ветки `radar-preview` не деплоится.
Telegram-шаблон `ai_value` в этом шаге **не меняется**, кроме адреса ссылки «разбор (md)» в режиме `live` (ниже).
Анонс в Telegram и поля `lead`/`highlights` в промпте — шаг 3, не сейчас.

## Как сейчас устроено (прочитай перед правкой)
- `analyzer/analyzer.py` ~1070–1080: `draft = await writer.compose(...)` → `for extra in spec.extras: await EXTRAS.get(extra)(selected, digest_ctx)` → `writer.render(...)`.
- `analyzer/pipeline/extras.py`: extra `candidates` (md-файл кандидатов в `extra_files`), extra `knowledge` → `publish_selected`
  в `analyzer/knowledge_publisher.py` (генерация md, площадки `local`/`github`, `batch_commit` через Git Data API —
  `GitHubPublisher.commit_files`, путь md пишется в `analysis.md_path`, ссылки → `md_map`).
- `analyzer/pipeline/writers.py::_ai_value_render` — Telegram HTML через `renderer.render_digest` (**renderer.py не трогать**,
  кроме случая, если без этого никак — тогда остановись и опиши).
- Черновик дайджеста (`draft`): `{"items": [{"source_id", "title", "takeaway", "summary", ...}]}`, `content_type` берётся из строки статьи.

## Что сделать

### 1. Конфиг — блок `site` (в ДВА места: `DEFAULT_CONFIG` в `config/config_watcher.py` и `config/settings.json`)
```json
"site": {
  "enabled": false,
  "live": false,
  "repo": "OnixFireOne/neuronavt",
  "branch": "radar-preview",
  "base_url": "https://neuronavt.blog",
  "posts_dir": "blog/src/content/posts/_digests",
  "reviews_dir": "blog/src/content/reviews",
  "digest_slug": "{date}-ai-radar"
}
```
- `DEFAULT_CONFIG`: как выше (выключено). `settings.json`: `enabled: true`, `live: false`, `branch: "radar-preview"` — предпросмотр.
- `enabled` — писать на сайт. `live` — сайт стал основной площадкой: ссылки «разбор» в Telegram ведут на сайт.
  При `live: false` ссылки остаются как сейчас (GitHub news-radar), сайт пополняется параллельно.
- В `categories.articles.extras` (`settings.json`) добавить `"site"` **после** `"knowledge"`.
- Токен — новая переменная `NEURONAVT_GITHUB_TOKEN` (только в `.env.example`, с комментарием: fine-grained token,
  Contents: Read and write, только репозиторий `OnixFireOne/neuronavt`; где создать — как у `GITHUB_TOKEN`).
  Нет токена → `site` пропускается с INFO, выпуск идёт как раньше.

### 2. Разборы в формате сайта — `analyzer/knowledge_publisher.py`
- `KnowledgeDoc` + поле `description`: для `format: full` — текст раздела `tldr` («Коротко»), для `brief` — `idea`;
  одна строка, обрезка по границе слова до ~280 символов.
- Функция `build_site_review(doc, digest_slug) -> str` — md для коллекции `reviews`. Фронтматтер:
  `title`, `description`, `pubDatetime`, `tags`, `source_url`, `source_type`, `content_type`, `value_score`, `digest`.
  - **`pubDatetime` — без кавычек**, ISO в UTC (`2026-10-07T06:10:00.000Z`): схема блога `z.date()` строку в кавычках
    не примет, и **упадёт сборка всего сайта**. Остальные строки — как сейчас, через `json.dumps` (валидный YAML).
  - `content_type` вне `practical_case|tutorial|tool_release|research|opinion|hype_news|other` → `other`.
  - `value_score` — число 0–10. `source_url` — только `http(s)://`.
  - Тело — то же, что сейчас (`doc.body` / Идея-Вывод), без заголовка-H1 (заголовок рисует страница).
- Путь: `<reviews_dir>/YYYY/MM/<имя как сейчас в build_path>.md` (то же имя файла, что для `knowledge/`: дата-слаг-id).
  Slug на сайте = имя файла без `.md`, адрес разбора — `<base_url>/reviews/<slug>/`.
- **Проверка перед коммитом** (`validate_site_frontmatter` или аналог): обязательные поля непустые, дата парсится,
  тип из списка, оценка в диапазоне, URL http(s). Не прошёл — файл **не коммитится**, WARNING с id статьи,
  ссылка на этот разбор ведёт туда же, куда при `live: false`.
- `publish_selected` получает площадку сайта (своё имя, напр. `site`; включается блоком `site`, а не `knowledge.targets`):
  она **не коммитит сама**, а складывает готовые файлы разборов (путь, контент, slug, message_id) в список,
  который extra `site` заберёт из `ctx.artifacts`. Генерация md — по-прежнему одна на статью для всех площадок.
- Уже опубликованная статья (`analysis.md_path` есть): если путь в `reviews_dir` — ссылка на сайт; если старый
  `knowledge/...` — как сейчас, blob в news-radar; на сайт её в этом шаге **не** дописываем (перенос — шаг 4).
  `analysis.md_path` при успешном коммите на сайт = путь на сайте; при `live: false` и успешной площадке `github`
  оставь путь news-radar (ссылки в Telegram пока идут туда) — продумай и опиши в ответе, как ты это развёл.

### 3. Дайджест для сайта — новый `analyzer/site_digest.py` (в `files=` `mypy.ini`, `--strict`)
`build_digest_post(draft, selected, pool, reviews, template_cfg, site_cfg, now) -> (path, content, url)`:
- Фронтматтер: `title: "AI-радар — 7 октября"` (месяц по-русски в родительном), `pubDatetime` (без кавычек, `now` минус
  1 минута), `description` (`"8 статей: 3 инструмента, 3 практики, 2 исследования"` — склонения по-русски), `tags: ["дайджест", "ai-радар"]`.
- Тело — **строго по контракту разметки из брифа шага 1**:
  `radar-meta` (отобрано N из M и разбивка по типам), `radar-lead` — только если в `draft` есть непустой `lead`
  (появится в шаге 3; сейчас блок пропускается), секции `h2.radar-section` по типам в порядке
  Инструменты → Практика (кейсы, туториалы) → Исследования → Мнения → Новости → Другое (подписи —
  из `template_cfg.types` или словаря, но заголовки секций — во множественном числе), карточки `article.radar-card`.
- Карточка: заголовок; `view-transition-name: review-<slug>` — **только** если у статьи есть разбор на сайте в этом
  же коммите или ранее; `span.radar-score` с `data-high="true"` при оценке ≥ 8; `radar-takeaway`, `radar-summary`;
  кнопки «Разбор» (`/reviews/<slug>/`, относительная ссылка) и «Источник» (внешняя, `target="_blank" rel="noopener"`).
- `details.radar-candidates`: кандидаты из `pool` с оценкой ≥ `candidates_list.min_score`, по убыванию оценки,
  ссылка на источник + оценка. Заголовок кандидата — как в `build_candidates_markdown` (переиспользуй, не дублируй логику).
- **Весь текст от LLM и из статей — экранировать** (`html.escape`, в атрибутах — с кавычками). Никакого сырого HTML из статей.
- Повторный запуск в тот же день перезаписывает тот же файл (`digest_slug` от даты) — это нормально.

### 4. Extra `site` — `analyzer/pipeline/extras.py`
- `analyzer/analyzer.py`: перед циклом extras положить черновик в `digest_ctx.artifacts["draft"]` (одна строка;
  файл под mypy — после правки прогнать).
- Extra `site`: если `site.enabled` и есть токен — собирает дайджест-пост + файлы разборов из artifacts и делает
  **один** `GitHubPublisher(site.repo, site.branch, token, batch=True).commit_files(...)` с сообщением
  `feat(radar): AI radar digest YYYY-MM-DD (+N reviews)`. Пишет в artifacts `site_digest_url` (`<base_url>/posts/<slug>/`)
  и, при `live: true`, подменяет в `md_map` ссылки на разборы адресами сайта.
  Коммит не удался → WARNING, `md_map` не трогается, выпуск в Telegram уходит как обычно.
- При `site.enabled` extra `candidates` **не** пишет отдельный md-файл кандидатов (список теперь внутри дайджеста);
  при `site.enabled: false` — всё как сейчас.
- Ни один сбой `site` не должен ронять выпуск (как у `knowledge`).

### 5. Документация
- `docs/06_digest.md` — раздел «Публикация на сайт»: блок `site`, `live`, путь файлов, один коммит, токен.
- `docs/09_config_hot_reload.md` (или где описаны ключи) — новый блок `site`.

## Ограничения
- Шаблоны `classic`/`spoiler` и их промпты — ни строки. `renderer.py` — см. выше.
- Новых зависимостей нет (`httpx`, `html`, `json` — уже есть/stdlib).
- Каждый новый файл — в `files=` `mypy.ini`, `--strict`. Legacy-baseline не расширять.
- Существующие тесты не менять и не ослаблять.
- Комментарии в коде — английские. Секреты — только из env, в логах токен/ответы API не печатать (см. существующий `GitHubPublisher`).
- Ты не можешь запускать docker и писать в `.git`: pytest/mypy и коммит делает Claude Code. Напиши тесты так, чтобы они шли без сети (моки `httpx`, как в `tests/test_knowledge_batch.py`).

## Тесты (новые файлы, напр. `tests/test_site_digest.py`, `tests/test_site_publish.py`)
1. `build_site_review`: `pubDatetime` без кавычек и парсится; неизвестный тип → `other`; `description` из «Коротко»; невалидный фронтматтер отсекается проверкой.
2. `build_digest_post`: разметка по контракту (классы, `data-high` только ≥ 8, `view-transition-name` только у статей с разбором),
   экранирование `<script>`/кавычек из заголовка и резюме, порядок секций, склонения в `description`, без `lead` — без блока.
3. Extra `site`: **один** вызов `commit_files` со всеми разборами и дайджестом; `live: false` — `md_map` прежний;
   `live: true` — ссылки на `https://neuronavt.blog/reviews/<slug>/`; нет токена — пропуск без исключения; сбой коммита — выпуск рендерится.
4. `site.enabled: false` — поведение полностью прежнее (кандидаты файлом, никаких запросов к neuronavt).

## Приёмка (Claude Code, в докере)
```bash
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
```
Плюс: md, собранные тестом, Claude Code кладёт в клон блога и прогоняет `npm run build` в `node:22-alpine` — сборка должна пройти.

## Ответ
Кратко: файлы, как развёл `md_path` между `live: false/true`, что осталось спорным.
