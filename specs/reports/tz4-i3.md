# Отчёт: ТЗ #4, И3 — часть 1 (шаги 1 и 3)

**Статус:** И3 **не закрыта**. По решению владельца от 21.09 итерация разбита; в этот заход вошли шаги 1 и 3 из раздела 10 спеки. Шаги 2 (реестр провайдеров), 4 (промпт `ai_value` + evals) и 5 (воронка с квотами) — следующими заходами.

**Исполнение:** код шага 1 писал субагент (Sonnet 5) по брифу, диф проверен вручную; шаг 3 писался вручную — файл `collectors/telegram.py` legacy, вне mypy и задевает прод.

---

## Что сделано

### Шаг 1 — чистка сырого HTML из RSS

- **`collectors/rss.py`** — функция `_clean_snippet_html()`. Хабр (38 записей из 40), dev.to и Simon Willison кладут `<p>`/`<img>`/`<a>` прямо в `summary`; этот HTML уезжал в `messages.text` и дальше попал бы в промпт классификатора дословно. Сначала пробуется `trafilatura.extract()`, при `None` или исключении — детерминированный фоллбэк: снять теги → `html.unescape` → схлопнуть пробелы. Оба пути заканчиваются одинаковой нормализацией.
- **`collectors/rss.py`** — порог `_MIN_SNIPPET_CHARS_FOR_FULL_FETCH` (500) теперь меряется по **очищенному тексту**, а не по сырому HTML. Раньше разметка раздувала длину: короткий тизер, обёрнутый в `<p>` и восемь `<img>`, перешагивал 500 символов и никогда не уходил на догрузку. Очищенный текст же и сохраняется в `RawMessage.text`.
- **`collectors/rss.py`** — порядок проверок в `_poll_feed`: было seen → `is_known_url` → возраст, стало seen → **возраст** → `is_known_url`. Сравнение таймстемпа бесплатно, а `is_known_url` — поход в SQLite на каждую запись; на архивных лентах набегало ~2150 коннектов за цикл. Отбор не изменился: старая запись отбрасывалась и раньше.
- **`collectors/fulltext_fetcher.py`** — «Domain X blocked for this cycle» логируется один раз, в момент блокировки (INFO). Повторные пропуски того же домена ушли на DEBUG: на `openai.com`, который 403-ит на любой UA, это были десятки одинаковых строк за цикл.

### Шаг 3 — крипта на паузе

- **`collectors/telegram.py`** — `_is_telegram_enabled()` и гейт в начале `main()`. Проверка стоит **до** чтения `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`: выключенному деплою credentials не нужны. При `false` сервис **идлит** с логом, а не выходит — у него `restart: unless-stopped`, выход дал бы рестарт-луп. Паттерн скопирован из `collectors/poll_runner.py`.
- **`config/config_watcher.py`** — `sources.telegram.enabled` в `DEFAULT_CONFIG`, дефолт **`True`**: прод без этого ключа собирает как раньше.
- **`config/settings.json`** — тот же ключ со значением **`false`**. Это фактический переключатель владельца; возврат крипты — правка одного значения, без кода и compose.

### Тесты (+12, существующие не тронуты)

- `tests/collector/test_rss.py` (+6): очистка Хабр-образного фрагмента с сущностями; пустой и битый ввод не роняет; HTML >500 символов при коротком тексте → догрузка вызвана; длинный реальный текст под разметкой → догрузки нет и тегов в `text` нет; устаревшая запись отбрасывается **без единого вызова** `is_known_url`; свежая запись через `is_known_url` всё ещё проходит.
- `tests/collector/test_fulltext_fetcher.py` (+1): заблокированный домен даёт ровно один INFO в момент блокировки и DEBUG на последующих пропусках.
- `tests/collector/test_telegram_gate.py` (новый, +5): отсутствие `sources`, отсутствие ключа `telegram`, явные `true`/`false`, и `"telegram": null` — битый конфиг это не намерение выключить.

### Документация

- `docs/02_collector.md` — раздел про выключатель, почему идл вместо выхода, почему отсутствие ключа = включён.
- `docs/09_config_hot_reload.md` — раздел про слияние только верхнего уровня и два следствия для вложенных ключей.
- `docs/11_problems_learned.md` — грабли 17, 18, 19.

---

## Коммиты

| Хеш | Заголовок |
|---|---|
| `6147622` | `fix(tz4-i3): clean RSS snippet HTML and cut per-cycle DB churn` |
| `67dc1ce` | `feat(tz4-i3): pause the telegram source by config` |

Отдельно, до итерации: `74ad1dd` — `docs(claude): add commit conventions and ignore .kilo`.

## Новые зависимости

**Нет.** `trafilatura` уже стояла (её использует `fulltext_fetcher.py`), `html` и `re` — stdlib.

