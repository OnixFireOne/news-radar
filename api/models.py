"""
API Models — Pydantic schemas for FastAPI responses.
Defines the shape of data returned to web clients and the Telegram bot.

Phase 1 additions:
  SearchResult      — one result from /search?q=
  SimilarResult     — one result from /similar?id=
  DuplicateGroup    — a group of near-identical messages from /duplicates
  TrendResponse     — a detected trend from /trends (Phase 2, schema ready)
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class SourceResponse(BaseModel):
    id: int
    type: str
    name: str
    display_name: Optional[str]
    active: bool


class AnalysisResponse(BaseModel):
    temperature: Optional[float]
    topic: Optional[str]
    summary: Optional[str]
    keywords: list[str]
    sentiment: Optional[str]


class MessageResponse(BaseModel):
    id: int
    source_name: str
    source_display_name: Optional[str]
    text: str
    views: int
    forwards: int
    collected_at: datetime
    analyzed: bool
    analysis: Optional[AnalysisResponse] = None


class TopicResponse(BaseModel):
    topic: str
    message_count: int
    avg_temperature: float
    max_temperature: float
    top_message: Optional[str]


class DigestResponse(BaseModel):
    id: int
    content_md: str
    period_start: datetime
    period_end: datetime
    created_at: datetime


class StatsResponse(BaseModel):
    total_messages: int
    analyzed_messages: int
    pending_messages: int
    total_sources: int
    active_sources: int
    latest_digest: Optional[datetime]
    messages_last_hour: int
    messages_last_24h: int
    chroma_documents: Optional[int] = None   # Phase 1: how many embeddings stored


# ──────────────────────────────────────────────
# Phase 1: Semantic Search models
# ──────────────────────────────────────────────

class SearchResult(BaseModel):
    """One result from GET /search?q=..."""
    message_id: int
    source_name: str
    topic: str
    temperature: float
    similarity: float       # 0.0-1.0, higher = more similar to query
    text_preview: str       # first 300 chars of the message
    collected_at: Optional[datetime] = None


class SimilarResult(BaseModel):
    """One result from GET /similar?id=..."""
    message_id: int
    source_name: str
    topic: str
    temperature: float
    similarity: float
    text_preview: str
    collected_at: Optional[datetime] = None


class DuplicateGroup(BaseModel):
    """A cluster of near-identical messages from GET /duplicates"""
    message_ids: list[int]
    sources: list[str]       # which channels reported this
    representative_text: str  # first message in the group
    size: int                 # how many duplicates


# ──────────────────────────────────────────────
# Phase 2 preview: Trend model (table ready, endpoint in Phase 2)
# ──────────────────────────────────────────────

class TrendResponse(BaseModel):
    """A detected multi-channel trend. Populated by TrendTracker in Phase 2."""
    id: int
    topic: str
    trend_score: float
    unique_sources: int     # KEY metric: how many different channels wrote about it
    message_count: int
    velocity: float         # posts/hour
    status: str             # emerging | hot | cooling | dead
    summary: Optional[str]
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]
