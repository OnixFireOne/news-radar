"""Characterize the legacy analyzer and digest paths before pipeline extraction."""

from __future__ import annotations

from contextlib import nullcontext
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import analyzer.analyzer as module
from analyzer.prompts import (
    DIGEST_PROMPT, DIGEST_PROMPT_SPOILER, DIGEST_SPOILER_MERGE_OFF,
    SYSTEM_PROMPT,
)
from analyzer.value_classifier import ClassifyOutcome, ValueVerdict
from database.schema import get_db, init_db


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[module.NewsAnalyzer, dict[str, Any], Mock, str]:
    path = str(tmp_path / "pipeline.db")
    init_db(path)
    conn = get_db(path)
    conn.execute("INSERT INTO sources (id, type, name) VALUES (1, 'telegram', 'channel')")
    conn.commit()
    conn.close()
    values: dict[str, Any] = {
        "digest_template": "classic", "digest_max_items": 7, "digest_min_temperature": 5.0,
        "digest_rules": {"dedup_threshold": 1.0, "max_per_topic": 2,
                         "always_include_alerts": True, "min_unique_sources_for_trend": 3},
        "digest_templates": {
            "classic": {"max_items": 7, "cross_dedup": False},
            "spoiler": {"max_items": 7, "cross_dedup": False, "llm_merge": False,
                        "title_max_words": 6, "summary_max_sentences": 2},
        },
        "analysis_profile": "crypto", "min_message_length": 1,
        "breaking_alert_min_temp": 9, "instant_alerts_temperature": True,
        "ad_filter": {"enabled": True, "use_heuristic": True, "heuristic_keywords": ["#ad"]},
    }
    cfg = Mock()
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    cfg.load_topics.return_value = {"canonical": {"aliases": ["raw"]}, "hack": {"alert": True}}
    chroma = Mock()
    chroma.health_check.return_value = False
    embedder = Mock()
    embedder.encode.return_value = [0.25, 0.75]
    monkeypatch.setattr(module, "ChromaClient", lambda: chroma)
    monkeypatch.setattr(module, "get_embedder", lambda: embedder)
    monkeypatch.setattr(module, "LLMLock", nullcontext)
    monkeypatch.setattr(module, "is_llm_locked", lambda: False)
    llm = Mock()
    llm.router = None
    llm.complete = AsyncMock(return_value="🔥 Главное за час\n**Новость**")
    llm.complete_json = AsyncMock(return_value={"items": [
        {"source_id": "1", "title": "Title <one>", "summary": "A & B"},
    ]})
    analyzer = module.NewsAnalyzer(path, cast(Any, llm), cfg=cast(Any, cfg))
    return analyzer, values, chroma, path


def _message(path: str, mid: int, text: str, topic: str, temp: float, *,
             age_hours: int = 0, in_digest: int = 0, is_ad: int = 0,
             analyzed: int = 1) -> None:
    stamp = (datetime.utcnow() - timedelta(hours=age_hours)).isoformat()
    conn = get_db(path)
    conn.execute(
        "INSERT INTO messages (id, source_id, external_id, text, collected_at, analyzed, in_digest, is_ad) "
        "VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
        (mid, str(mid), text, stamp, analyzed, in_digest, is_ad),
    )
    if analyzed:
        conn.execute("INSERT INTO analysis (message_id, temperature, topic, summary) VALUES (?, ?, ?, ?)",
                     (mid, temp, topic, f"Summary {mid}"))
    conn.commit()
    conn.close()


def _marks(path: str) -> dict[int, int]:
    conn = get_db(path)
    try:
        return {int(row["id"]): int(row["in_digest"])
                for row in conn.execute("SELECT id, in_digest FROM messages ORDER BY id")}
    finally:
        conn.close()


def _messages_text(rows: list[tuple[str, float, str]]) -> str:
    return "\n\n".join(
        f"[{i}] Channel: @channel | Temperature: {temp}/10\nTopic: {topic}\nText: {body}"
        for i, (body, temp, topic) in enumerate(rows, 1)
    )


