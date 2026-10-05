"""Category routing, validation and source isolation."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

import analyzer.analyzer as module
from analyzer.pipeline.categories import load_categories, warn_uncategorized_sources
from analyzer.pipeline.context import AnalysisResult, AnalyzeContext, Row
from analyzer.pipeline.registry import ANALYZERS
from analyzer.trend_tracker import TrendTracker
from config.config_watcher import DEFAULT_CONFIG
from database.schema import get_db
from tests.test_pipeline_characterization import _setup, _message


def categories() -> dict[str, Any]:
    return {
        "articles": {"enabled": True, "sources": ["rss", "hackernews"], "analyzer": "ai_value",
                     "hooks": [], "select": "quotas", "template": "ai_value", "extras": []},
        "crypto": {"enabled": True, "sources": ["telegram"], "analyzer": "crypto",
                   "hooks": ["alerts", "trends", "subscriptions"], "select": "tiers",
                   "template": "classic", "extras": []},
    }


def seed_sources(path: str) -> None:
    conn = get_db(path)
    conn.executemany("INSERT INTO sources (id, type, name) VALUES (?, ?, ?)",
                     [(2, "rss", "feed"), (3, "hackernews", "hn"), (4, "unknown", "other")])
    conn.commit()
    conn.close()


def message(path: str, mid: int, source: int, *, analyzed: int = 0, age_hours: int = 0) -> None:
    _message(path, mid, f"MATCH article {mid}", f"topic{mid}", 9, analyzed=analyzed, age_hours=age_hours)
    conn = get_db(path)
    conn.execute("UPDATE messages SET source_id=?, views=?, chroma_synced=? WHERE id=?",
                 (source, 1000 if source == 1 else 0, analyzed, mid))
    if analyzed:
        conn.execute("UPDATE analysis SET content_type='tutorial', value_score=9 WHERE message_id=?", (mid,))
    conn.commit()
    conn.close()


def install_bricks(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[int]]:
    seen: dict[str, list[int]] = {"crypto": [], "ai_value": []}

    def brick(name: str) -> Any:
        async def analyze(rows: list[Row], ctx: AnalyzeContext) -> list[AnalysisResult]:
            seen[name].extend(row["id"] for row in rows)
            return [(row, {"temperature": 10, "summary": "match", "topic": "agents",
                           "keywords": "[]" if name == "ai_value" else [],
                           "content_type": "tutorial", "value_score": 9, "has_outcome": True,
                           "takeaway": "Useful", "embedding": [1.0, 0.0]}) for row in rows]
        return analyze

    for name in seen:
        monkeypatch.setitem(ANALYZERS._items, name, brick(name))
    return seen


@pytest.mark.asyncio
async def test_legacy_defaults_run_all_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert DEFAULT_CONFIG["categories"] == {} and DEFAULT_CONFIG["digests"] == []
    assert "analysis_profile" not in DEFAULT_CONFIG
    analyzer, values, _, path = _setup(tmp_path, monkeypatch)
    values.pop("analysis_profile")
    values["instant_alerts_temperature"] = False
    seed_sources(path)
    for mid in range(1, 5):
        message(path, mid, mid)
    seen = install_bricks(monkeypatch)
    assert await analyzer.analyze_pending() == 4
    assert sorted(seen["crypto"]) == [1, 2, 3, 4] and seen["ai_value"] == []


@pytest.mark.asyncio
async def test_routing_and_hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, values, _, path = _setup(tmp_path, monkeypatch)
    values["categories"] = categories()
    cast(Any, analyzer.llm).router = object()
    seed_sources(path)
    for mid in range(1, 5):
        message(path, mid, mid)
    conn = get_db(path)
    conn.execute("INSERT INTO subscriptions (user_id, query) VALUES ('user', 'match')")
    conn.commit()
    conn.close()
    alert, route = AsyncMock(), AsyncMock()
    monkeypatch.setattr(analyzer, "_send_instant_alert", alert)
    monkeypatch.setattr(analyzer, "_route_event", route)
    seen = install_bricks(monkeypatch)
    assert await analyzer.analyze_pending() == 3
    assert seen["crypto"] == [1] and sorted(seen["ai_value"]) == [2, 3]
    await asyncio.sleep(0)
    alert.assert_awaited_once()
    assert alert.call_args.args[0] == 1
    route.assert_awaited_once()
    assert route.call_args.args[1]["source"] == "channel"
    assert analyzer._pending_count() == 0


@pytest.mark.asyncio
async def test_disabled_backlog_does_not_starve_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, values, _, path = _setup(tmp_path, monkeypatch)
    values["categories"] = categories()
    values["categories"]["crypto"]["enabled"] = False
    cast(Any, analyzer.llm).router = object()
    analyzer.batch_size = 1
    seed_sources(path)
    for mid in range(1, 6):
        message(path, mid, 1)
    message(path, 6, 2)
    seen = install_bricks(monkeypatch)
    assert analyzer._pending_count() == 1
    assert await analyzer.analyze_pending() == 1
    assert seen == {"crypto": [], "ai_value": [6]}
    assert analyzer._pending_count() == 0
    conn = get_db(path)
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE analyzed=0").fetchone()[0] == 5
    conn.close()
    values["categories"]["crypto"]["enabled"] = True
    assert analyzer._pending_count() == 5
    assert await analyzer.analyze_pending() == 1
    assert len(seen["crypto"]) == 1


def test_validation_and_uncategorized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       caplog: pytest.LogCaptureFixture) -> None:
    _, _, _, path = _setup(tmp_path, monkeypatch)
    seed_sources(path)
    cfg = {"categories": categories()}
    cfg["categories"]["articles"]["sources"].append("telegram")
    loaded = load_categories(cfg)
    assert [cat.name for cat in loaded] == ["articles"]
    assert "first category wins" in caplog.text
    for field in ("analyzer", "select", "template", "hooks", "extras"):
        bad = categories()
        bad["articles"][field] = ["missing"] if field in ("hooks", "extras") else "missing"
        assert [cat.name for cat in load_categories({"categories": bad})] == ["crypto"]
    assert any(record.levelname == "ERROR" for record in caplog.records)
    assert [cat.name for cat in load_categories({"categories": categories()}, False)] == ["crypto"]
    conn = get_db(path)
    warn_uncategorized_sources(conn, loaded)
    conn.close()
    assert "Source type unknown has no enabled category" in caplog.text


def test_trend_source_filter_hot_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, values, chroma, path = _setup(tmp_path, monkeypatch)
    seed_sources(path)
    for mid in range(1, 4):
        message(path, mid, mid, analyzed=1)
    tracker = TrendTracker(path, analyzer.llm, chroma, analyzer)
    assert {row["id"] for row in tracker._fetch_recent_messages()} == {1, 2, 3}
    assert analyzer._trend_sources() is None
    values["categories"] = categories()
    tracker.source_types = analyzer._trend_sources()
    assert tracker.source_types == ("telegram",)
    assert [row["id"] for row in tracker._fetch_recent_messages()] == [1]
    values["categories"]["crypto"]["hooks"] = []
    tracker.source_types = analyzer._trend_sources()
    assert tracker.source_types == () and tracker._fetch_recent_messages() == []


@pytest.mark.asyncio
async def test_loop_skips_trends_without_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, values, _, _ = _setup(tmp_path, monkeypatch)
    values["categories"] = categories()
    values["categories"]["crypto"]["enabled"] = False
    tracker = Mock(run_cycle=AsyncMock())
    monkeypatch.setattr(module, "TrendTracker", Mock(return_value=tracker))
    monkeypatch.setattr(analyzer, "analyze_pending", AsyncMock(return_value=0))
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await analyzer.run_loop()
    tracker.run_cycle.assert_not_awaited()
