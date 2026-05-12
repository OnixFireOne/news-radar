# Архитектура News Radar

## Общая схема

```
┌──────────────────────────────────────────────────────────────────┐
│                        Telegram Channels                         │
│                    (57+ каналов по папке Ton/DeFi)              │
└────────────────────────────┬─────────────────────────────────────┘
                             │ Telethon userbot (real-time + catchup)
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                        COLLECTOR                                  │
│   - Telethon session (data/sessions/news_radar.session)          │
│   - Smart catchup: unread messages на старте                      │
│   - Real-time listener: новые посты сразу в DB                    │
│   - Mark as read после сохранения                                 │
│   - Metadata sync: subscribers, verified, scam                    │
└────────────────────────────┬─────────────────────────────────────┘
                             │ SQLite WAL (data/news.db)
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                         DATABASE (SQLite)                         │
│  sources │ messages │ analysis │ digests │ trends                │
│  subscriptions │ source_stats │ dispatch_log                     │
└────────────────────────────┬─────────────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                              ▼
┌──────────────────────┐       ┌──────────────────────┐
│      ANALYZER         │       │        API           │
│  (AI processing)      │       │   (FastAPI :8000)    │
│                       │       │                      │
│  - LLM analysis (Qwen3)│       │  /feed /search       │
│  - BGE-m3 embeddings  │       │  /trends /digest     │
│  - ChromaDB dedup     │       │  /stats /settings    │
│  - TrendTracker       │       │                      │
│  - Digest generation  │       │  ┌──────────────┐    │
│                       │       │  │ Telegram Bot │    │
│  ┌──────────────────┐ │       │  │  (outbound)  │    │
│  │   ChromaDB       │ │       │  └──────────────┘    │
│  │ (vector store)   │ │       │                      │
│  └──────────────────┘ │       └──────────────────────┘
└──────────────────────┘
```

## Сервисы

| Сервис | Dockerfile | Роль |
|--------|-----------|------|
| `collector` | `collectors/Dockerfile` | Telegram userbot — читает каналы |
| `analyzer` | `analyzer/Dockerfile` | AI pipeline — анализ, эмбеддинги, тренды |
| `news-radar-api` | `api/Dockerfile` | FastAPI — все запросы от бота и фронтенда |
| `bot` | `bot/Dockerfile` | Telegram Bot — команды, auto-digest |
| `chromadb` | — | ChromaDB — векторное хранилище |

## Поток данных

```
Новое сообщение в канале
        │
        ▼
  Collector (Telethon)
  - Парсит metadata (views, reactions, forward origin)
  - Upsert в sources + messages
  - Mark as read (send_read_acknowledge)
        │
        ▼
  Analyzer (каждые 30 мин)
  1. Приоритизация: views DESC, length DESC
  2. Heuristic ad filter (до LLM)
  3. Семантический dedup через ChromaDB (если уже есть похожее)
  4. LLM analysis (temperature, topic, summary, keywords, sentiment)
  5. BGE-m3 embedding → ChromaDB
  6. Если temperature ≥ 9 → instant alert (Telegram или OpenClaw)
  7. Если есть подписки пользователя → subscription match
        │
        ▼
  TrendTracker (каждые 15 мин или после 20 обработанных)
  - HDBSCAN-кластеризация эмбеддингов
  - TrendScore = unique_sources × avg_temp × recency × log(views)
  - Если unique_sources ≥ 5 → hot_trend alert
  - LLM naming + summary для топ-N кластеров
        │
        ▼
  Digest (по cron или по команде /digest new)
  - 4-tier priority queue: alerts → trends → high-temp → fill
  - Semantic dedup (ChromaDB cosine similarity ≥ 0.85)
  - Cross-digest dedup (против предыдущих 2 дайджестов)
  - Emotional balance: если прошлый дайджест был негативным — разбавить
  - LLM генерация (classic или spoiler template)
  - Сохранение в digests table
  - Отправка в Telegram
```

## Режим Agent Mode

```
route_via_openclaw = true
        │
        ▼
  Analyzer отправляет события в OpenClaw вместо прямого Telegram:
  - breaking_alert → OpenClaw → /alerts
  - hot_trend     → OpenClaw → /alerts
  - digest        → OpenClaw → /digest/queue
  - subscription_match → OpenClaw

  OpenClaw (внешний AI-агент) обрабатывает и отправляет результат
  обратно в news-radar через API endpoints.
```

## Volume mapping

Все сервисы разделяют:
- `data/` — SQLite БД, сессии, кэш моделей
- `config/` — settings.json, topics.json

Критично: `data/models/` кэширует BGE-m3 (~570MB) между пересборками analyzer.