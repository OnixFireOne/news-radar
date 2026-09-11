# И2 — чеклист приёмки

**Статус:** прогон 11.09.2026 — ❌ возврат на доработку И2.1 (вердикт в `tz4-i2.md`). И2.1 сдана и принята 11.09.2026 (все 6 критериев, детали и замечания — в разделе «И2.1» / «Приёмка И2.1» `tz4-i2.md`). Весь сценарий автоматизирован: `bash scripts/tz4_i2_check.sh` (лог `data/tz4_i2_check.log`); ниже — что он делает.
Этот файл — сценарий следующей сессии. Открываешь новый чат словами «тестируем И2» — дальше по пунктам.

Всё в контейнере, на хост ничего не ставим.

---

## A. Статика — 4 команды

```bash
docker network inspect ai-network >/dev/null 2>&1 || docker network create ai-network   # сеть external, без llm-stack её нет
docker compose --profile feeds build analyzer collector-feeds
docker compose run --rm --no-deps analyzer python -m pytest -q --ignore=tests/collector
docker compose --profile feeds run --rm --no-deps collector-feeds python -m pytest -q tests/collector
docker compose run --rm --no-deps analyzer python -m mypy
docker compose run --rm --no-deps analyzer python -m mypy --strict llm_core
```

Ожидание: все зелёные. Тестов должно стать заметно больше 26 — И2 принесла четыре файла в `tests/collector/`.
Если `build` тянет `trafilatura`/`lxml` — это нормально, первый раз долго.

## B. Регрессия прода — главное

Смысл: при выключенных флагах всё обязано работать ровно как до ТЗ #4.

1. `sources.rss.enabled` и `sources.hackernews.enabled` в `config/settings.json` = `false` (сейчас так и есть).
2. `docker compose config --services` — в списке **нет** `collector-feeds` (он под `profiles: ["feeds"]`). ⚠️ Не делать `docker compose up -d` не на прод-хосте: поднимется бот с боевым токеном (конфликт getUpdates с продом) и телеграм-коллектор без сессии.
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
# ✏️ 11.09: исправлено — в messages нет колонки source_type, тип берём из sources
docker compose run --rm --no-deps analyzer python - <<'EOF'
import sqlite3
c = sqlite3.connect('/app/data/news.db')
c.row_factory = sqlite3.Row
print('по типам источников:')
for r in c.execute("SELECT s.type t, COUNT(*) n FROM messages m JOIN sources s ON s.id = m.source_id GROUP BY s.type"):
    print(' ', r['t'], r['n'])
print('с непустым url:', c.execute("SELECT COUNT(*) FROM messages WHERE url IS NOT NULL AND url != ''").fetchone()[0])
print('дубли по url:', c.execute("SELECT COUNT(*) FROM (SELECT url FROM messages WHERE url IS NOT NULL AND url != '' GROUP BY url HAVING COUNT(*) > 1)").fetchone()[0])
print('длина текста по источникам (min/avg/max):')
for r in c.execute("SELECT s.type t, s.name nm, MIN(LENGTH(m.text)) mn, AVG(LENGTH(m.text)) av, MAX(LENGTH(m.text)) mx FROM messages m JOIN sources s ON s.id = m.source_id WHERE s.type IN ('rss','hackernews') GROUP BY s.id"):
    print(' ', r['t'], r['nm'], int(r['mn'] or 0), int(r['av'] or 0), int(r['mx'] or 0))
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
