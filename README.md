# 📡 News Radar

AI-агрегатор новостей из Telegram-каналов с детекцией трендов и умными дайджестами.

Мониторит 57+ каналов в реальном времени, анализирует каждое сообщение через LLM (temperature, topic, summary), кластеризует тренды через HDBSCAN на BGE-m3 эмбеддингах и отправляет дайджесты в Telegram.

## Быстрый старт

### 1. Настройка переменных окружения

```bash
cp .env.example .env
```

Заполни `.env`:

| Переменная | Где взять | Обязательно |
|-----------|-----------|-------------|
| `TELEGRAM_API_ID` | https://my.telegram.org/apps | ✅ |
| `TELEGRAM_API_HASH` | https://my.telegram.org/apps | ✅ |
| `TELEGRAM_BOT_TOKEN` | @BotFather в Telegram | ✅ |
| `TELEGRAM_ALLOWED_USERS` | @userinfobot — узнать свой ID | ✅ |
| `LLM_BASE_URL` | URL Oobabooga/Ollama/vLLM (`http://host:5000/v1`) | ✅ |
| `LLM_MODEL` | Название модели (например `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`) | |
| `OPENCLAW_WEBHOOK_URL` | URL OpenClaw агента (если используется Agent mode) | |
| `OPENCLAW_WEBHOOK_TOKEN` | Bearer token для OpenClaw | |

### 2. ВАЖНО: авторизация userbot

Collector — это Telegram userbot (не бот), требует авторизации через SMS:

```bash
# Запустить только collector в интерактивном режиме
docker-compose run --rm collector
# Ввести номер телефона и SMS-код
# После авторизации сессия сохранится в data/sessions/
# Последующие запуски — без кода
```

### 3. Запуск всех сервисов

```bash
docker-compose up -d
```

### 4. Проверка

```bash
# Статус контейнеров
docker-compose ps

# Логи collector (должны появляться каналы и сообщения)
docker logs news-radar-collector -f

# Логи analyzer (анализ + тренды)
docker logs news-radar-analyzer -f

# API
curl http://localhost:8100/health
curl http://localhost:8100/stats
curl http://localhost:8100/trends
```

## Архитектура

```
Telegram каналы (57+, папка Ton/DeFi)
        │
        ▼
  collector (Telethon userbot)
  real-time listener + smart catchup на старте
        │
        ▼
   SQLite DB (data/news.db) — WAL mode
        │
        ├──────────────────────────────────────┐
        ▼                                      ▼
  analyzer                               FastAPI :8100
  ├── LLM analysis (Qwen3, Oobabooga)    ├── /feed /search /topics
  │   temperature 1-10, topic, summary   ├── /trends /digest
  ├── BGE-m3 embeddings → ChromaDB       ├── /settings (hot-reload)
  ├── TrendTracker (HDBSCAN, 15 мин)     └── /dispatch-log
  └── Digest generation (4-tier queue)
        │
        ▼
  Telegram Bot
  ├── /digest /hot /status /ask
  ├── /track /untrack (подписки)
  └── Auto-digest 12:00 и 20:00 MSK
```

## Порты

| Сервис | Порт | Описание |
|--------|------|----------|
| API | http://localhost:8100 | FastAPI backend |
| API Docs | http://localhost:8100/docs | Swagger UI |
| ChromaDB | http://localhost:8200 | Vector store (debug) |

## Конфигурация

Все параметры в `config/settings.json`. Изменения применяются **без перезапуска** (hot-reload через watchdog, ~3 сек).

Ключевые параметры:

```json
{
  "telegram_folder": "Ton/DeFi",
  "llm_thinking_mode": "full",
  "digest_template": "spoiler",
  "breaking_alert_min_temp": 9,
  "hot_trend_min_sources": 5,
  "trend_hdbscan_epsilon": 0.25,
  "llm_concurrency": 3,
  "route_via_openclaw": false
}
```

`route_via_openclaw: true` — Agent mode: события уходят в OpenClaw вместо прямого Telegram.

## Telegram Bot команды

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

## Структура проекта

```
news-radar/
├── collectors/          # Telegram userbot (Telethon)
│   ├── base.py          # Абстрактный BaseCollector + RawMessage
│   └── telegram.py      # TelegramCollector
├── analyzer/            # AI pipeline
│   ├── analyzer.py      # NewsAnalyzer: LLM analysis + digest generation
│   ├── trend_tracker.py # TrendTracker: HDBSCAN кластеризация
│   ├── embedder.py      # BGE-m3 (sentence-transformers)
│   ├── chroma_client.py # ChromaDB wrapper
│   ├── llm_client.py    # OpenAI-compatible LLM client
│   ├── prompts.py       # Все LLM промпты
│   └── renderer.py      # Digest template renderer (classic/spoiler)
├── api/                 # FastAPI backend
│   ├── main.py          # Все endpoints
│   └── models.py        # Pydantic response models
├── bot/                 # Telegram Bot (python-telegram-bot)
│   └── telegram_bot.py
├── database/            # SQLite schema + migrations
│   └── schema.py
├── config/              # Конфигурация
│   ├── config_watcher.py
│   ├── settings.json    # Основной конфиг (hot-reload)
│   └── topics.json      # Topic aliases (bitcoin → btc, биткоин...)
├── tests/               # pytest: DB state + agent routing
├── docs/                # Архитектурная документация
├── data/                # Данные — не в git!
│   ├── news.db          # SQLite база
│   ├── sessions/        # Telegram сессии
│   ├── chroma/          # ChromaDB векторы
│   └── models/          # BGE-m3 кэш (~570MB)
└── docker-compose.yml
```

## Как работает анализ

1. Collector сохраняет сообщение в `messages` (analyzed=0)
2. Analyzer каждые 30 мин (или при накоплении 10+ pending):
   - Приоритизирует по views DESC
   - Heuristic ad filter (без LLM, по ключевым словам)
   - Semantic dedup через ChromaDB (similarity > 0.90 → клонировать результат)
   - LLM analysis: temperature 1-10, topic, summary, sentiment, is_ad
   - BGE-m3 embedding → ChromaDB
   - Если temperature ≥ 9 → instant breaking alert
3. TrendTracker каждые 15 мин:
   - HDBSCAN кластеризация эмбеддингов за последние 2 часа
   - TrendScore = unique\_sources × avg\_temp × recency × log(views)
   - Если unique\_sources ≥ 5 → hot trend alert (один раз)
4. Digest по расписанию или `/digest new`:
   - 4-tier priority: alerts → trends → high-temp → fill
   - Semantic dedup + cross-digest dedup
   - LLM генерация (classic Markdown или spoiler HTML)

## Roadmap

- ✅ Telegram каналы + real-time collection
- ✅ LLM analysis (temperature, topic, summary)
- ✅ BGE-m3 embeddings + ChromaDB semantic search
- ✅ HDBSCAN trend detection
- ✅ Digest templates (classic / spoiler)
- ✅ Agent mode (OpenClaw routing)
- ✅ Hot-reload конфигурации
- ✅ Ad detection (heuristic + LLM)
- ⬜ Discord серверы
- ⬜ Twitter/X
- ⬜ On-chain данные
