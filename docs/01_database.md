# База данных

SQLite с WAL mode. Легко мигрировать в PostgreSQL — схема идентична.

## Схема (ключевые таблицы)

```sql
-- Источники данных (Telegram каналы)
CREATE TABLE sources (
    id              INTEGER PRIMARY KEY,
    type            TEXT DEFAULT 'telegram',  -- telegram | discord | twitter
    name            TEXT NOT NULL,            -- @username или channel_id
    display_name    TEXT,

    -- Telegram metadata (обновляется при каждом sync)
    subscribers     INTEGER DEFAULT 0,
    description     TEXT,
    verified        INTEGER DEFAULT 0,
    scam            INTEGER DEFAULT 0,
    linked_chat_id  INTEGER,
    channel_created DATETIME,
    meta_updated    DATETIME,

    -- Метрики первоисточника (для reliability scoring)
    originator_count INTEGER DEFAULT 0,  -- сколько раз первым опубликовал новость
    copier_count     INTEGER DEFAULT 0,  -- сколько раз скопировал чужое
    reliability_score REAL DEFAULT 1.0,
    created_at      DATETIME
);

-- Сообщения
CREATE TABLE messages (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER REFERENCES sources(id),
    external_id     TEXT NOT NULL,          -- ID оригинального сообщения в канале
    text            TEXT NOT NULL,
    media_type      TEXT,                   -- photo, video, document

    -- Engagement метрики
    views           INTEGER DEFAULT 0,
    forwards        INTEGER DEFAULT 0,
    reactions_count INTEGER DEFAULT 0,
    reactions_json  TEXT,                   -- {"👍": 12, "🔥": 5}
    replies_count   INTEGER DEFAULT 0,

    -- Forward tracking
    forward_from_channel TEXT,              -- оригинальный канал (для дедупликации)
    forward_from_msg_id  INTEGER,

    -- Post lifecycle
    edit_date       DATETIME,               -- NULL = никогда не редактилось
    collected_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    analyzed        INTEGER DEFAULT 0,      -- 0=pending, 1=done
    chroma_synced   INTEGER DEFAULT 0,      -- 0=нет в ChromaDB, 1=есть
    in_digest       INTEGER DEFAULT 0,      -- 0=free, 1=included, 2=pending agent digest
    is_ad           INTEGER DEFAULT 0,      -- 1=реклама (исключается из дайджеста)
    alerted_at      DATETIME,              -- когда отправлен alert
    UNIQUE(source_id, external_id)
);

-- AI-анализ (результат LLM)
CREATE TABLE analysis (
    id           INTEGER PRIMARY KEY,
    message_id   INTEGER REFERENCES messages(id),
    temperature  REAL,                      -- 1-10: hype level
    topic        TEXT,                      -- bitcoin, defi, hack/scam...
    summary      TEXT,                      -- 2-3 предложения
    keywords     TEXT,                      -- JSON array
    sentiment    TEXT,                      -- positive | negative | neutral
    analyzed_at  DATETIME
);

-- Дайджесты
CREATE TABLE digests (
    id           INTEGER PRIMARY KEY,
    content_md   TEXT NOT NULL,
    parse_mode   TEXT DEFAULT 'Markdown',   -- Markdown | MarkdownV2 | HTML
    period_start DATETIME NOT NULL,
    period_end   DATETIME NOT NULL,
    created_at   DATETIME,
    sent_telegram INTEGER DEFAULT 0
);

-- Тренды (Phrase 2)
CREATE TABLE trends (
    id             INTEGER PRIMARY KEY,
    topic          TEXT NOT NULL,           -- имя тренда (от LLM или placeholder)
    trend_score    REAL DEFAULT 0,           -- unique_sources × avg_temp × recency × views
    unique_sources INTEGER DEFAULT 0,
    message_count  INTEGER DEFAULT 0,
    first_seen     DATETIME,
    last_seen      DATETIME,
    velocity       REAL DEFAULT 0,           -- posts per hour
    status         TEXT DEFAULT 'emerging', -- emerging | hot | cooling | dead
    summary        TEXT,                     -- LLM summary
    alerted_at     DATETIME,                -- когда отправлен hot_trend alert
    created_at     DATETIME,
    updated_at     DATETIME
);

CREATE TABLE trend_messages (
    trend_id   INTEGER REFERENCES trends(id),
    message_id INTEGER REFERENCES messages(id),
    PRIMARY KEY (trend_id, message_id)
);

-- Подписки пользователей (/track)
CREATE TABLE subscriptions (
    id              INTEGER PRIMARY KEY,
    user_id         TEXT NOT NULL,
    query           TEXT NOT NULL,
    last_notified_at DATETIME,
    active          INTEGER DEFAULT 1,
    UNIQUE(user_id, query)
);

-- Лог всех dispatch-событий (для отладки routing)
CREATE TABLE dispatch_log (
    id              INTEGER PRIMARY KEY,
    event_type      TEXT,   -- breaking_alert, hot_trend, digest, subscription_match
    sent_to         TEXT,   -- agent | fallback_telegram
    status          TEXT,   -- ok | error
    payload_preview TEXT,   -- первые 300 символов
    http_status     INTEGER,
    created_at      DATETIME
);
```

## Миграции

Каждая миграция — пара `(name, sql)`. Выполняется ровно один раз:

```python
MIGRATIONS = [
    ("add_chroma_synced",    "ALTER TABLE messages ADD COLUMN chroma_synced INTEGER DEFAULT 0"),
    ("add_reactions_count",   "ALTER TABLE messages ADD COLUMN reactions_count INTEGER DEFAULT 0"),
    ("add_forward_from_channel", "ALTER TABLE messages ADD COLUMN forward_from_channel TEXT"),
    ("add_message_in_digest", "ALTER TABLE messages ADD COLUMN in_digest INTEGER DEFAULT 0"),
    ("add_message_is_ad",    "ALTER TABLE messages ADD COLUMN is_ad INTEGER DEFAULT 0"),
    # ... и т.д.
]

def _run_migrations(conn):
    for name, sql in MIGRATIONS:
        if conn.execute("SELECT 1 FROM schema_migrations WHERE name=?", (name,)).fetchone():
            continue  # уже применена
        try:
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (name,))
            conn.commit()
        except Exception as e:
            if "duplicate column" in str(e).lower():
                # колонка уже есть — записываем миграцию как применённую
                conn.execute("INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)", (name,))
                conn.commit()
            else:
                logger.warning(f"Migration '{name}' skipped: {e}")
```

**Проблема**, которую это решает: ALTER TABLE на боевой БД с миллионами строк может падать. Миграция помечается как применённая даже если колонка уже существует — безопасно при перезапуске.

## Инициализация

```python
def init_db(db_path: str | None = None):
    conn = get_db(db_path)
    conn.executescript(SCHEMA)          # CREATE TABLE IF NOT EXISTS
    _run_migrations(conn)                # apply pending migrations
    conn.close()

def get_db(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or os.getenv("DATABASE_PATH", "/app/data/news.db")
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn
```

**Ключевые решения:**
- `check_same_thread=False` — SQLite из нескольких async потоков
- `busy_timeout=30000` — 30 секунд retry при lock (бывает при одновременном доступе collector + analyzer)
- `WAL mode` — reader не блокирует writer и наоборот
- `row_factory = sqlite3.Row` — доступ по ключу `row["temperature"]`, не только по индексу