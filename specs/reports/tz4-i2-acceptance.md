# И2 — чеклист приёмки

**Статус:** не пройдена. Код закоммичен 06.09 (`989939a`), тестирования не было.
Этот файл — сценарий следующей сессии. Открываешь новый чат словами «тестируем И2» — дальше по пунктам.

Всё в контейнере, на хост ничего не ставим.

---

## A. Статика — 4 команды

```bash
docker compose build
docker compose run --rm --no-deps analyzer python -m pytest -q
docker compose run --rm --no-deps analyzer python -m mypy
docker compose run --rm --no-deps analyzer python -m mypy --strict llm_core
```

Ожидание: все зелёные. Тестов должно стать заметно больше 26 — И2 принесла четыре файла в `tests/collector/`.
Если `build` тянет `trafilatura`/`lxml` — это нормально, первый раз долго.

## B. Регрессия прода — главное

Смысл: при выключенных флагах всё обязано работать ровно как до ТЗ #4.

1. `sources.rss.enabled` и `sources.hackernews.enabled` в `config/settings.json` = `false` (сейчас так и есть).
2. Обычный подъём: `docker compose up -d` — сервис `collector-feeds` **не должен подняться** (он под `profiles: ["feeds"]`). Проверить: `docker compose ps`.
3. Живой дайджест на текущем шаблоне (`spoiler` или `classic`) — выглядит как раньше.

## C. Живой прогон коллекторов

Фиды в `settings.json` уже заполнены (OpenAI, DeepMind, HuggingFace, Simon Willison, Habr AI, dev.to/ai), HN-запросы — `llm`, `ai agents`, `min_points: 30`. Менять ничего не нужно, только включить:

```bash
# в config/settings.json: sources.rss.enabled = true, sources.hackernews.enabled = true
docker compose --profile feeds up -d collector-feeds
docker compose logs -f collector-feeds
```

Что смотреть в логах первого цикла:

- сколько записей пришло по каждому фиду;
- сколько раз догружался полный текст (лимит — 20 за цикл, больше быть не должно);
- warning'и по битым записям фида — цикл при них обязан жить дальше;
- пустой результат `trafilatura` → сохранён сниппет и запись в лог, **без retry**.

## D. Что проверить в базе

```bash
docker compose run --rm --no-deps analyzer python - <<'EOF'
import sqlite3
c = sqlite3.connect('/app/data/news.db')
c.row_factory = sqlite3.Row
print('по типам источников:')
for r in c.execute("SELECT source_type, COUNT(*) n FROM messages GROUP BY source_type"):
    print(' ', r['source_type'], r['n'])
print('с непустым url:', c.execute("SELECT COUNT(*) FROM messages WHERE url IS NOT NULL AND url != ''").fetchone()[0])
print('дубли по url:', c.execute("SELECT COUNT(*) FROM (SELECT url FROM messages WHERE url IS NOT NULL AND url != '' GROUP BY url HAVING COUNT(*) > 1)").fetchone()[0])
print('длина текста, медиана-ish:')
for r in c.execute("SELECT source_type, MIN(LENGTH(text)) mn, AVG(LENGTH(text)) av, MAX(LENGTH(text)) mx FROM messages WHERE source_type IN ('rss','hackernews') GROUP BY source_type"):
    print(' ', r['source_type'], int(r['mn'] or 0), int(r['av'] or 0), int(r['mx'] or 0))
EOF
```

Ожидание:

- записи с `source_type` = `rss` и `hackernews` появились;
- **дублей по url — ноль** (это и есть проверка дедупа из И1);
- средняя длина текста заметно больше типичного сниппета (~200–300 символов) — значит догрузка полного текста реально работает, а не молча падает.

## E. Проверка дедупа на втором цикле

Записать общее число сообщений, дождаться второго poll-цикла (интервал `poll_minutes: 60`, либо перезапустить сервис), посчитать снова. Прирост должен быть только за счёт реально новых статей, старые URL повторно не вставляются.

## F. Что принести в чат

Не весь лог — только:

1. вывод четырёх команд из блока A (хвосты);
2. 10–20 строк логов первого poll-цикла;
3. вывод SQL-блока из D;
4. что показалось странным.

По этому я вынесу вердикт: принимаем И2 или возвращаем на доработку — и допишу результат в `specs/reports/tz4-i2.md`.

## Известные риски, на которые смотреть внимательнее

- **Habr-фид** отдаёт полный текст в RSS — проверить, что не догружаем его повторно через trafilatura впустую.
- **HN Algolia** без ключа имеет rate limit; при двух запросах (`llm`, `ai agents`) проблем быть не должно, но 429 в логах — сигнал.
- **`url` появился в `RawMessage` только в И2** — у старых телеграм-записей он пустой. Убедиться, что дедуп по URL не схлопывает записи с `url = NULL`.
