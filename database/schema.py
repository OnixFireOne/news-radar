"""
Database schema and initialization for News Radar.
SQLite for now — easy to migrate to PostgreSQL later.
"""

import sqlite3
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA = """
-- Migration tracking: prevents re-running migrations on every startup
CREATE TABLE IF NOT EXISTS schema_migrations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Data sources (Telegram channels, Discord servers, Twitter accounts, etc.)
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    type            TEXT NOT NULL DEFAULT 'telegram',
    name            TEXT NOT NULL,              -- @username or channel_id
    display_name    TEXT,                        -- human-readable name
    url             TEXT,
    active          INTEGER DEFAULT 1,
    -- Channel metadata (fetched periodically, used for channel rating)
    subscribers     INTEGER DEFAULT 0,           -- subscriber count
    description     TEXT,                        -- channel bio/description
    verified        INTEGER DEFAULT 0,           -- Telegram verified badge
    scam            INTEGER DEFAULT 0,           -- Telegram scam flag
    linked_chat_id  INTEGER,                     -- discussion group chat_id
    channel_created DATETIME,                    -- when the channel was created
    meta_updated    DATETIME,                    -- when we last fetched this metadata
    -- Phase 5 metrics (OpenClaw / Source Reliability)
    originator_count INTEGER DEFAULT 0,          -- how many times they posted a news first
    copier_count     INTEGER DEFAULT 0,          -- how many times they copied a news
    reliability_score REAL DEFAULT 1.0,          -- computed weight multiplier
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Raw messages collected from sources
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    external_id     TEXT NOT NULL,               -- original message ID in source
    text            TEXT NOT NULL,
    media_type      TEXT,                        -- photo, video, document, or null

    -- Engagement metrics (for channel rating and trend scoring)
    views           INTEGER DEFAULT 0,           -- view count
    forwards        INTEGER DEFAULT 0,           -- how many times forwarded
    reactions_count INTEGER DEFAULT 0,           -- total reactions (all types)
    reactions_json  TEXT,                        -- JSON: {"👍": 12, "🔥": 5, ...}
    replies_count   INTEGER DEFAULT 0,           -- number of comments/replies

    -- Authorship
    post_author     TEXT,                        -- editor signature (multi-author channels)

    -- Forward tracking (for deduplication & origin analysis)
    forward_from_channel TEXT,                   -- original channel @name or id
    forward_from_msg_id  INTEGER,                -- original message id in source channel

    -- Post lifecycle
    edit_date       DATETIME,                    -- last edit timestamp (NULL = never edited)
    collected_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    analyzed        INTEGER DEFAULT 0,           -- 0 = pending, 1 = done
    chroma_synced   INTEGER DEFAULT 0,           -- 0 = not in ChromaDB, 1 = synced
    in_digest       INTEGER DEFAULT 0,           -- 1 = already included in a digest
    UNIQUE(source_id, external_id)               -- prevent duplicates
);

-- AI analysis results per message
CREATE TABLE IF NOT EXISTS analysis (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   INTEGER NOT NULL REFERENCES messages(id),
    temperature  REAL,                   -- 1.0-10.0: hype level
    topic        TEXT,                   -- cluster/category name
    summary      TEXT,                   -- 2-3 sentence summary
    keywords     TEXT,                   -- JSON array ["btc", "pump", ...]
    sentiment    TEXT,                   -- positive / negative / neutral
    analyzed_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Digest snapshots sent to Telegram bot
CREATE TABLE IF NOT EXISTS digests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    content_md   TEXT NOT NULL,          -- Markdown digest text
    parse_mode   TEXT DEFAULT 'Markdown', -- Telegram parse_mode: Markdown | MarkdownV2
    period_start DATETIME NOT NULL,
    period_end   DATETIME NOT NULL,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    sent_telegram INTEGER DEFAULT 0
);

-- User topic subscriptions (for /track command)
CREATE TABLE IF NOT EXISTS subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    query           TEXT NOT NULL,
    last_notified_at DATETIME,
    active          INTEGER DEFAULT 1,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, query)
);

-- Channel rating: rolling stats per source
CREATE TABLE IF NOT EXISTS source_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    period_date     DATE NOT NULL,               -- YYYY-MM-DD (daily rollup)
    message_count   INTEGER DEFAULT 0,           -- posts published that day
    avg_views       REAL DEFAULT 0,              -- average views/post
    avg_forwards    REAL DEFAULT 0,              -- average forwards/post
    avg_reactions   REAL DEFAULT 0,              -- average reactions/post
    avg_replies     REAL DEFAULT 0,              -- average replies/post
    engagement_rate REAL DEFAULT 0,
    unique_topics   INTEGER DEFAULT 0,
    UNIQUE(source_id, period_date)
);

-- Phase 2: Trend Detection tables (populated by TrendTracker)
CREATE TABLE IF NOT EXISTS trends (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    topic          TEXT NOT NULL,
    trend_score    REAL DEFAULT 0,
    unique_sources INTEGER DEFAULT 0,
    message_count  INTEGER DEFAULT 0,
    first_seen     DATETIME,
    last_seen      DATETIME,
    velocity       REAL DEFAULT 0,
    status         TEXT DEFAULT 'emerging',
    summary        TEXT,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trend_messages (
    trend_id   INTEGER NOT NULL REFERENCES trends(id),
    message_id INTEGER NOT NULL REFERENCES messages(id),
    PRIMARY KEY (trend_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_analyzed     ON messages(analyzed);
CREATE INDEX IF NOT EXISTS idx_messages_collected_at ON messages(collected_at);
CREATE INDEX IF NOT EXISTS idx_messages_source_id    ON messages(source_id);
CREATE INDEX IF NOT EXISTS idx_messages_chroma       ON messages(chroma_synced);
CREATE INDEX IF NOT EXISTS idx_analysis_temperature  ON analysis(temperature);
CREATE INDEX IF NOT EXISTS idx_analysis_topic        ON analysis(topic);
CREATE INDEX IF NOT EXISTS idx_trends_status         ON trends(status);
CREATE INDEX IF NOT EXISTS idx_trends_score          ON trends(trend_score DESC);
CREATE INDEX IF NOT EXISTS idx_source_stats_date     ON source_stats(period_date DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_type_name ON sources(type, name);

-- Dispatch log: tracks every event sent to agent or fallback Telegram
CREATE TABLE IF NOT EXISTS dispatch_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,     -- breaking_alert, hot_trend, digest, subscription_match
    sent_to         TEXT NOT NULL,     -- 'agent' or 'fallback_telegram'
    status          TEXT NOT NULL,     -- 'ok', 'error'
    payload_preview TEXT,              -- first 300 chars of the message payload
    http_status     INTEGER,           -- HTTP response code (agent calls only)
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dispatch_log_created  ON dispatch_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dispatch_log_type     ON dispatch_log(event_type);
"""