## Расхождения со спекой

- **Раздел 8.1 указывал `sources.telegram.enabled = false`, но этот ключ никто не читал.** `poll_runner` гатит только `rss`/`hackernews`, а telegram-коллектор — отдельный compose-сервис `collector`, стартующий безусловно. Добавить ключ без потребителя значило бы получить конфиг-ложь: галочка стоит, крипта собирается. Решение владельца от 21.09 — провести флаг в `telegram.py` по-настоящему; сделано. **Спеку под это править владельцу** (р. 8.1 стоит дополнить строкой о том, что читает ключ).
- **Квота `crypto = 0` в этот заход не вошла.** Её потребитель — воронка отбора (р. 3.2), а это шаг 5. Ключ будет добавлен вместе с потребителем, как требует спека. До тех пор «крипта на паузе» держится на выключенном коллекторе.

## Что НЕ сделано / отложено

- Шаги 2, 4, 5 итерации И3 — не начаты.
- `analyzer/analyzer.py` **остаётся в mypy-бейзлайне**: в этот заход файл не правился, снимать бейзлайн не на чем. Снять при шаге 4 или 5, когда файл действительно меняется.
- Порог 500 стал эффективно строже (то же число, но по чистому тексту) — доля записей, уходящих на full-text fetch, вырастет. Бюджеты `max_per_cycle`/`max_per_feed` её ограничивают, но после первого живого прогона стоит посмотреть, не упирается ли теперь сбор в них.
- Реального поведения `trafilatura.extract()` на коротких фрагментах не проверяли живьём: тесты его мокают. Код написан так, что результат от версии либы не зависит (любое исключение и любой пустой результат уходят в фоллбэк), но это защита конструкцией, а не замер.

## Побочные находки

- **У poll-коллекторов нет страницы в `docs/`.** `docs/02_collector.md` описывает только Telegram-userbot; `rss.py`, `hackernews.py` и `poll_runner.py` живут только в отчёте по И2. Заводить новую страницу в этом заходе не стал — вне задачи.
- **Git-локи в `.git/`** блокировали коммиты владельцу (`index.lock`, `HEAD.lock` от 21.09 и `index.lock.stale-20260918`). Причина — в среде агента запрещено удаление файлов, а гит снимает лок через `unlink`. Подробности — грабли 19.
- **В репозитории лежит `.kilo/`** — рабочая папка стороннего агента с полным клоном проекта в `.kilo/worktrees/fog-abstract/`. Добавлена в `.gitignore` коммитом `74ad1dd`; её содержимое не трогалось.

## Команды приёмки

```bash
docker compose --profile feeds build analyzer collector-feeds
docker compose --profile feeds run --rm --no-deps collector-feeds python -m pytest -q tests/collector
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
```

**Результат прогона 21.09:** 46 passed (collector) · 26 passed (analyzer) · `Success: no issues found in 18 source files`.

---

# Часть 2 — шаг 2: реестр провайдеров (25.09)

**Статус:** шаг 2 **закрыт 25.09**. Цены сверены с выгрузкой расходов прокси, живой вызов через каталог прошёл.

**Исполнение:** код писал Codex (`codex exec` в фоне, задания в `~/.codex-bridge/briefs/news-radar/tz4-i3-step2-c*.md`). Оркестратор (Claude Code) проверял диф, гонял pytest и mypy в Docker, правил мелочи и коммитил.

## Решения владельца 25.09 (меняют текст спеки р.6.1 / 6.1.1 / 8.1 / 8.2)

1. **Библиотеки-шлюзы (LiteLLM, any-llm) рассмотрены и отклонены.** Порт morning-post даёт ту же гибкость (новое облако = профиль в JSON), без зависимостей и с уже найденными граблями прокси. LiteLLM — кандидат на будущее, если понадобятся нативные API сверх chat/messages, балансировка или бюджеты.
2. **Оба протокола в И3:** `chat_completions` и `messages` (р.6.1 п.0 разрешал только один). Названия как в morning-post, а не `openai_chat` / `anthropic_messages`.
3. **Адрес облака — в каталоге** (`config/providers.json`), не в `.env`.
4. **`.env`:** `LLM_PROVIDERS=<профиль>,<профиль>` (порядок = приоритет) и ключи по шаблону `LLM_KEY_<ПРОВАЙДЕР>` (часть имени профиля до точки, заглавными), с возможностью задать своё имя через `api_key_env`. Необязательный `LLM_PROVIDERS_FILE` — другой путь к каталогу.
5. **Модели под задачу — внутри профиля** (`models.default` / `digest` / потом `classify`), потому что у разных облаков разные id одной модели. Отдельной таблицы `llm_routes` нет, она появится вместе с «мульти» (разные задачи в разные облака одновременно).
6. **Автопереключение — потом**, отдельным шагом после шага 4. Сейчас проверяются все профили из списка, вызовы идут в первый. `ProviderRouter.candidates()` уже отдаёт упорядоченный список.
7. **`llm_local_mode` остаётся** как переключатель legacy-режима. В режиме каталога его роль выполняют `gpu_lock` и `chat_template_kwargs` профиля.
8. **Пробу запускает исполнитель** при настройке нового облака, автоматизация не нужна.

