"""
FastAPI — основной backend API для News Radar.

Эндпоинты:
  GET /feed          — лента новостей с AI-анализом
  GET /topics        — горячие темы
  GET /digest/latest — последний дайджест
  GET /sources       — список каналов
  GET /stats         — статистика системы

Документация (автоматически): http://localhost:8000/docs
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.models import (
    MessageResponse,
    AnalysisResponse,
    TopicResponse,
    DigestResponse,
    StatsResponse,
    SourceResponse,
)
from database.schema import get_db, init_db

logger = logging.getLogger(__name__)

app = FastAPI(
    title="News Radar API",
    description="Telegram news aggregator with AI analysis",
    version="0.1.0",
)

# CORS — разрешаем фронту обращаться к API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # в проде заменить на конкретный домен
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = os.environ.get("DATABASE_PATH", "/app/data/news.db")


@app.on_event("startup")
async def startup():
    """Инициализация при старте."""
    init_db(DB_PATH)
    logger.info("✅ News Radar API started")


# ──────────────────────────────────────────────
# FEED — лента новостей
# ──────────────────────────────────────────────

@app.get("/feed", response_model=list[MessageResponse])
async def get_feed(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    min_temperature: float = Query(0.0, ge=0.0, le=10.0),
    topic: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    hours: int = Query(24, ge=1, le=168),
):
    """
    Лента новостей с AI-анализом.
    
    Фильтры:
    - min_temperature: показывать только горячие (например, 7+)
    - topic: фильтр по теме (bitcoin, defi, macro...)
    - source: фильтр по каналу (@username)
    - hours: за последние N часов
    """
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

    query = """
        SELECT 
            m.id, m.text, m.views, m.forwards, m.collected_at, m.analyzed,
            s.name as source_name, s.display_name as source_display_name,
            a.temperature, a.topic, a.summary, a.keywords, a.sentiment
        FROM messages m
        JOIN sources s ON m.source_id = s.id
        LEFT JOIN analysis a ON a.message_id = m.id
        WHERE m.collected_at >= ?
          AND (a.temperature >= ? OR a.temperature IS NULL)
    """
    params: list = [since, min_temperature]

    if topic:
        query += " AND a.topic = ?"
        params.append(topic)

    if source:
        query += " AND s.name LIKE ?"
        params.append(f"%{source}%")

    query += " ORDER BY COALESCE(a.temperature, 0) DESC, m.collected_at DESC"
    query += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    conn = get_db(DB_PATH)
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    result = []
    for row in rows:
        keywords = []
        if row["keywords"]:
            try:
                keywords = json.loads(row["keywords"])
            except Exception:
                keywords = []

        analysis = None
        if row["analyzed"] and row["temperature"] is not None:
            analysis = AnalysisResponse(
                temperature=row["temperature"],
                topic=row["topic"],
                summary=row["summary"],
                keywords=keywords,
                sentiment=row["sentiment"],
            )

        result.append(MessageResponse(
            id=row["id"],
            source_name=row["source_name"],
            source_display_name=row["source_display_name"],
            text=row["text"],
            views=row["views"] or 0,
            forwards=row["forwards"] or 0,
            collected_at=datetime.fromisoformat(row["collected_at"]),
            analyzed=bool(row["analyzed"]),
            analysis=analysis,
        ))

    return result


# ──────────────────────────────────────────────
# TOPICS — горячие темы
# ──────────────────────────────────────────────

@app.get("/topics", response_model=list[TopicResponse])
async def get_topics(
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(10, ge=1, le=50),
):
    """Горячие темы за последние N часов, отсортированные по температуре."""
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

    conn = get_db(DB_PATH)
    try:
        rows = conn.execute("""
            SELECT
                a.topic,
                COUNT(*) as message_count,
                AVG(a.temperature) as avg_temperature,
                MAX(a.temperature) as max_temperature,
                (SELECT m2.text FROM messages m2
                 JOIN analysis a2 ON a2.message_id = m2.id
                 WHERE a2.topic = a.topic AND m2.collected_at >= ?
                 ORDER BY a2.temperature DESC LIMIT 1) as top_message
            FROM analysis a
            JOIN messages m ON m.id = a.message_id
            WHERE m.collected_at >= ?
              AND a.topic IS NOT NULL
            GROUP BY a.topic
            ORDER BY avg_temperature DESC
            LIMIT ?
        """, (since, since, limit)).fetchall()
    finally:
        conn.close()

    return [
        TopicResponse(
            topic=row["topic"],
            message_count=row["message_count"],
            avg_temperature=round(row["avg_temperature"], 1),
            max_temperature=round(row["max_temperature"], 1),
            top_message=row["top_message"],
        )
        for row in rows
    ]


# ──────────────────────────────────────────────
# DIGEST
# ──────────────────────────────────────────────

@app.get("/digest/latest", response_model=DigestResponse)
async def get_latest_digest():
    """Последний сгенерированный дайджест."""
    conn = get_db(DB_PATH)
    try:
        row = conn.execute("""
            SELECT * FROM digests ORDER BY created_at DESC LIMIT 1
        """).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="No digests yet")

    return DigestResponse(
        id=row["id"],
        content_md=row["content_md"],
        period_start=datetime.fromisoformat(row["period_start"]),
        period_end=datetime.fromisoformat(row["period_end"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


@app.get("/digest", response_model=list[DigestResponse])
async def get_digests(limit: int = Query(10, ge=1, le=50)):
    """Список последних дайджестов."""
    conn = get_db(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT * FROM digests ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()

    return [
        DigestResponse(
            id=row["id"],
            content_md=row["content_md"],
            period_start=datetime.fromisoformat(row["period_start"]),
            period_end=datetime.fromisoformat(row["period_end"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
        for row in rows
    ]


# ──────────────────────────────────────────────
# SOURCES — источники
# ──────────────────────────────────────────────

@app.get("/sources", response_model=list[SourceResponse])
async def get_sources():
    """Список всех источников (каналов)."""
    conn = get_db(DB_PATH)
    try:
        rows = conn.execute("SELECT * FROM sources ORDER BY display_name").fetchall()
    finally:
        conn.close()

    return [
        SourceResponse(
            id=row["id"],
            type=row["type"],
            name=row["name"],
            display_name=row["display_name"],
            active=bool(row["active"]),
        )
        for row in rows
    ]


# ──────────────────────────────────────────────
# STATS
# ──────────────────────────────────────────────

@app.get("/stats", response_model=StatsResponse)
async def get_stats():
    """Статистика системы."""
    conn = get_db(DB_PATH)
    try:
        now = datetime.utcnow()

        total = conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"]
        analyzed = conn.execute("SELECT COUNT(*) as c FROM messages WHERE analyzed=1").fetchone()["c"]
        pending = total - analyzed

        total_sources = conn.execute("SELECT COUNT(*) as c FROM sources").fetchone()["c"]
        active_sources = conn.execute("SELECT COUNT(*) as c FROM sources WHERE active=1").fetchone()["c"]

        last_digest_row = conn.execute(
            "SELECT created_at FROM digests ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        latest_digest = datetime.fromisoformat(last_digest_row["created_at"]) if last_digest_row else None

        hour_ago = (now - timedelta(hours=1)).isoformat()
        day_ago = (now - timedelta(hours=24)).isoformat()

        msgs_1h = conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE collected_at >= ?", (hour_ago,)
        ).fetchone()["c"]
        msgs_24h = conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE collected_at >= ?", (day_ago,)
        ).fetchone()["c"]

    finally:
        conn.close()

    return StatsResponse(
        total_messages=total,
        analyzed_messages=analyzed,
        pending_messages=pending,
        total_sources=total_sources,
        active_sources=active_sources,
        latest_digest=latest_digest,
        messages_last_hour=msgs_1h,
        messages_last_24h=msgs_24h,
    )


# ──────────────────────────────────────────────
# HEALTH
# ──────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8000)