# ─────────────────────────────────────────────────────────
# Migrations: each entry is (name, sql).
# A migration runs ONLY if its name is not in schema_migrations.
# Use IF NOT EXISTS / OR IGNORE everywhere to be safe.
# ─────────────────────────────────────────────────────────
MIGRATIONS = [
    # Initial columns added after the original deploy
    ("add_chroma_synced",
     "ALTER TABLE messages ADD COLUMN chroma_synced INTEGER DEFAULT 0"),

    ("dedup_sources",
     """DELETE FROM sources WHERE id NOT IN (
         SELECT MIN(id) FROM sources GROUP BY type, name
     )"""),

    ("add_reactions_count",
     "ALTER TABLE messages ADD COLUMN reactions_count INTEGER DEFAULT 0"),
    ("add_reactions_json",
     "ALTER TABLE messages ADD COLUMN reactions_json TEXT"),
    ("add_replies_count",
     "ALTER TABLE messages ADD COLUMN replies_count INTEGER DEFAULT 0"),
    ("add_post_author",
     "ALTER TABLE messages ADD COLUMN post_author TEXT"),
    ("add_forward_from_channel",
     "ALTER TABLE messages ADD COLUMN forward_from_channel TEXT"),
    ("add_forward_from_msg_id",
     "ALTER TABLE messages ADD COLUMN forward_from_msg_id INTEGER"),
    ("add_edit_date",
     "ALTER TABLE messages ADD COLUMN edit_date DATETIME"),

    ("add_source_subscribers",
     "ALTER TABLE sources ADD COLUMN subscribers INTEGER DEFAULT 0"),
    ("add_source_description",
     "ALTER TABLE sources ADD COLUMN description TEXT"),
    ("add_source_verified",
     "ALTER TABLE sources ADD COLUMN verified INTEGER DEFAULT 0"),
    ("add_source_scam",
     "ALTER TABLE sources ADD COLUMN scam INTEGER DEFAULT 0"),
    ("add_source_linked_chat_id",
     "ALTER TABLE sources ADD COLUMN linked_chat_id INTEGER"),
    ("add_source_channel_created",
     "ALTER TABLE sources ADD COLUMN channel_created DATETIME"),
    ("add_source_meta_updated",
     "ALTER TABLE sources ADD COLUMN meta_updated DATETIME"),
    ("add_source_originator_count",
     "ALTER TABLE sources ADD COLUMN originator_count INTEGER DEFAULT 0"),
    ("add_source_copier_count",
     "ALTER TABLE sources ADD COLUMN copier_count INTEGER DEFAULT 0"),
    ("add_source_reliability_score",
     "ALTER TABLE sources ADD COLUMN reliability_score REAL DEFAULT 1.0"),
    ("add_message_in_digest",
     "ALTER TABLE messages ADD COLUMN in_digest INTEGER DEFAULT 0"),
    ("add_trends_alerted_at",
     "ALTER TABLE trends ADD COLUMN alerted_at DATETIME DEFAULT NULL"),

    ("add_digest_parse_mode",
     "ALTER TABLE digests ADD COLUMN parse_mode TEXT DEFAULT 'Markdown'"),
]


