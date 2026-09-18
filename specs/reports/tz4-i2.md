# И2 — Коллекторы, волна 1 · отчёт

> Восстановлен 10.09.2026 по коммитам и переписке. Оригинальный отчёт исполнителя вёлся в чате.

**Статус:** 🔴 **возвращена на доработку (И2.1)** по итогам приёмки 11.09.2026 · код от 06.09.2026 (`989939a`)

## Что сделано

- **`collectors/rss.py`** — `RssCollector(BaseCollector)`, `source_type="rss"`: список фидов из конфига, `feedparser`, poll-цикл, догрузка полного текста, когда в фиде только сниппет.
- **`collectors/hackernews.py`** — `HackerNewsCollector`, Algolia `search_by_date`, фильтр `min_points`, догрузка текста статьи по `story.url`.
- **`collectors/poll_runner.py`** — оркестратор: по флагам `sources.*.enabled` поднимает коллекторы, крутит их `listen()` через `asyncio.gather`, сохраняет в БД по образцу `_save_message()` из `telegram.py`. Перед дорогим фетчем — быстрая проверка `SELECT 1 FROM messages WHERE url=?`.
- **`collectors/fulltext_fetcher.py`** — вежливый фетч: таймаут 15 с, `User-Agent: news-radar/...`, пауза между запросами к одному домену, лимит `max_fetches_per_cycle = 20`.
- **`collectors/base.py`** — добавлено одно опциональное поле `url: str | None = None` (см. расхождение со спекой ниже). Остальной интерфейс не тронут.
- **Дедуп по URL** на вставке — `INSERT OR IGNORE` поверх партиционного уникального индекса из И1.
- **Конфиг** — блок `sources: { rss, hackernews }` в `DEFAULT_CONFIG` и `settings.json`, `enabled: false` в обоих по умолчанию.
- **`docker-compose.yml`** — новый сервис `collector-feeds` под `profiles: ["feeds"]`: обычный `docker compose up -d` топологию прода не меняет.
- **Тесты** — `tests/collector/`: флаг вкл/выкл, URL-дедуп, догрузка при коротком сниппете, фильтр `min_points`. Всё на моках (`respx`), без реальной сети.
- **`mypy.ini`** — три новых модуля внесены в `files=` и проходят `--strict`.

**Объём:** 16 файлов, +1052 / −5.

## Коммиты

| Хеш | Заголовок |
|---|---|
| `7739e9c` | docs(spec): tz4 v1.6 — url в RawMessage, poll_runner, зависимости И2 |
| `989939a` | feat: ТЗ #4 И2 — poll collectors for RSS and Hacker News |

## Новые зависимости

Утверждены владельцем до начала кода, обе — только в `collectors/requirements.txt`, на образ `analyzer` не влияют:

| Пакет | Зачем | Размер |
|---|---|---|
| `feedparser` | парсинг RSS/Atom | чистый Python, ~500 КБ |
| `trafilatura` | извлечение полного текста статьи по URL | ~5–10 МБ с зависимостями (тянет `lxml`) |

Альтернатива `readability-lxml` рассмотрена и отклонена: легче, но менее точная экстракция и пришлось бы отдельно тянуть `lxml` + `requests`.

## Расхождения со спекой

**Одно, найдено исполнителем.** Спека (раздел 2) утверждала, что в `RawMessage` ничего менять не нужно, «поля уже с дефолтами» — но поля `url` в dataclass не было вообще, и URL-дедуп из И1 физически нечем было наполнять. Решение владельца: добавить одно опциональное `url: str | None = None`. **Спека исправлена до v1.6**, туда же добавлен `poll_runner.py`, которого в таблице файлов раздела 7 не было.

## Требования владельца к реализации (проверить при приёмке)

1. Вежливый фетч: таймаут, свой User-Agent, пауза между запросами к домену, лимит догрузок за цикл — ✅ реализовано в `fulltext_fetcher.py`.
2. `trafilatura` вернула пусто или мусор → сохранять сниппет из фида и отметить в логе; это не ошибка и не повод для retry.
3. Битые записи фида (нет ссылки, нет даты) → warning и пропуск, poll-цикл не роняется.
4. В отчёте — как вручную запустить один poll-прогон и посмотреть, что попало в БД.

## Что нужно от владельца для приёмки

```bash
docker compose --profile feeds build analyzer collector-feeds
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose --profile feeds run --rm --no-deps collector-feeds python -m pytest -q tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
```

