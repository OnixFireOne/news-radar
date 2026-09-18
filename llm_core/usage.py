"""Per-call token usage tracking."""

from __future__ import annotations

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


class UsageTracker:
    """In-memory accumulator of UsageRecord entries for later reporting."""

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []

    def record(self, entry: UsageRecord) -> None:
        self._records.append(entry)

    def all(self) -> list[UsageRecord]:
        return list(self._records)

    def clear(self) -> None:
        self._records.clear()