def get_db(db_path: str | None = None) -> sqlite3.Connection:
    """Open and return a database connection with WAL mode and timeouts."""
    path = db_path or os.getenv("DATABASE_PATH", "/app/data/news.db")
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")  # 30s retry on lock
    return conn


def _run_migrations(conn: sqlite3.Connection) -> None:
    """
    Apply only NEW migrations (those not yet recorded in schema_migrations).
    Each migration is identified by a unique name — run exactly once ever.
    """
    for name, sql in MIGRATIONS:
        already_applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?", (name,)
        ).fetchone()

        if already_applied:
            continue  # already done — skip silently

        try:
            conn.execute(sql)
            conn.execute(
                "INSERT INTO schema_migrations (name) VALUES (?)", (name,)
            )
            conn.commit()
            logger.info(f"Migration applied: {name}")
        except Exception as e:
            err = str(e).lower()
            # Already exists — record as applied and move on
            if "duplicate column" in err or "already exists" in err:
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)", (name,)
                )
                conn.commit()
            else:
                logger.warning(f"Migration '{name}' skipped: {e}")


def init_db(db_path: str | None = None) -> None:
    """
    Initialize the database schema (idempotent, safe to call on every startup).
    Creates all tables + indexes, then runs any pending migrations (once each).
    """
    path = db_path or os.getenv("DATABASE_PATH", "/app/data/news.db")
    logger.info(f"Initializing database at {path}")

    conn = get_db(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        _run_migrations(conn)
        logger.info("Database schema initialized")
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db("./data/news.db")
    print("Database initialized")
