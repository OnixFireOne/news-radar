# 13. Деплой на VPS (только статьи)

Сервер: `ssh root@serv` (алиас `cts serv`), Ubuntu 24.04, 2 CPU, 1.9 ГБ RAM, общий с inp и VPN.
Каталог: `/opt/news-radar`. На сервере работает только конвейер статей: rss / hackernews / devto →
`ai_value`-дайджест → Telegram, Neuronavt, база знаний в GitHub. Крипта (Telegram-юзербот, эмбеддинги,
ChromaDB, тренды) сюда не переносится — для неё нужен другой сервер.

## Что запускается

`docker-compose.server.yml` — отдельный файл, не дополнение к `docker-compose.yml`:

| Сервис | Образ | Память |
|---|---|---|
| `collector-feeds` | `collectors/Dockerfile` | 250m |
| `analyzer` | `deploy/lite.Dockerfile` (~230 МБ, без torch/chromadb/hdbscan) | 300m |
| `news-radar-api` | тот же облегчённый образ, `uvicorn` | 300m |
| `bot` | `bot/Dockerfile` | 150m |

- Портов наружу нет; бот ходит в API по внутренней сети `http://news-radar-api:8000`.
- Код запекается в образы при сборке; монтируются только `./data` (одна `news.db`) и `./config`.
- Логи ротируются (`json-file`, 10 МБ × 3).
- Облегчённый образ работает, потому что torch / sentence-transformers / chromadb / numpy / hdbscan
  импортируются лениво и нужны только хукам крипты (`embeddings`, `trends`). У `articles` `hooks: []`.
  `/search`, `/similar`, `/duplicates` в API на сервере не работают, `/health` показывает `chroma: unavailable`.
- Тесты и mypy по-прежнему гоняются в локальных полных образах (`docker-compose.yml`).

## Выкладка кода

Источник — ветка `main`. С Mac из корня репо:

```bash
git archive main | ssh root@serv "mkdir -p /opt/news-radar && tar -x -C /opt/news-radar"
ssh root@serv "cd /opt/news-radar && docker compose -f docker-compose.server.yml up -d --build"
```

`config/settings.json` приходит из репо и перезаписывает серверный — конфиг правится в репо, не на сервере.
`.env` в архив не входит; владелец кладёт его сам: `scp .env root@serv:/opt/news-radar/.env`.

## Перенос базы (разово)

Локальный стек сначала гасится: два бота с одним токеном конфликтуют (polling).

```bash
docker compose stop bot analyzer news-radar-api collector-feeds
sqlite3 data/news.db "PRAGMA integrity_check; VACUUM INTO '/tmp/news.db'"
scp /tmp/news.db root@serv:/opt/news-radar/data/news.db
```

## Проверка и откат

```bash
ssh root@serv "cd /opt/news-radar && docker compose -f docker-compose.server.yml ps && docker compose -f docker-compose.server.yml logs --tail 30"
```

Откат: `docker compose -f docker-compose.server.yml down` на сервере, затем вернуть `news.db` с сервера
(`scp root@serv:/opt/news-radar/data/news.db data/`) и `docker compose up -d` локально.