@pytest.mark.asyncio
async def test_classic_tiers_prompt_and_persistence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, _values, _chroma, path = _setup(tmp_path, monkeypatch)
    items = [
        (1, "Alert", "hack", 6.0), (2, "Trend", "trend", 8.0),
        (3, "High A", "shared", 8.0), (4, "High B", "shared", 7.5),
        (5, "High C", "shared", 7.0), (6, "Fill A", "fill", 6.0),
        (7, "Fill B", "fill", 5.5), (8, "Low", "low", 4.0),
    ]
    for mid, body, topic, temp in items:
        _message(path, mid, body, topic, temp)
    _message(path, 9, "Ad", "ad", 10.0, is_ad=1)
    _message(path, 10, "Digested", "done", 10.0, in_digest=1)
    conn = get_db(path)
    conn.execute("INSERT INTO trends (id, topic, unique_sources, status) VALUES (1, 'trend', 3, 'hot')")
    conn.execute("INSERT INTO trend_messages (trend_id, message_id) VALUES (1, 2)")
    conn.commit()
    conn.close()
    expected_text = _messages_text([
        ("Alert", 6.0, "hack"), ("Trend", 8.0, "trend"),
        ("High A", 8.0, "shared"), ("High B", 7.5, "shared"),
        ("Fill A", 6.0, "fill"),
    ])
    result = await analyzer.generate_digest(hours=12)
    assert result == "*🔥 Главное за час*\n\n*Новость*"
    cast(Any, analyzer.llm.complete).assert_awaited_once_with(
        user_prompt=DIGEST_PROMPT.format(
            period="последнее время", count=5, messages=expected_text,
            ongoing_trends_section="\n", digest_max=7,
        ),
        system_prompt=SYSTEM_PROMPT, temperature=0.4,
        disable_thinking=False, task="digest",
    )
    conn = get_db(path)
    assert [tuple(row) for row in conn.execute("SELECT content_md, parse_mode FROM digests")] == [
        (result, "Markdown")]
    conn.close()
    assert _marks(path) == {1: 1, 2: 1, 3: 1, 4: 1, 5: 0, 6: 1, 7: 0, 8: 0, 9: 0, 10: 1}


@pytest.mark.asyncio
async def test_spoiler_prompt_and_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, values, _chroma, path = _setup(tmp_path, monkeypatch)
    values["digest_template"] = "spoiler"
    _message(path, 1, "Story", "market", 8.0)
    content = await analyzer.generate_digest(hours=12)
    assert content == ("🔥 <b>Главное за последнее время:</b>\n\n"
                       "🔹 <b>Title &lt;one&gt;</b>\n"
                       "<blockquote expandable>A &amp; B</blockquote>\n"
                       '<a href="https://t.me/channel/1">источник</a>')
    cast(Any, analyzer.llm.complete_json).assert_awaited_once_with(
        user_prompt=DIGEST_PROMPT_SPOILER.format(
            period="последнее время", count=1,
            messages=_messages_text([("Story", 8.0, "market")]), digest_max=7,
            title_max_words=6, summary_max_sentences=2,
            merge_step=DIGEST_SPOILER_MERGE_OFF.format(digest_max=7),
        ),
        system_prompt=SYSTEM_PROMPT, temperature=0.3,
        disable_thinking=False, task="digest",
    )
    conn = get_db(path)
    assert [tuple(row) for row in conn.execute("SELECT content_md, parse_mode FROM digests")] == [(content, "HTML")]
    conn.close()
    assert _marks(path) == {1: 1}


