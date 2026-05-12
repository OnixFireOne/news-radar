# 📡 News Radar

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat&logo=docker&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-WAL-003B57?style=flat&logo=sqlite&logoColor=white)
![ChromaDB](https://img.shields.io/badge/ChromaDB-BGE--m3-FF6B35?style=flat)
![LLM](https://img.shields.io/badge/LLM-Qwen3%2035B-7C3AED?style=flat)
![Telegram](https://img.shields.io/badge/Telegram-Userbot%20%2B%20Bot-26A5E4?style=flat&logo=telegram&logoColor=white)

AI-агрегатор новостей из Telegram-каналов с детекцией трендов и умными дайджестами.

Мониторит 57+ каналов в реальном времени, анализирует каждое сообщение через LLM (temperature, topic, summary), кластеризует тренды через HDBSCAN на BGE-m3 эмбеддингах и отправляет дайджесты в Telegram.

---

## Как выглядит результат

**Breaking Alert** (temperature ≥ 9/10):
```
🚨 Binance заморозила вывод средств после взлома на $40M (9/10)

┌─────────────────────────────────────────────────────┐
│ Binance приостановила все выводы после обнаружения  │
│ подозрительной активности. По данным on-chain       │
│ аналитиков, хакеры вывели ~$40M в ETH через         │
│ несколько миксеров. Команда подтвердила инцидент.   │
└─────────────────────────────────────────────────────┘

источник → @binance_news/12453
```

**Hot Trend Alert** (5+ независимых каналов):
```
🔥 HOT TREND

Ethereum ETF Net Inflows Record
Score: 47.3 | Channels: 8

┌─────────────────────────────────────────────────────┐
│ Спотовые ETH ETF зафиксировали рекордный приток     │
│ $340M за один день. BlackRock и Fidelity лидируют.  │
└─────────────────────────────────────────────────────┘

Источники: @coindesk @theblock @cryptopanic ...
```

**Дайджест (spoiler template)**:
```
🔥 Главное за последнее время:

🔹 Биткоин пробил $105K на фоне ETF-рекорда
    ▶ Спотовые BTC ETF зафиксировали приток $1.2B...
      источник

🔹 SEC одобрила листинг Solana ETF
    ▶ Комиссия дала зелёный свет трём провайдерам...
      источник

🔹 DeFi-протокол Euler потерял $8M из-за флэш-лоана
    ▶ Атака использовала уязвимость в логике...
      источник
```

---

## Архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│              Telegram Channels  (57+, папка Ton/DeFi)           │
└──────────────────────────┬──────────────────────────────────────┘
                           │  Telethon userbot
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  COLLECTOR                                                       │
│  ├─ real-time listener (NewMessage events)                       │
│  ├─ smart catchup на старте (unread messages only)               │
│  └─ metadata sync (subscribers, verified, scam)                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │  SQLite WAL  (data/news.db)
                           ▼
┌──────────────────────────────────────┐
│  DATABASE                            │
│  sources │ messages │ analysis       │
│  trends  │ digests  │ subscriptions  │
│  dispatch_log │ source_stats         │
└───────┬──────────────────┬───────────┘
        │                  │
        ▼                  ▼
┌───────────────┐   ┌──────────────────────────────────────────┐
│  ANALYZER     │   │  API  (FastAPI :8100)                     │
│               │   │                                           │
│  ① LLM        │   │  /feed      /search    /topics           │
│    Qwen3 35B  │   │  /trends    /digest    /similar           │
│    temp 1-10  │   │  /settings  /dispatch-log                 │
│               │   └──────────────────┬───────────────────────┘
│  ② BGE-m3     │                      │
│    embeddings │              ┌───────▼────────┐
│       ↓       │              │  TELEGRAM BOT  │
│  ③ ChromaDB   │              │                │
│    dedup      │              │  /digest  /hot │
│    search     │              │  /track   /ask │
│               │              │  auto 12 & 20h │
│  ④ HDBSCAN    │              └────────────────┘
│    trends     │
│    15 мин     │
│               │
│  ⑤ Digest     │
│    4-tier     │
│    queue      │
└───────────────┘
```

---

## Быстрый старт

### 1. Переменные окружения

```bash
cp .env.example .env
```

| Переменная | Где взять | Обязательно |
|-----------|-----------|:-----------:|
| `TELEGRAM_API_ID` | https://my.telegram.org/apps | ✅ |
| `TELEGRAM_API_HASH` | https://my.telegram.org/apps | ✅ |
| `TELEGRAM_BOT_TOKEN` | @BotFather | ✅ |
| `TELEGRAM_ALLOWED_USERS` | @userinfobot | ✅ |
| `LLM_BASE_URL` | URL Oobabooga/Ollama/vLLM (`http://host:5000/v1`) | ✅ |
| `LLM_MODEL` | Название модели (`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`) | |
| `OPENCLAW_WEBHOOK_URL` | URL OpenClaw агента (Agent mode) | |
| `OPENCLAW_WEBHOOK_TOKEN` | Bearer token для OpenClaw | |

### 2. Авторизация userbot

Collector — это Telegram **userbot** (не бот), требует авторизации через SMS один раз:

```bash
docker-compose run --rm collector
# Ввести номер телефона и SMS-код
# Сессия сохранится в data/sessions/ — последующие запуски без кода
```

### 3. Запуск

```bash
docker-compose up -d
```

### 4. Проверка

```bash
docker-compose ps

docker logs news-radar-collector -f   # каналы и входящие сообщения
docker logs news-radar-analyzer -f    # анализ + тренды

curl http://localhost:8100/health
curl http://localhost:8100/stats
curl http://localhost:8100/trends
```

---

## Конфигурация

Все параметры в `config/settings.json`. Изменения применяются **без перезапуска** (~3 сек, watchdog polling).

```json
{
  "telegram_folder":        "Ton/DeFi",
  "llm_thinking_mode":      "full",
  "llm_concurrency":        3,
  "digest_template":        "spoiler",
  "breaking_alert_min_temp": 9,
  "hot_trend_min_sources":  5,
  "trend_hdbscan_epsilon":  0.25,
  "route_via_openclaw":     false
}
```

`route_via_openclaw: true` — Agent mode: события уходят в OpenClaw вместо прямого Telegram.

---

## Telegram Bot

| Команда | Описание |
|---------|----------|
| `/hot` | Горячие тренды прямо сейчас |
| `/digest` | Последний AI дайджест |
| `/digest new` | Сгенерировать свежий дайджест |
| `/digest new 6` | Дайджест за последние 6 часов |
| `/track <topic>` | Подписаться на тему (например `/track SEC`) |
| `/untrack <topic>` | Отписаться |
| `/ask <вопрос>` | Задать вопрос AI-агенту |
| `/status` | Статистика системы |

---

## Как работает анализ

```
Новое сообщение
      │
      ├─ Heuristic ad filter ──────────────────→ is_ad=1, skip
      │  (промокод, #реклама, utm_source...)
      │
      ├─ Semantic dedup (ChromaDB)
      │  similarity > 0.90 ────────────────────→ clone AI result
      │
      ├─ LLM analysis (Qwen3)
      │  temperature 1-10 │ topic │ summary
      │  sentiment │ is_ad
      │
      ├─ BGE-m3 embedding → ChromaDB
      │
      └─ temperature ≥ 9 ──────────────────────→ 🚨 Breaking Alert


TrendTracker (каждые 15 мин)
      │
      ├─ HDBSCAN кластеризация эмбеддингов (окно 2ч)
      ├─ TrendScore = unique_sources × avg_temp × recency × log(views)
      └─ unique_sources ≥ 5 ───────────────────→ 🔥 Hot Trend Alert


Digest (12:00 и 20:00 MSK, или /digest new)
      │
      ├─ Priority queue: alerts → trends → high-temp → fill
      ├─ Semantic dedup (cosine ≥ 0.85)
      ├─ Cross-digest dedup (против последних 2 дайджестов)
      └─ LLM генерация → Telegram (classic Markdown / spoiler HTML)
```

---

## Структура проекта

```
news-radar/
├── collectors/
│   ├── base.py              # BaseCollector + RawMessage dataclass
│   └── telegram.py          # Telethon userbot
├── analyzer/
│   ├── analyzer.py          # NewsAnalyzer: pipeline + digest
│   ├── trend_tracker.py     # HDBSCAN trend detection
│   ├── embedder.py          # BGE-m3 (sentence-transformers)
│   ├── chroma_client.py     # ChromaDB wrapper
│   ├── llm_client.py        # OpenAI-compatible client + LLMLock
│   ├── prompts.py           # Все LLM промпты
│   └── renderer.py          # Digest renderer (classic / spoiler)
├── api/
│   ├── main.py              # FastAPI endpoints
│   └── models.py            # Pydantic models
├── bot/
│   └── telegram_bot.py      # python-telegram-bot
├── database/
│   └── schema.py            # SQLite schema + migrations
├── config/
│   ├── config_watcher.py    # Hot-reload (watchdog polling)
│   ├── settings.json        # Основной конфиг
│   └── topics.json          # Topic aliases (btc → bitcoin)
├── tests/                   # pytest: DB state + agent routing
├── docs/                    # Архитектурная документация (11 файлов)
└── data/                    # Не в git
    ├── news.db
    ├── sessions/
    ├── chroma/
    └── models/              # BGE-m3 кэш (~570 MB)
```

---

## Порты

| Сервис | Порт |
|--------|------|
| API | http://localhost:8100 |
| API Docs | http://localhost:8100/docs |
| ChromaDB | http://localhost:8200 |

---

## Roadmap

- ✅ Telegram real-time collection + smart catchup
- ✅ LLM analysis — temperature, topic, summary, sentiment
- ✅ BGE-m3 embeddings + ChromaDB semantic search & dedup
- ✅ HDBSCAN trend detection + TrendScore
- ✅ Digest templates — classic Markdown / spoiler HTML
- ✅ Breaking alerts + Hot trend alerts
- ✅ Agent mode (OpenClaw routing)
- ✅ Hot-reload конфигурации без рестарта
- ✅ Ad detection — heuristic + LLM `is_ad`
- ✅ Topic subscriptions (`/track`)
- ⬜ Discord серверы
- ⬜ Twitter / X
- ⬜ On-chain данные