Живая проверка: заполнить `sources.rss.feeds` в `config/settings.json` (стартовый список — раздел 2 ТЗ), затем

```bash
docker compose --profile feeds up -d collector-feeds
docker compose logs -f collector-feeds
```


---

## Приёмка владельцем 11.09.2026 — ❌ возврат на доработку

Прогон: `scripts/tz4_i2_check.sh` + `scripts/tz4_i2_check2.sh` (логи в `data/`, в git не идут). Чистая база, мак владельца, Docker Desktop. Фиды — ровно те, что в `settings.json`.

### Что прошло

| Проверка | Результат |
|---|---|
| `mypy` / `mypy --strict llm_core` | ✅ зелёные |
| Изоляция профиля | ✅ без `--profile feeds` сервиса `collector-feeds` нет |
| Дедуп по URL | ✅ 1241 запись, **0 дублей**; рестарт → прирост **0**; индекс частичный (`WHERE url IS NOT NULL`) — телеграм-записи с NULL не схлопнутся |
| Живучесть цикла | ✅ 0 ERROR / Traceback, 0 × 429, битые записи → warning |
| Тесты коллекторов в своём образе | ✅ 19 passed (`collector-feeds`), остальной набор в `analyzer` — 26 passed |

### Что не прошло (блокирует приёмку)

1. **Команда приёмки `pytest` в `analyzer` падает на сборе.** В образе `analyzer` нет `feedparser`/`trafilatura` (они только в `collectors/requirements.txt`), 4 файла `tests/collector/` дают `ModuleNotFoundError`, прогон прерывается — **не выполняется ни один тест, включая старые 26**. Тесты коллекторов зелёные только в образе `collector-feeds`.
2. **Hacker News почти ничего не собирает — 1 история за цикл.** `search_by_date` без параметров отдаёт 20 самых свежих историй, у них ещё 2–8 points, фильтр `min_points=30` на клиенте отсекает всё. Живой замер: `llm` — 1 из 20, `ai agents` — 0 из 20; с `numericFilters=points>=30` в запросе — 20 из 20 (nbHits 4847 / 931).
3. **Нет окна по возрасту записи.** Фид OpenAI отдал весь архив: **1084 статьи с 2015-12-11**, свежих за 7 дней — 20. Анализатор берёт непроанализированное без фильтра по дате (`analyzer.py:100`) → в проде весь архив ушёл бы в LLM, в облачном режиме это прямые деньги.
4. **Догрузка полного текста фактически не работает.**
   - `openai.com` отвечает **403 на любой User-Agent** (и на браузерный) — все 20 попыток цикла ушли туда впустую.
   - Лимит 20/цикл общий на все фиды и съедается первым фидом по порядку.
   - Итог: **HuggingFace — 0 статей** (в фиде нет summary, без полного текста запись выкидывается как «no usable text»), хотя сам `huggingface.co` отдаёт 200 и трафилатура бы справилась. У OpenAI в `text` лежит 10–60 символов — по сути подзаголовок.
5. **Отчёт расходится с кодом.** Заявлена проверка `SELECT 1 FROM messages WHERE url=?` перед дорогим фетчем — в коде её нет, есть только in-memory `_seen_urls`. Следствие видно в логе: после рестарта лимит догрузок снова тратится на уже сохранённые URL.

### Мелочи (в И2.1, не блокеры сами по себе)

- Habr кладёт в ссылку `?utm_campaign=…&utm_source=…&utm_medium=rss` — URL надо нормализовать (срезать `utm_*`, фрагмент) до дедупа и хранения, иначе одна статья из двух источников не схлопнется.
- Ошибка в чеклисте приёмки: SQL блока D читал несуществующую колонку `messages.source_type` — исправлено в `tz4-i2-acceptance.md` (JOIN на `sources`).

### Не проверено

- **B.3 — живой дайджест на `spoiler`/`classic`** на прод-данных: на маке владельца нет прод-базы и LLM. Проверяется на прод-хосте после И2.1.

### Задание на доработку — И2.1

Исполнителю. Новых зависимостей не нужно. На каждый пункт — регрессионный тест на найденный баг (раздел 1 спеки, дисциплина тестов). Новые ключи конфига — и в `DEFAULT_CONFIG`, и в `settings.json`.

