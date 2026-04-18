"""
FastAPI backend for News Radar.

Endpoints:
  GET /feed          — news feed with AI analysis
  GET /topics        — trending topics (by LLM temperature)
  GET /digest/latest — most recent digest
  GET /digest        — list of digests
  GET /sources       — list of monitored channels
  GET /stats         — system statistics
  GET /health        — health check

  Phase 1 (semantic layer):
  GET /search?q=..      — semantic search by meaning (not exact keywords)
  GET /similar?id=..    — find similar news to a given message
  GET /duplicates       — find near-duplicate messages (same news, diff channels)

  Phase 2 (trend detection):
  GET /trends           — active trends detected by TrendTracker (HDBSCAN clustering)
  GET /trends/{id}/posts — all messages belonging to a specific trend

Auto-generated docs: http://localhost:8000/docs
"""

import asyncio
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
    SearchResult,
    SimilarResult,
    DuplicateGroup,
    TrendResponse,    # Phase 2
)
from database.schema import get_db, init_db
from analyzer.chroma_client import ChromaClient
from analyzer.embedder import get_embedder
from analyzer.llm_client import LLMClient
from analyzer.analyzer import NewsAnalyzer

logger = logging.getLogger(__name__)

# Shared ChromaDB client and embedder (initialized once at startup)
# The embedder loads BGE-m3 lazily on first /search call
_chroma: ChromaClient | None = None


def get_chroma() -> ChromaClient:
    """Get or create the shared ChromaDB client."""
    global _chroma
    if _chroma is None:
        _chroma = ChromaClient()
    return _chroma

app = FastAPI(
    title="News Radar API",
    description="Telegram news aggregator with AI analysis",
    version="0.1.0",
)

# Allow frontend to call the API from any origin
# In production: replace "*" with your actual domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = os.environ.get("DATABASE_PATH", "/app/data/news.db")


@app.on_event("startup")
async def startup():
    """Initialize database on startup."""
    init_db(DB_PATH)
    logger.info("News Radar API started")


# ──────────────────────────────────────────────
# FEED
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
    News feed with AI analysis scores.

    Filters:
    - min_temperature: only show hot topics (e.g. 7+ for high hype)
    - topic: filter by category (bitcoin, defi, macro...)
    - source: filter by channel @username
    - hours: time window in hours
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
        WHERE datetime(m.collected_at) >= datetime(?)
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
# TOPICS
# ──────────────────────────────────────────────

@app.get("/topics", response_model=list[TopicResponse])
async def get_topics(
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(10, ge=1, le=50),
):
    """Trending topics sorted by average temperature."""
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
                 WHERE a2.topic = a.topic AND datetime(m2.collected_at) >= datetime(?)
                 ORDER BY a2.temperature DESC LIMIT 1) as top_message
            FROM analysis a
            JOIN messages m ON m.id = a.message_id
            WHERE datetime(m.collected_at) >= datetime(?)
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

