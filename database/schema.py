"""
Database schema and initialization for News Radar.
SQLite на старте — легко мигрировать на PostgreSQL позже.
"""

import sqlite3
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA = """
-- Источники (Telegram каналы, потом Discord, Twitter и т.д.)
CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    type         TEXT NOT NULL DEFAULT 'telegram',
    name         TEXT NOT NULL,          -- @username или channel_id
    display_name TEXT,                   -- человекочитаемое название
    url          TEXT,
    active       INTEGER DEFAULT 1,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Сырые сообщения из каналов
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES sources(id),
    external_id TEXT NOT NULL,           -- ID сообщения в Telegram
    text        TEXT NOT NULL,
    media_type  TEXT,                    -- photo, video, document, null
    views       INTEGER DEFAULT 0,
    forwards    INTEGER DEFAULT 0,
    collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    analyzed    INTEGER DEFAULT 0,       -- 0 = не обработано, 1 = обработано
    UNIQUE(source_id, external_id)       -- не дублируем
);

-- AI-анализ сообщений
CREATE TABLE IF NOT EXISTS analysis (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   INTEGER NOT NULL REFERENCES messages(id),
    temperature  REAL,                   -- 1.0-10.0: хайп темы
    topic        TEXT,                   -- кластер/категория
    summary      TEXT,                   -- 2-3 предложения
    keywords     TEXT,                   -- JSON array ["btc", "pump", ...]
    sentiment    TEXT,                   -- positive / negative / neutral
    analyzed_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Дайджесты (для Telegram-бота)
CREATE TABLE IF NOT EXISTS digests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    content_md   TEXT NOT NULL,          -- Markdown текст дайджеста
    period_start DATETIME NOT NULL,
    period_end   DATETIME NOT NULL,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    sent_telegram INTEGER DEFAULT 0
);

-- Индексы для быстрых запросов
CREATE INDEX IF NOT EXISTS idx_messages_analyzed ON messages(analyzed);
CREATE INDEX IF NOT EXISTS idx_messages_collected_at ON messages(collected_at);
CREATE INDEX IF NOT EXISTS idx_analysis_temperature ON analysis(temperature);
CREATE INDEX IF NOT EXISTS idx_analysis_topic ON analysis(topic);
"""


def get_db(db_path: str | None = None) -> sqlite3.Connection:
    """Получить соединение с БД."""
    path = db_path or os.getenv("DATABASE_PATH", "/app/data/news.db")
    
    # Создаём папку если нет
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # возвращать dict-like строки
    conn.execute("PRAGMA journal_mode=WAL")  # лучше для concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | None = None) -> None:
    """Инициализировать БД с нужной схемой."""
    path = db_path or os.getenv("DATABASE_PATH", "/app/data/news.db")
    logger.info(f"Initializing database at {path}")
    
    conn = get_db(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        logger.info("Database schema initialized successfully")
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db("./data/news.db")
    print("✅ Database initialized")