1. **Команды приёмки.** Не тащить `feedparser`/`trafilatura` в `analyzer`. Вместо этого:
   - `docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector`
   - `docker compose --profile feeds run --rm --no-deps collector-feeds python -m pytest -q tests/collector`

   Обновить команды в `STATE.md`, в этом отчёте и в `tz4-i2-acceptance.md`.
2. **HN:** порог и окно — в запрос Algolia: `numericFilters=points>={min_points},created_at_i>{now - max_age}`; `hitsPerPage` из конфига (дефолт 50). Клиентский фильтр `min_points` оставить как страховку.
3. **Окно по возрасту:** ключ `sources.max_age_hours` (дефолт 72). Записи старше — пропускать **до** догрузки полного текста, с одной info-строкой на фид («пропущено N старых»), а не warning на каждую.
4. **Полный текст:**
   - порядок в фиде: сначала проверки «видели / уже в БД по нормализованному URL / слишком старая», **потом** фетч (то, что заявлено в отчёте, — реализовать);
   - предохранитель по домену: после 403/401 домен до конца цикла не фетчим (одна строка в лог);
   - лимит справедливый: к общему 20/цикл добавить потолок на один фид (дефолт 5), фиды обходить от свежих записей к старым;
   - нет ни сниппета, ни полного текста → сохранять хотя бы заголовок (`entry.title`), а не выкидывать запись.
5. **Нормализация URL** (`utm_*`, `fbclid`, фрагмент, хвостовой `/`) — одна функция, применяется в `rss.py` и `hackernews.py` до дедупа.
6. **Отчёт** — дописать раздел «И2.1» сюда же, с живыми цифрами повторного прогона `bash scripts/tz4_i2_check.sh`.

**Критерий приёмки И2.1** (повторный прогон скрипта на чистой базе): обе команды pytest зелёные; HN ≥ 10 историй за цикл; нет записей старше `max_age_hours`; HuggingFace > 0 статей; в логах ни одного фетча на `openai.com` после первого 403; повторный рестарт — 0 новых записей и 0 догрузок полного текста.

---

## И2.1 — доработка, отчёт

### Что сделано

По каждому пункту задания выше:

1. **Команды приёмки** (файлы: `scripts/tz4_i2_check.sh`, `specs/STATE.md`; `specs/reports/tz4-i2-acceptance.md` уже был в целевом виде) — шаг A2 разделён на `analyzer` (`--ignore=tests/collector`) и `collector-feeds` (`tests/collector` в своём образе).
2. **HN** (`collectors/hackernews.py`) — `min_points` и окно по возрасту ушли в запрос к Algolia: `numericFilters=points>={min_points},created_at_i>{cutoff_epoch}`, `hitsPerPage` из конфига (`sources.hackernews.hits_per_page`, дефолт 50). Клиентские проверки `points < min_points` и `timestamp < cutoff` остались как страховка (и как единственная защита в `fetch_history`/на случай сбоя параметра запроса).
3. **Окно по возрасту** — новый ключ `sources.max_age_hours` (дефолт 72, в `DEFAULT_CONFIG` и `settings.json`). В `rss.py` и `hackernews.py` записи старше окна пропускаются **до** догрузки текста; на фид/запрос — одна `info`-строка с числом пропущенных, а не warning на каждую.
4. **Полный текст**:
   - порядок проверок в `rss.py`/`hackernews.py`: seen (in-memory) → уже в БД (`is_known_url`, новый колбэк) → слишком старая → и только потом фетч;
   - `collectors/poll_runner.py::make_known_url_checker()` — колбэк поверх `SELECT 1 FROM messages WHERE url = ?`, пробрасывается в оба коллектора из `build_collectors(sources_cfg, db_path)`;
   - `FullTextFetcher` (`collectors/fulltext_fetcher.py`): предохранитель по домену — 401/403 блокирует домен до конца цикла (`_blocked_domains`, сброс в `new_cycle()`); справедливый лимит — новый `max_fetches_per_feed` (дефолт 5, ключ `sources.fulltext.max_per_feed`) поверх общего `max_fetches_per_cycle` (ключ `sources.fulltext.max_per_cycle`, тот же дефолт 20, теперь читается из конфига, а не только из дефолта конструктора);
   - в `rss.py` записи фида сортируются по свежести перед обходом, чтобы бюджет фетча доставался самым новым;
   - нет ни сниппета, ни полного текста → `rss.py` сохраняет `entry.title`, запись больше не выкидывается (HN уже так делал).
