"""
API Models — Pydantic схемы для FastAPI.
Определяют что возвращает API клиентам (веб + бот).
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