## Что сделано

- **`llm_core/probe.py`** — порт `tools/ai-probe.ts`: 8 проверок из оригинала плюс 9-я (пустой ответ при `max_tokens: 1`). Печатает черновик профиля с источником каждого поля и `TODO(unverified)`. Ключ читается только из переменной, указанной в `--key-env`, и маскируется везде, в том числе если прокси вернёт его в теле ответа. Отчёт пишется в `data/llm_probe/`. Лежит в `llm_core/`, а не в `scripts/`: `scripts/` не примонтирован в контейнер, и проба переносится вместе с плагином.
- **`llm_core/catalog.py`** — схема профиля, ручная проверка (неизвестное поле, тип, обязательные поля, `max_tokens` для messages, цены на все модели при `cost_source: table`), шаблон имени ключа, гард `TODO(unverified)`, `resolve_active()` проверяет **все** выбранные профили при старте. `compute_cost()` имеет три честных состояния: при `none` или неполном `usage` возвращает `None`, а не 0.
- **`llm_core/transport.py`** — нейтральные `LLMRequest` / `LLMResponse` и `create_transport()`, единственное место выбора протокола.
- **`llm_core/client_messages.py`** — messages: `system` отдельным полем, текст из блоков, `chat_template_kwargs` вырезается, та же политика повторов. Пустой ответ → `LLMEmptyResponseError` с `usage` и ценой.
- **`llm_core/client.py` / `config.py`** — `auth_style` (`bearer` / `x-api-key` / `none`). С `bearer` запрос байт-в-байт прежний. Добавлены `finish_reason` и `raw_usage`.
- **`llm_core/router.py`** — `ProviderRouter`: `candidates()`, `primary()`, `model_for(task)`, `complete()` через первый профиль.
- **`config/providers.json`** — `aiprime.messages` (основной; x-api-key, `max_tokens` 8192), `aiprime.chat_completions`, `local` (Qwen из бывшего `llm_model`, `gpu_lock`, `chat_template_kwargs`).
- **`analyzer/llm_client.py`** — `build_llm_client()`: при пустом `LLM_PROVIDERS` legacy-режим, как было; иначе каталог. **Починен ключ:** `api_key` по умолчанию `None`, `LLM_API_KEY` читается. На каждый вызов пишется INFO с провайдером, моделью, токенами и ценой. Пустой ответ учитывается, пишется WARNING и возвращается `""`, как раньше.
- **`analyzer/llm_local.py`** — хелпер thinking-опции. Лок остался в `llm_client.py`: существующий тест monkeypatch-ит `llm_client.LLM_LOCK_FILE`.
- **`analyzer/analyzer.py`** (только `main()`) и **`api/main.py`** — `build_llm_client(timeout=300)`. `llm_local_mode` подключается только в legacy-режиме.
- **`config/settings.json`** — удалён `llm_model` (его никто не читал, модель теперь в каталоге). Обновлён комментарий к `llm_local_mode`, и в `DEFAULT_CONFIG` тоже.
- **`.env.example`** — `LLM_PROVIDERS`, `LLM_KEY_AIPRIME`, `LLM_PROVIDERS_FILE`. Удалены `LLM_CLOUD_BASE_URL`, `LLM_CLOUD_API_KEY`, `LLM_LOCAL_BASE_URL`.
- **Тесты (+57, существующие не тронуты):** `test_llm_probe.py`, `test_llm_catalog.py`, `test_llm_messages_transport.py`, `test_llm_router.py`, `test_llm_client_catalog.py`. Покрыты критерии р.6.1: в облако не уходит `chat_template_kwargs`, лок-файл не создаётся, при `local` старое поведение, ключ из переменной по имени и не попадает в логи, смена `LLM_PROVIDERS` меняет провайдера без правки кода.
- **Документация:** `docs/12_llm_providers.md` (новая), `docs/09_config_hot_reload.md`, `docs/11_problems_learned.md` (грабли 20–22).

## Живая проба прокси (25.09, `data/llm_probe/probe-20260925-150921.md`)

