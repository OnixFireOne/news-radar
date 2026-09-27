"""Typed data passed between pipeline bricks."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from analyzer.analyzer import NewsAnalyzer

Row = dict[str, Any]
AnalysisResult = tuple[Row, Row | None]


@dataclass
class AnalyzeContext:
    analyzer: NewsAnalyzer
    conn: sqlite3.Connection
    cfg: Mapping[str, Any]
    subs_list: list[Row]
    concurrency: int
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class DigestContext:
    analyzer: NewsAnalyzer
    cfg: Mapping[str, Any]
    rules: dict[str, Any]
    template_name: str
    template_cfg: dict[str, Any]
    digest_max: int
    min_temp: float
    since: datetime
    force: bool
    return_raw: bool
    params: Mapping[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CategorySpec:
    name: str
    analyzer: str
    hooks: tuple[str, ...]
    select: str
    template: str
    extras: tuple[str, ...]
    params: Mapping[str, Any] = field(default_factory=dict)
    sources: tuple[str, ...] = ()