@pytest.mark.asyncio
async def test_raw_marks_reset_before_next_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, _values, _chroma, path = _setup(tmp_path, monkeypatch)
    _message(path, 1, "First", "first", 8.0)
    _message(path, 2, "Second", "second", 7.0)
    raw = await analyzer.generate_digest(hours=12, return_raw=True)
    assert raw == _messages_text([("First", 8.0, "first"), ("Second", 7.0, "second")])
    assert _marks(path) == {1: 2, 2: 2}
    assert await analyzer.generate_digest(hours=12, return_raw=True) == raw
    assert _marks(path) == {1: 2, 2: 2}
    cast(Any, analyzer.llm.complete).assert_not_awaited()
    conn = get_db(path)
    assert conn.execute("SELECT COUNT(*) FROM digests").fetchone()[0] == 0
    conn.close()


@pytest.mark.asyncio
async def test_force_hours_and_last_period_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, _values, _chroma, path = _setup(tmp_path, monkeypatch)
    _message(path, 1, "Already", "old", 9.0, in_digest=1)
    _message(path, 2, "Recent", "new", 8.0)
    _message(path, 3, "Outside", "outside", 10.0, age_hours=4)
    assert await analyzer.generate_digest(hours=2, return_raw=True) == _messages_text([("Recent", 8.0, "new")])
    assert await analyzer.generate_digest(hours=2, force=True, return_raw=True) == _messages_text([
        ("Already", 9.0, "old"), ("Recent", 8.0, "new"),
    ])
    conn = get_db(path)
    future_start = (datetime.utcnow() + timedelta(minutes=1)).isoformat()
    conn.execute("INSERT INTO digests (content_md, period_start, period_end) VALUES ('prior', ?, ?)",
                 (future_start, future_start))
    conn.commit()
    conn.close()
    assert await analyzer.generate_digest(return_raw=True) is None
    assert _marks(path) == {1: 0, 2: 0, 3: 0}


@pytest.mark.asyncio
async def test_negative_previous_digest_reorders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, values, _chroma, path = _setup(tmp_path, monkeypatch)
    values["keywords_alert"] = ["hack"]
    _message(path, 1, "Earlier hack", "hack", 9.0, in_digest=1)
    _message(path, 2, "New hack", "hack", 9.5)
    _message(path, 3, "Good news", "good", 8.0)
    _message(path, 4, "Neutral news", "neutral", 7.5)
    raw = await analyzer.generate_digest(hours=12, return_raw=True)
    assert raw == _messages_text([
        ("Good news", 8.0, "good"), ("Neutral news", 7.5, "neutral"),
        ("New hack", 9.5, "hack"),
    ])
    assert _marks(path) == {1: 1, 2: 2, 3: 2, 4: 2}


@pytest.mark.asyncio
async def test_openclaw_payload_and_dispatched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, values, _chroma, path = _setup(tmp_path, monkeypatch)
    values["route_via_openclaw"] = True
    _message(path, 1, "Story", "market", 8.0)
    monkeypatch.setenv("OPENCLAW_WEBHOOK_URL", "http://agent/hooks/wake")
    monkeypatch.setenv("OPENCLAW_WEBHOOK_TOKEN", "token")
    client = AsyncMock()
    client.post.return_value.raise_for_status = Mock()
    manager = AsyncMock()
    manager.__aenter__.return_value = client
    monkeypatch.setattr(httpx, "AsyncClient", Mock(return_value=manager))
    assert await analyzer.generate_digest(hours=12) == "dispatched"
    client.post.assert_awaited_once_with(
        "http://agent/v1/chat/completions",
        json={"model": "main", "messages": [
            {"role": "system", "content": "You are the RoutingAgent. Process this event according to AGENTS.md instructions."},
            {"role": "user", "content": "[NEWS-RADAR EVENT: digest_raw]\nPeriod: последнее время\n"
             "Messages: 1\n\n" + _messages_text([("Story", 8.0, "market")]) + "\n\n"
             "Action: Generate a markdown digest based on these messages and send it to the user. Do not return the raw messages."},
        ]}, headers={"Authorization": "Bearer token"},
    )
    cast(Any, analyzer.llm.complete).assert_not_awaited()
    assert _marks(path) == {1: 1}
    conn = get_db(path)
    assert [tuple(row) for row in conn.execute("SELECT content_md, parse_mode FROM digests")] == []
    conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["crypto", "ai_value"])