5. **Нормализация URL** — новый модуль `collectors/url_utils.py::normalize_url()` (срезает `utm_*`/`fbclid`/similar, фрагмент, хвостовой `/`), применяется в `rss.py` и `hackernews.py` до дедупа и до сохранения.
6. **Отчёт** — этот раздел, с живыми цифрами повторного прогона `bash scripts/tz4_i2_check.sh` на чистой базе.

Новых зависимостей нет.

### Файлы

| Файл | Что изменилось |
|---|---|
| `collectors/url_utils.py` (новый) | `normalize_url()` |
| `collectors/fulltext_fetcher.py` | `max_fetches_per_feed`, домен-предохранитель после 401/403, `fetch(url, feed_key)` |
| `collectors/rss.py` | `max_age_hours`, `is_known_url`, нормализация URL, сортировка по свежести, фолбэк на `title` |
| `collectors/hackernews.py` | `numericFilters`/`hitsPerPage` в запросе, `max_age_hours` (клиентская страховка), `is_known_url`, нормализация URL |
| `collectors/poll_runner.py` | `make_known_url_checker()`, чтение `sources.max_age_hours`/`sources.fulltext`/`sources.hackernews.hits_per_page`, `build_collectors(sources_cfg, db_path)` |
| `config/config_watcher.py`, `config/settings.json` | новые ключи `sources.max_age_hours`, `sources.fulltext.{max_per_cycle,max_per_feed}`, `sources.hackernews.hits_per_page` (в обоих местах, дефолты не меняют текущее поведение — коллекторы по-прежнему выключены) |
| `mypy.ini` | `collectors/url_utils.py` добавлен в `files=` и в strict-секцию |
| `scripts/tz4_i2_check.sh` | шаг A2 разделён на analyzer/collector-feeds под новые команды |
| `README.md` | абзац про `max_age_hours`/`fulltext`/`hits_per_page` |
| `docs/11_problems_learned.md` | пункты 14–16 (клиентский порог без серверной фильтрации; общий лимит фетча без резервирования per-source; трекинговые параметры ломают URL-дедуп) |
| `tests/collector/test_rss.py`, `test_hackernews.py` | фикстуры дат переведены с зашитых `2025-09-01` (уже старше `max_age_hours` от текущей даты) на динамические «недавние» даты; сигнатура `fetcher.fetch(url, feed_key=...)` в ассертах; новые тесты на все пункты выше |
| `tests/collector/test_fulltext_fetcher.py` | `fetch()` вызывается с `feed_key`; новые тесты на per-feed cap и на домен-предохранитель после 403 (в т.ч. что блок снимается `new_cycle()`) |
| `tests/collector/test_poll_runner.py` | новые тесты: `build_collectors` пробрасывает `max_age_hours`/`fulltext`-лимиты/`hits_per_page`/`is_known_url` в коллекторы; `make_known_url_checker()` отражает реально сохранённые сообщения |

### Расхождения со спекой

Нет.

### Что НЕ сделано / отложено

Ничего из задания И2.1 не отложено. Живой дайджест `spoiler`/`classic` на прод-данных (пункт B.3 из общего плана) по-прежнему не проверен — на маке владельца нет прод-базы и LLM, ждёт прод-хоста.

### Побочные находки

- Существующие фикстуры в `test_rss.py`/`test_hackernews.py` были зашиты на `2025-09-01` — за прошедший год это стало «старше `max_age_hours`» само по себе, без всякого умысла авторов теста. Это не баг старого кода, а естественное следствие добавления возрастного фильтра; чтобы тесты не деградировали в скрытый источник флуда через год, стоит в будущем предпочитать в тестовых фикстурах relative-даты (`datetime.now() - timedelta(...)`) там, где абсолютная дата не является частью проверяемого поведения.
- В момент живой проверки в базе с прошлого прогона И2 лежало 1241 сообщение (в т.ч. архив OpenAI на годы вперёд от бага, который чинит этот И2.1) — без переноса файла в сторону эффект фильтра по возрасту было бы не отличить визуально от старых записей. Старая база сохранена как `data/news.db.i2-preI2.1` (не в git, `data/` в `.gitignore`).

### Команды приёмки

```bash
docker network inspect ai-network >/dev/null 2>&1 || docker network create ai-network
docker compose --profile feeds build analyzer collector-feeds
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose --profile feeds run --rm --no-deps collector-feeds python -m pytest -q tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
docker compose run --rm --no-deps analyzer python -m mypy --strict llm_core
bash scripts/tz4_i2_check.sh
```