@app.post("/digest/generate", response_model=DigestResponse)
async def generate_digest(hours: int = Query(6, ge=1, le=48)):
    """Manually trigger AI digest generation for the last N hours."""
    llm = LLMClient()
    analyzer = NewsAnalyzer(db_path=DB_PATH, llm_client=llm)
    
    digest_text = await analyzer.generate_digest(hours=hours)
    if not digest_text:
        raise HTTPException(status_code=400, detail="Could not generate digest (no news or LLM error)")

    conn = get_db(DB_PATH)
    try:
        row = conn.execute("SELECT * FROM digests ORDER BY created_at DESC LIMIT 1").fetchone()
    finally:
        conn.close()

    return DigestResponse(
        id=row["id"],
        content_md=row["content_md"],
        period_start=datetime.fromisoformat(row["period_start"]),
        period_end=datetime.fromisoformat(row["period_end"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


@app.get("/digest/latest", response_model=DigestResponse)
async def get_latest_digest():
    """Get the most recently generated digest."""
    conn = get_db(DB_PATH)
    try:
        row = conn.execute(
            "SELECT * FROM digests ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="No digests generated yet")

    return DigestResponse(
        id=row["id"],
        content_md=row["content_md"],
        period_start=datetime.fromisoformat(row["period_start"]),
        period_end=datetime.fromisoformat(row["period_end"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


@app.get("/digest", response_model=list[DigestResponse])
async def get_digests(limit: int = Query(10, ge=1, le=50)):
    """List recent digests."""
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
# SOURCES
# ──────────────────────────────────────────────

@app.get("/sources", response_model=list[SourceResponse])
async def get_sources():
    """List all monitored channels/sources."""
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
    """System-wide statistics."""
    conn = get_db(DB_PATH)
    try:
        now = datetime.utcnow()

        total = conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"]
        analyzed = conn.execute("SELECT COUNT(*) as c FROM messages WHERE analyzed=1").fetchone()["c"]

        total_sources = conn.execute("SELECT COUNT(*) as c FROM sources").fetchone()["c"]
        active_sources = conn.execute("SELECT COUNT(*) as c FROM sources WHERE active=1").fetchone()["c"]

        last_digest_row = conn.execute(
            "SELECT created_at FROM digests ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        latest_digest = datetime.fromisoformat(last_digest_row["created_at"]) if last_digest_row else None

        hour_ago = (now - timedelta(hours=1)).isoformat()
        day_ago = (now - timedelta(hours=24)).isoformat()

        msgs_1h = conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE datetime(collected_at) >= datetime(?)", (hour_ago,)
        ).fetchone()["c"]
        msgs_24h = conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE datetime(collected_at) >= datetime(?)", (day_ago,)
        ).fetchone()["c"]

    finally:
        conn.close()

    return StatsResponse(
        total_messages=total,
        analyzed_messages=analyzed,
        pending_messages=total - analyzed,
        total_sources=total_sources,
        active_sources=active_sources,
        latest_digest=latest_digest,
        messages_last_hour=msgs_1h,
        messages_last_24h=msgs_24h,
        chroma_documents=get_chroma().count if get_chroma().health_check() else None,
    )


# ──────────────────────────────────────────────
# HEALTH
# ──────────────────────────────────────────────

@app.get("/health")
async def health():
    chroma_ok = get_chroma().health_check()
    return {
        "status": "ok",
        "time": datetime.utcnow().isoformat(),
        "chroma": "ok" if chroma_ok else "unavailable",
    }


# ──────────────────────────────────────────────
# Phase 1: SEMANTIC SEARCH
# ──────────────────────────────────────────────

@app.get("/search", response_model=list[SearchResult])
async def semantic_search(
    q: str = Query(..., min_length=3, description="Search query (any language)"),
    limit: int = Query(10, ge=1, le=50),
    min_temperature: float = Query(0.0, ge=0.0, le=10.0),
):
    """
    Semantic search: find news messages similar in MEANING to the query.

    Unlike /feed which filters by exact topic match, /search understands:
    - Synonyms: "bitcoin crash" ≈ "BTC dump" ≈ "падение биткоина"
    - Cross-language: query in English, finds Russian news and vice versa
    - Concepts: "crypto regulation" finds news about SEC, MiCA, bans, etc.

    Results ordered by semantic similarity (highest first).
    """
    loop = asyncio.get_running_loop()
    embedder = get_embedder()

    # Encode query in thread pool (CPU-bound)
    query_embedding = await loop.run_in_executor(None, embedder.encode, q)

    chroma = get_chroma()
    results = chroma.search(
        query_embedding=query_embedding,
        limit=limit,
        min_temperature=min_temperature,
    )

    if not results:
        return []

    # Enrich with SQLite data (source name, collected_at)
    conn = get_db(DB_PATH)
    try:
        output = []
        for r in results:
            row = conn.execute("""
                SELECT m.text, m.collected_at, s.name as source_name
                FROM messages m JOIN sources s ON m.source_id = s.id
                WHERE m.id = ?
            """, (r["message_id"],)).fetchone()

            output.append(SearchResult(
                message_id=r["message_id"],
                source_name=row["source_name"] if row else r["source"],
                topic=r["topic"],
                temperature=r["temperature"],
                similarity=r["similarity"],
                text_preview=(row["text"][:300] if row else r["document"]),
                collected_at=datetime.fromisoformat(row["collected_at"]) if row else None,
            ))
    finally:
        conn.close()

    return output


@app.get("/similar", response_model=list[SimilarResult])
async def find_similar(
    id: int = Query(..., description="Message ID to find similar news for"),
    limit: int = Query(5, ge=1, le=20),
):
    """
    Find news messages semantically similar to a given message.

    Useful for:
    - "Other channels reporting the same story"
    - Detecting if a trend is forming around this topic
    - Finding context/background for a breaking story
    """
    chroma = get_chroma()

    try:
        results = chroma.find_similar(message_id=id, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if not results:
        return []

    conn = get_db(DB_PATH)
    try:
        output = []
        for r in results:
            row = conn.execute("""
                SELECT m.text, m.collected_at, s.name as source_name
                FROM messages m JOIN sources s ON m.source_id = s.id
                WHERE m.id = ?
            """, (r["message_id"],)).fetchone()

            output.append(SimilarResult(
                message_id=r["message_id"],
                source_name=row["source_name"] if row else r["source"],
                topic=r["topic"],
                temperature=r["temperature"],
                similarity=r["similarity"],
                text_preview=(row["text"][:300] if row else r["document"]),
                collected_at=datetime.fromisoformat(row["collected_at"]) if row else None,
            ))
    finally:
        conn.close()

    return output


@app.get("/duplicates", response_model=list[DuplicateGroup])
async def find_duplicates(
    hours: int = Query(6, ge=1, le=48, description="Time window to search for duplicates"),
    threshold: float = Query(0.92, ge=0.7, le=1.0, description="Cosine similarity threshold"),
):
    """
    Find groups of near-duplicate messages (same news across different channels).

    Use cases:
    - Digest deduplication: don't show same story N times
    - Measuring trend spread: if 10 channels post same thing, it's hot news
    - Source quality: originators vs re-posters

    threshold=0.92 → near-identical text (different wording of same fact)
    threshold=0.85 → same topic but different perspective (broader dedup)
    """
    # Get message IDs from the time window via SQLite
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    conn = get_db(DB_PATH)
    try:
        rows = conn.execute("""
            SELECT m.id, m.text, s.name as source_name
            FROM messages m
            JOIN sources s ON m.source_id = s.id
            WHERE datetime(m.collected_at) >= datetime(?)
              AND m.analyzed = 1
              AND m.chroma_synced = 1
            ORDER BY m.collected_at DESC
        """, (since,)).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    message_ids = [row["id"] for row in rows]
    id_to_row = {row["id"]: row for row in rows}

    chroma = get_chroma()
    groups = chroma.find_duplicates(message_ids=message_ids, threshold=threshold)

    output = []
    for group in groups:
        sources = [id_to_row[mid]["source_name"] for mid in group if mid in id_to_row]
        first = id_to_row.get(group[0])
        output.append(DuplicateGroup(
            message_ids=group,
            sources=sources,
            representative_text=first["text"][:300] if first else "",
            size=len(group),
        ))

    # Sort by size descending (biggest duplicate groups first)
    output.sort(key=lambda g: g.size, reverse=True)
    return output


# ──────────────────────────────────────────────
# Phase 2: TREND DETECTION
# ──────────────────────────────────────────────

@app.get("/trends", response_model=list[TrendResponse])
async def get_trends(
    hours: int = Query(24, ge=1, le=168, description="Look back window in hours"),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter by status: emerging|hot|cooling|dead"),
    min_sources: int = Query(1, ge=1, description="Minimum unique channels that reported"),
):
    """
    Active trends detected by TrendTracker (runs every 15 min in the analyzer).

    A trend = a semantic cluster of messages from MULTIPLE channels about the same story.
    Sorted by trend_score DESC (= unique_sources × avg_temperature × recency × views).

    Key field: unique_sources — how many DIFFERENT channels reported this story.
    status values: emerging → hot → cooling → dead
    """
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

    query = """
        SELECT
            id, topic, trend_score, unique_sources, message_count,
            velocity, status, summary, first_seen, last_seen
        FROM trends
        WHERE datetime(last_seen) >= datetime(?)
          AND unique_sources >= ?
    """
    params: list = [since, min_sources]

    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY trend_score DESC LIMIT ?"
    params.append(limit)

    conn = get_db(DB_PATH)
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    return [
        TrendResponse(
            id=row["id"],
            topic=row["topic"],
            trend_score=round(row["trend_score"], 2),
            unique_sources=row["unique_sources"],
            message_count=row["message_count"],
            velocity=round(row["velocity"], 2),
            status=row["status"],
            summary=row["summary"],
            first_seen=datetime.fromisoformat(row["first_seen"]) if row["first_seen"] else None,
            last_seen=datetime.fromisoformat(row["last_seen"]) if row["last_seen"] else None,
        )
        for row in rows
    ]


@app.get("/trends/{trend_id}/posts", response_model=list[MessageResponse])
async def get_trend_posts(
    trend_id: int,
    limit: int = Query(20, ge=1, le=100),
):
    """
    Get all messages belonging to a specific trend cluster.

    Useful for:
    - Showing the full picture of a trend (all channels that reported it)
    - Building a digest from a specific trend
    - Verifying that the cluster makes sense
    """
    conn = get_db(DB_PATH)
    try:
        # Verify trend exists
        trend = conn.execute(
            "SELECT id, topic FROM trends WHERE id = ?", (trend_id,)
        ).fetchone()
        if not trend:
            raise HTTPException(status_code=404, detail=f"Trend {trend_id} not found")

        rows = conn.execute("""
            SELECT
                m.id, m.text, m.views, m.forwards, m.collected_at, m.analyzed,
                s.name as source_name, s.display_name as source_display_name,
                a.temperature, a.topic, a.summary, a.keywords, a.sentiment
            FROM trend_messages tm
            JOIN messages m ON m.id = tm.message_id
            JOIN sources s ON m.source_id = s.id
            LEFT JOIN analysis a ON a.message_id = m.id
            WHERE tm.trend_id = ?
            ORDER BY a.temperature DESC, m.collected_at DESC
            LIMIT ?
        """, (trend_id, limit)).fetchall()
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


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8000)