async def test_analysis_writes_and_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str,
) -> None:
    analyzer, values, chroma, path = _setup(tmp_path, monkeypatch)
    values["analysis_profile"] = profile
    conn = get_db(path)
    conn.execute("INSERT INTO subscriptions (user_id, query) VALUES ('user', 'match')")
    conn.commit()
    conn.close()
    _message(path, 1, "MATCH details", "unused", 0, analyzed=0)
    _message(path, 2, "Special #ad", "unused", 0, analyzed=0)
    conn = get_db(path)
    stamp = str(conn.execute("SELECT collected_at FROM messages WHERE id=1").fetchone()[0])
    conn.close()
    alert = AsyncMock()
    route = AsyncMock()
    monkeypatch.setattr(analyzer, "_send_instant_alert", alert)
    monkeypatch.setattr(analyzer, "_route_event", route)
    if profile == "crypto":
        analyze_message = AsyncMock(return_value={
            "temperature": 9.0, "topic": "raw", "summary": "Summary match", "keywords": ["one"],
            "sentiment": "positive", "is_ad": False,
        })
        monkeypatch.setattr(analyzer, "_analyze_message", analyze_message)
    else:
        cast(Any, analyzer.llm).router = object()
        verdict = ValueVerdict(9, "tutorial", 8, True, "Useful outcome", "agents",
                               "Summary match", ["one"], False)
        classifier = Mock()
        classifier.classify = AsyncMock(return_value=[ClassifyOutcome("1", verdict, None, "tool")])
        classifier.calls = []
        factory = Mock(return_value=classifier)
        monkeypatch.setattr(module, "LLMValueClassifier", factory)
    assert await analyzer.analyze_pending() == 2
    if profile == "crypto":
        analyze_message.assert_awaited_once_with(message_id=1, text="MATCH details", source_name="channel")
    conn = get_db(path)
    row = conn.execute("SELECT message_id, temperature, topic, summary, keywords, sentiment, "
                       "content_type, value_score, has_outcome, takeaway FROM analysis WHERE message_id=1").fetchone()
    assert tuple(row) == (
        1, 9.0, "canonical" if profile == "crypto" else "agents", "Summary match", '["one"]',
        "positive" if profile == "crypto" else "neutral",
        None if profile == "crypto" else "tutorial",
        None if profile == "crypto" else 8,
        0 if profile == "crypto" else 1,
        None if profile == "crypto" else "Useful outcome",
    )
    assert conn.execute("SELECT COUNT(*) FROM analysis WHERE message_id=2").fetchone()[0] == 0
    assert [tuple(r) for r in conn.execute("SELECT id, analyzed, chroma_synced, is_ad FROM messages ORDER BY id")] == [
        (1, 1, 1, 0), (2, 1, 0, 1),
    ]
    conn.close()
    chroma.add_message.assert_called_once_with(
        message_id=1, embedding=[0.25, 0.75], text="MATCH details", source_name="channel",
        timestamp=stamp,
        temperature=9.0, topic="canonical" if profile == "crypto" else "agents",
    )
    if profile == "ai_value":
        factory.assert_called_once_with(analyzer.llm.router, task="classify", concurrency=3)
        assert [item.id for item in classifier.classify.call_args.args[0]] == ["1"]
    await asyncio.sleep(0)
    alert.assert_awaited_once_with(1, "channel", 9.0, "raw" if profile == "crypto" else "agents",
                                   "Summary match", "MATCH details")
    route.assert_awaited_once_with("subscription_match", {
        "user_id": "user", "query": "match", "summary": "Summary match",
        "source": "channel", "text": "MATCH details",
    })