| | Результат |
|---|---|
| `/messages` | 200 с `x-api-key` и с `Bearer`; `anthropic-version` не обязателен |
| `/chat/completions` | 200 с `Bearer`; **`prompt_tokens` теперь > 0** — факт «`prompt_tokens: 0`» устарел |
| Добавка входных токенов | **есть и оплачивается**: 1317 (messages) / 1964 (chat) на «reply with ok»; выгрузка расходов: `Billed Cost` = токены × цена, добавка включена |
| Пустой ответ при `max_tokens` | воспроизводится: текста нет, токены посчитаны |
| `/models` | отдаёт список: `claude-sonnet-5`, `claude-opus-5`, `claude-opus-5-5`, `claude-fable-5-1`, `claude-haiku-4-5` и др. |

## Коммиты

| Хеш | Заголовок |
|---|---|
| `b15d3f0` | `feat(tz4-i3): add standalone LLM endpoint probe` |
| `2669691` | `feat(tz4-i3): add provider catalog, two wire protocols and router to llm_core` |
| `b405939` | `fix(tz4-i3): ship llm_core in the api image` |
| `ec2ba7a` | `feat(tz4-i3): wire the provider catalog into analyzer and api` |
| `a952ef5` | `fix(tz4-i3): fill aiprime prices from the usage export` |

Процесс, до шага 2: `665ae9c` (AGENTS.md), `ac2a999` (`.claude` вне git), `ab4c554` (раздел про Codex в CLAUDE.md).

## Новые зависимости

**Нет.** `httpx`, `tenacity`, `respx` уже стояли.

## Расхождения со спекой

- Р.6.1 п.0 (один адаптер `openai_chat`) → реализованы оба, `chat_completions` и `messages` (решение 2).
- Р.6.1 набросок, р.8.2, `.env.example` (`base_url_env`, `LLM_CLOUD_BASE_URL`) → адрес в каталоге (решение 3).
- Р.6.1 п.3, р.6.1.2 п.1, р.8.1 (`llm_routes`, маршрут «задача → профиль + модель») → модели внутри профиля, облако задаёт `LLM_PROVIDERS` (решения 4–5).
- Р.6.1 п.6 (старые переменные — «запасной источник для провайдера `local`») → это отдельный legacy-режим при пустом `LLM_PROVIDERS`, а не часть профиля `local`.
- Р.6.1.1 (проба в `tools/` / скриптом) → `llm_core/probe.py`, запуск `python -m llm_core.probe`.
- Р.7 (`llm_client.py` «флаг `llm_local_mode`») → флаг работает только в legacy-режиме.

Спеку под это правит владелец (нужна v1.13).

## Что НЕ сделано / отложено

- ~~Цены aiprime~~ → вписаны по выгрузке расходов прокси за 25.09: sonnet $2/$10, opus $5/$25 за миллион (вход/выход), совпадение до цента.
- ~~Живой вызов через каталог~~ → прошёл 25.09 с `LLM_PROVIDERS=aiprime.messages` из `.env` владельца: `claude-sonnet-5` ответил, в логе `provider=aiprime.messages prompt_tokens=1315 completion_tokens=29 cost_usd=0.00292 cost_source=table`, `local_mode` выключился по профилю, `health_check` — True.
- **Прод-хост:** в `.env` владельца стоит `LLM_PROVIDERS=aiprime.messages`, значит при следующем подъёме анализатор и API пойдут в облако через каталог, а не в локальную модель.
- Маршруты `classify` / `digest` / `trend_name` к вызывающему коду не подключены. Все вызовы идут через `default`, `digest` пока задан только в каталоге. Подключение — в шаге 4.
- Автопереключение, hot-reload провайдеров, `openai_responses` — не делались (решение 6).
- `analyzer/analyzer.py` остаётся в mypy-бейзлайне: правка касалась только `main()`, после неё mypy по файлу чистый.

## Побочные находки

- **API падал на старте с И1** (`No module named 'llm_core'`) — исправлено в `b405939`, грабля 20.
- **`config/settings.json` держал `llm_local_mode: true`**, а в STATE было записано решение владельца от 13.09 «режим облачный». Теперь это неважно: облако включается через `LLM_PROVIDERS`.
- Добавка ~1300 входных токенов на вызов делает классификацию по одной статье дорогой. В шаге 4 заложить батч.

## Команды приёмки

```bash
docker compose build analyzer news-radar-api
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
docker compose run --rm --no-deps analyzer python -m mypy --strict llm_core analyzer/llm_local.py
docker compose run --rm --no-deps news-radar-api python -c "import api.main; print('ok')"
docker compose run --rm --no-deps analyzer python -m llm_core.probe --base-url https://aiprimetech.io/v1 --model claude-sonnet-5 --key-env LLM_KEY_AIPRIME --profile-name aiprime
```

**Результат прогона 25.09:** 106 passed (analyzer) · mypy `Success: no issues found in 24 source files` · strict — 15 файлов чисто · `import api.main` — ok.
