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
-- Data sources (Telegram channels, Discord servers, Twitter accounts, etc.)
CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    type         TEXT NOT NULL DEFAULT 'telegram',
    name         TEXT NOT NULL,          -- @username or channel_id
    display_name TEXT,                   -- human-readable name
    url          TEXT,
    active       INTEGER DEFAULT 1,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Raw messages collected from sources
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES sources(id),
    external_id TEXT NOT NULL,           -- original message ID in source
    text        TEXT NOT NULL,
    media_type  TEXT,                    -- photo, video, document, or null
    views       INTEGER DEFAULT 0,
    forwards    INTEGER DEFAULT 0,
    collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    analyzed    INTEGER DEFAULT 0,       -- 0 = pending, 1 = done
    UNIQUE(source_id, external_id)       -- prevent duplicates
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
    period_start DATETIME NOT NULL,
    period_end   DATETIME NOT NULL,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    sent_telegram INTEGER DEFAULT 0
);

-- Indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_messages_analyzed ON messages(analyzed);
CREATE INDEX IF NOT EXISTS idx_messages_collected_at ON messages(collected_at);
CREATE INDEX IF NOT EXISTS idx_analysis_temperature ON analysis(temperature);
CREATE INDEX IF NOT EXISTS idx_analysis_topic ON analysis(topic);
"""


def get_db(db_path: str | None = None) -> sqlite3.Connection:
    """Open and return a database connection."""
    path = db_path or os.getenv("DATABASE_PATH", "/app/data/news.db")

    # Create parent directory if it doesn't exist
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # return dict-like rows
    conn.execute("PRAGMA journal_mode=WAL")  # better for concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | None = None) -> None:
    """Initialize the database schema (idempotent)."""
    path = db_path or os.getenv("DATABASE_PATH", "/app/data/news.db")
    logger.info(f"Initializing database at {path}")

    conn = get_db(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        logger.info("Database schema initialized")
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db("./data/news.db")
    print("Database initialized")
