"""Application-owned SQLite usage sink and async-local category attribution."""
from __future__ import annotations

from contextvars import ContextVar
from datetime import timezone
from pathlib import Path
from collections.abc import Callable

from database.schema import get_db
from llm_core.usage import UsageRecord

usage_category: ContextVar[str | None] = ContextVar("usage_category", default=None)
_sinks: dict[str, Callable[[UsageRecord], None]] = {}


def install_usage_sink(db_path: str) -> None:
    """Install once per database and process; each write owns its connection."""
    from analyzer.llm_client import get_usage_tracker

    path = str(Path(db_path).resolve())
    if path not in _sinks:
        def write(record: UsageRecord) -> None:
            conn = get_db(path)
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO llm_usage (created_at, task, category, provider, model, "
                        "prompt_tokens, completion_tokens, cost_usd, cost_source) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (record.timestamp.astimezone(timezone.utc).isoformat(),
                         record.task or "default", record.category, record.provider, record.model,
                         record.prompt_tokens, record.completion_tokens, record.cost_usd, record.cost_source),
                    )
            finally:
                conn.close()
        _sinks[path] = write
    get_usage_tracker().add_listener(_sinks[path])