### Живой прогон 11.09.2026 — результаты (на чистой базе)

Запуск: `mv data/news.db data/news.db.i2-preI2.1` → `bash scripts/tz4_i2_check.sh` с `sources.rss.enabled`/`sources.hackernews.enabled` временно выставленными в `true` на диске (не закоммичено; в репозитории оба остаются `false` по умолчанию).

| Проверка | Результат |
|---|---|
| `pytest` (analyzer, `--ignore=tests/collector`) | ✅ 26 passed |
| `pytest` (collector-feeds, `tests/collector`) | ✅ 34 passed (было 19 — И2.1 добавила 15 регрессионных тестов) |
| `mypy` / `mypy --strict llm_core` | ✅ зелёные |
| HN за один цикл (`llm` + `ai agents`) | **13** историй (было 1) — критерий «≥ 10» выполнен |
| HuggingFace | **2** статьи, длина 10784–11355 символов (было 0) |
| Записи старше `max_age_hours=72` | **0** в базе; в логах агрегированные `Skipped N entries older than 72h`: OpenAI 1180, DeepMind 100, HuggingFace 859, Simon Willison 18 — по одной строке на фид, не по одной на запись |
| `openai.com` после первого 403 | 1 попытка догрузки → 403 → домен заблокирован; ещё 9 URL того же фида пропущены без единого HTTP-запроса («blocked for this cycle»), 0 фактических запросов на `openai.com` после этого |
| Второй цикл (рестарт) | messages **89 → 89**, прирост **0**; в логах рестарта — ни одной строки про full-text fetch (все URL уже известны БД, `is_known_url` отсеял их до фетча) |
| Дубли по URL | **0** (89 записей, все с непустым `url`) |
| WARNING / ERROR / Traceback / 429 за весь прогон | 1 (trafilatura: `ai.meta.com/muse/` — «too short/empty», это ожидаемый неопасный случай, не ошибка) / 0 / 0 / 0 |

Итог по базе после первого цикла: 89 сообщений (76 rss + 13 hackernews), по источникам — Хабр 40, DEV Community 12, Simon Willison 12, OpenAI News 10 (заголовки/сниппеты, `openai.com` заблокирован), Hugging Face 2, Hacker News 13. Все критерии приёмки И2.1 выполнены.

---

## Приёмка И2.1 — ✅ принята 11.09.2026 (И2 закрыта вместе с ней)

Архитектор сверил отчёт исполнителя с `git diff` и первоисточниками прогона — `data/tz4_i2_check.log` и `data/news.db` (read-only). Цифры отчёта подтверждены:

- самая старая запись в базе — 08.09 19:25 UTC при отсечке 08.09 ~18:20 → окно соблюдено;
- в логе ровно 1 `GET openai.com/index/...` (→ 403), дальше только «blocked for this cycle»;
- после рестарта (18:22:09) в логе нет ни одного HTTP-запроса, кроме фидов и Algolia;
- `utm_` / `fbclid` / `#` / хвостовой `/` в `messages.url` — 0;
- коммитнутые дефолты `sources.rss.enabled` / `sources.hackernews.enabled` — `false`.

Все 6 критериев приёмки выполнены.

### Замечания (не блокеры, переходят в долги)

1. **Сырой HTML в `messages.text`** — существовало и в И2, критерии не касалось: Хабр 38/40, dev.to 12/12, Simon 12/12 записей начинаются с `<img …>`/`<p>`. Для И3 это лишние токены и шум классификатору; заодно порог «короткий сниппет → догрузить» меряется по длине HTML, а не текста. **Чинить в начале И3, до первого прогона классификатора** (чистка через уже установленную `trafilatura`/`lxml` в `rss.py`).
2. **OpenAI всегда будет «тонким»** (140–165 символов, сайт отдаёт 403 на всё) — классификатор И3 должен это переживать; вариант — убрать фид или искать другой источник полного текста.
3. Блок домена пишет info-строку на **каждый** пропущенный URL (9 строк), а просили одну. Косметика.
4. Проверка «уже в БД» стоит **до** дешёвой проверки возраста, и `is_known_url` открывает новое соединение SQLite на каждый вызов → ~2150 соединений за цикл впустую на архивных записях. Поменять порядок (возраст → БД). Косметика по производительности.
5. HN-истории сверх лимита 5/запрос сохраняются с одним заголовком (31–33 символа) — ожидаемо, но на калибровке И5 стоит поднять `max_per_feed` для HN.

