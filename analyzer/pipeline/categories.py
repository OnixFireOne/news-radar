"""Configuration of source ownership, named digests and wall-clock schedules."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import time
import logging
import sqlite3
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from analyzer.pipeline.context import CategorySpec
from analyzer.pipeline.registry import ANALYZERS, HOOKS, SELECTORS, WRITERS, EXTRAS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DigestSpec:
    name: str
    enabled: bool
    categories: tuple[str, ...]
    at: tuple[time, ...]
    tz: str


@dataclass(frozen=True)
class DigestPart:
    category: str
    result: str
    digest_id: int | None


class UnknownDigestError(ValueError):
    """The requested digest is not configured."""


def load_categories(cfg: Mapping[str, Any], has_router: bool = True) -> list[CategorySpec]:
    # Import registrations lazily so schedule-only users need no analyzer runtime.
    import analyzer.pipeline.analyzers  # noqa: F401
    import analyzer.pipeline.hooks  # noqa: F401
    import analyzer.pipeline.selectors  # noqa: F401
    import analyzer.pipeline.writers  # noqa: F401
    import analyzer.pipeline.extras  # noqa: F401

    result: list[CategorySpec] = []
    claimed: set[str] = set()
    for name, block in cfg.get("categories", {}).items():
        if not block.get("enabled", True):
            continue
        try:
            spec = CategorySpec(
                name=name, analyzer=block["analyzer"], hooks=tuple(block.get("hooks", [])),
                select=block["select"], template=block["template"],
                extras=tuple(block.get("extras", [])), params=block.get("params", {}),
                sources=tuple(block.get("sources", [])),
            )
            ANALYZERS.get(spec.analyzer)
            SELECTORS.get(spec.select)
            WRITERS.get(spec.template)
            for hook in spec.hooks:
                HOOKS.get(hook)
            for extra in spec.extras:
                EXTRAS.get(extra)
        except (KeyError, TypeError) as exc:
            logger.error("Invalid category %s: %s", name, exc)
            continue
        sources: list[str] = []
        for source in spec.sources:
            if source in claimed:
                logger.warning("Source type %s claimed twice; first category wins", source)
            else:
                claimed.add(source)
                sources.append(source)
        if spec.analyzer == "ai_value" and not has_router:
            logger.warning("Category %s requires a catalog router; skipped for this cycle", name)
            continue
        # Empty sources are reserved for legacy; never broaden a duplicate-only category.
        if not sources:
            logger.warning("Category %s has no unclaimed sources; skipped", name)
            continue
        if "trends" in spec.hooks and "embeddings" not in spec.hooks:
            logger.warning("Category %s uses trends without embeddings; it gets no trend clusters", name)
        result.append(replace(spec, sources=tuple(sources)))
    return result


def load_digests(cfg: Mapping[str, Any]) -> list[DigestSpec]:
    result: list[DigestSpec] = []
    names: set[str] = set()
    for block in cfg.get("digests", []):
        name = str(block["name"])
        if name in names:
            raise ValueError(f"Duplicate digest name: {name}")
        names.add(name)
        tz = str(block.get("tz", "Europe/Moscow"))
        ZoneInfo(tz)
        slots = tuple(time.fromisoformat(value) for value in block.get("at", []))
        if any(slot.tzinfo is not None for slot in slots):
            raise ValueError(f"Digest {name}: at must be local times without offsets")
        result.append(DigestSpec(name, bool(block.get("enabled", True)),
                                 tuple(block.get("categories", [])), slots, tz))
    return result


def resolve_digest(cfg: Mapping[str, Any], name: str | None = None) -> DigestSpec | None:
    digests = load_digests(cfg)
    if name is not None:
        for digest in digests:
            if digest.name == name:
                return digest
        raise UnknownDigestError(f"Unknown digest: {name}")
    return next((digest for digest in digests if digest.enabled), None)


def schedule_slots(digests: Sequence[DigestSpec]) -> list[tuple[str, time]]:
    return list(dict.fromkeys(
        (digest.name, slot.replace(tzinfo=ZoneInfo(digest.tz)))
        for digest in digests if digest.enabled for slot in digest.at
    ))


def warn_uncategorized_sources(conn: sqlite3.Connection, categories: Sequence[CategorySpec]) -> None:
    known = {source for category in categories for source in category.sources}
    for row in conn.execute("SELECT DISTINCT type FROM sources"):
        if row[0] not in known:
            logger.warning("Source type %s has no enabled category", row[0])
