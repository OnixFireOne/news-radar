# И2 — Коллекторы, волна 1 · отчёт

> Восстановлен 10.09.2026 по коммитам и переписке. Оригинальный отчёт исполнителя вёлся в чате.

**Статус:** 🟡 код готов и закоммичен, **приёмка владельцем не зафиксирована** · **Дата:** 06.09.2026

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
docker compose build
docker compose run --rm --no-deps analyzer python -m pytest -q
docker compose run --rm --no-deps analyzer python -m mypy
```

Живая проверка: заполнить `sources.rss.feeds` в `config/settings.json` (стартовый список — раздел 2 ТЗ), затем

```bash
docker compose --profile feeds up -d collector-feeds
docker compose logs -f collector-feeds
```
