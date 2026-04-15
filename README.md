# 📡 News Radar

AI-агрегатор новостей из Telegram-каналов. Часть Плана 911.

## Быстрый старт

### 1. Настройка переменных окружения

```bash
cp .env.example .env
```

Заполни `.env`:

| Переменная | Где взять |
|-----------|-----------|
| `TELEGRAM_API_ID` | https://my.telegram.org/apps |
| `TELEGRAM_API_HASH` | https://my.telegram.org/apps |
| `TELEGRAM_BOT_TOKEN` | @BotFather в Telegram |
| `TELEGRAM_ALLOWED_USERS` | @userinfobot — узнать свой ID |
| `LLM_MODEL` | Название модели в Oobabooga UI |

### 2. ВАЖНО: авторизация userbot

Первый запуск collector потребует SMS-код от Telegram:

```bash
# Запустить только collector в интерактивном режиме
docker-compose run --rm collector
# Ввести номер телефона и SMS-код
# После авторизации сессия сохранится в data/sessions/
```

### 3. Запуск всех сервисов

```bash
docker-compose up -d
```

### 4. Проверка

```bash
# Статус контейнеров
docker-compose ps

# Логи collector (должны появляться каналы)
docker logs news-radar-collector -f

# Логи analyzer
docker logs news-radar-analyzer -f

# API
curl http://localhost:8000/stats
curl http://localhost:8000/feed
```

### 5. Веб-интерфейс (разработка)

```bash
cd web
npm install
npm run dev
# Открыть http://localhost:3000
```

## Архитектура

```
Telegram каналы
      ↓
  collector (Telethon userbot)
      ↓
   SQLite DB (data/news.db)
      ↓
  analyzer (Oobabooga LLM)
      ↓ (analysis table)
  ┌────────────┐
  │  FastAPI   │ :8000
  └─────┬──────┘
        ├── Next.js Dashboard :3000
        └── Telegram Bot (вывод)
```

## Порты

| Сервис | Порт |
|--------|------|
| API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |
| Web | http://localhost:3000 |

## Структура

```
news-radar/
├── collectors/      # Сбор данных (Telegram userbot)
├── analyzer/        # AI-анализ (LLM)
├── api/             # FastAPI backend
├── bot/             # Telegram bot (вывод)
├── web/             # Next.js dashboard
├── database/        # SQLite schema
├── data/            # Данные (БД, сессии) — не в git!
└── docker-compose.yml
```

## Масштабирование (Roadmap)

- Фаза 1 ✅ Telegram каналы
- Фаза 2 Discord серверы
- Фаза 3 LunaCrash API + Twitter/X
- Фаза 4 YouTube (субтитры)
- Фаза 5 On-chain данные
