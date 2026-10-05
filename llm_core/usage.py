"""Per-call token usage tracking."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class UsageRecord:
    """One LLM call's token accounting."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    call_kind: str  # free-form label the caller assigns, e.g. "complete", "complete_json"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str | None = None
    cost_usd: float | None = None
    cost_source: str | None = None
    task: str | None = None
    category: str | None = None


class UsageTracker:
    """In-memory accumulator of UsageRecord entries for later reporting."""

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []
        self._listeners: list[Callable[[UsageRecord], None]] = []

    def add_listener(self, listener: Callable[[UsageRecord], None]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def record(self, entry: UsageRecord) -> None:
        self._records.append(entry)
        for listener in tuple(self._listeners):
            try:
                listener(entry)
            except Exception:
                logging.getLogger(__name__).warning("Usage listener failed", exc_info=True)

    def all(self) -> list[UsageRecord]:
        return list(self._records)

    def clear(self) -> None:
        self._records.clear()
