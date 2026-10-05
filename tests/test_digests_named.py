"""Named digest windows, category parts, schedules and migration regressions."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from typing import Any, cast
from unittest.mock import Mock

import pytest

from analyzer.pipeline.categories import load_digests, resolve_digest, schedule_slots, UnknownDigestError
from analyzer.value_funnel import select_with_quotas
from bot.digest_schedule import schedule_slots as bot_slots, slot_key
from database.schema import get_db, init_db
from tests.test_pipeline_characterization import _setup, _marks
from tests.test_categories import categories, seed_sources, message


def digests() -> list[dict[str, Any]]:
    return [
        {"name": "off", "enabled": False, "categories": ["crypto"], "at": ["20:00"], "tz": "Europe/Moscow"},
        {"name": "morning", "enabled": True, "categories": ["articles", "crypto"],
         "at": ["09:10"], "tz": "Europe/Moscow"},
        {"name": "evening", "enabled": True, "categories": ["crypto"],
         "at": ["12:00", "20:00"], "tz": "Europe/Moscow"},
    ]


def test_resolution_and_schedule() -> None:
    cfg = {"digests": digests()}
    assert resolve_digest({}) is None and resolve_digest({"digests": []}) is None
    default = resolve_digest(cfg)
    assert default is not None and default.name == "morning"
    named = resolve_digest(cfg, "evening")
    assert named is not None and named.categories == ("crypto",)
    with pytest.raises(UnknownDigestError, match="Unknown digest: absent"):
        resolve_digest(cfg, "absent")
    assert [slot_key(name, slot) for name, slot in schedule_slots(load_digests(cfg))] == [
        ("morning", "09:10:00", "Europe/Moscow"),
        ("evening", "12:00:00", "Europe/Moscow"),
        ("evening", "20:00:00", "Europe/Moscow"),
    ]
    assert bot_slots(cfg) == schedule_slots(load_digests(cfg))
    assert [slot_key(name, slot) for name, slot in bot_slots({})] == [
        (None, "12:00:00", "Europe/Moscow"), (None, "20:00:00", "Europe/Moscow")]
    assert bot_slots({"digests": [digests()[0]]}) == []
    assert resolve_digest({"digests": [digests()[0]]}) is None
    cfg["digests"][1]["tz"] = "America/New_York"
    assert [slot_key(*slot) for slot in bot_slots(cfg)] == [
        slot_key(*slot) for slot in schedule_slots(load_digests(cfg))]


def test_migration_preserves_old_digest_and_is_idempotent(tmp_path: Path) -> None:
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE digests (id INTEGER PRIMARY KEY, content_md TEXT NOT NULL, "
                 "period_start DATETIME NOT NULL, period_end DATETIME NOT NULL, "
                 "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, sent_telegram INTEGER DEFAULT 0)")
    conn.execute("INSERT INTO digests (content_md, period_start, period_end) VALUES ('old', '2026-09-01', '2026-09-02')")
    conn.commit()
    conn.close()
    init_db(path)
    init_db(path)
    conn = get_db(path)
    row = conn.execute("SELECT content_md, parse_mode, name, category FROM digests").fetchone()
    assert tuple(row) == ("old", "Markdown", None, None)
    conn.close()


@pytest.mark.asyncio
async def test_two_categories_share_window_but_not_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, values, _, path = _setup(tmp_path, monkeypatch)
    values["categories"] = categories()
    values["digests"] = digests()
    values["categories"]["articles"].update(template="classic", select="tiers")
    cast(Any, analyzer.llm).router = object()
    seed_sources(path)
    message(path, 1, 1, analyzed=1, age_hours=2)
    message(path, 2, 2, analyzed=1, age_hours=2)
    message(path, 3, 3, analyzed=1, age_hours=2)
    conn = get_db(path)
    recent = datetime.utcnow().isoformat()
    conn.execute("INSERT INTO digests (content_md, period_start, period_end, name) VALUES ('other', ?, ?, 'evening')",
                 (recent, recent))
    conn.commit()
    conn.close()
    parts = await analyzer.run_digest("morning")
    assert [part.category for part in parts] == ["articles", "crypto"]
    assert all(part.result and part.digest_id is not None for part in parts)
    conn = get_db(path)
    rows = conn.execute("SELECT id, name, category, period_start FROM digests WHERE name='morning' ORDER BY id").fetchall()
    assert [row["id"] for row in rows] == [part.digest_id for part in parts]
    assert [(row["name"], row["category"]) for row in rows] == [("morning", "articles"), ("morning", "crypto")]
    assert rows[0]["period_start"] == rows[1]["period_start"]
    assert datetime.fromisoformat(rows[0]["period_start"]) < datetime.utcnow() - timedelta(hours=2)
    conn.close()
    assert _marks(path) == {1: 1, 2: 1, 3: 1}
    assert await analyzer.run_digest("morning", hours=12) == []
    assert await analyzer.run_digest("evening", hours=12) == []
    assert len(await analyzer.run_digest("morning", hours=12, force=True)) == 2


@pytest.mark.asyncio
async def test_disabled_missing_and_params(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           caplog: pytest.LogCaptureFixture) -> None:
    analyzer, values, _, path = _setup(tmp_path, monkeypatch)
    values["categories"] = categories()
    values["categories"]["crypto"]["enabled"] = False
    values["categories"]["articles"].update(template="classic", select="tiers", params={"max_items": 1})
    values["digests"] = digests()
    values["digests"][1]["categories"].append("missing")
    cast(Any, analyzer.llm).router = object()
    seed_sources(path)
    for mid in range(1, 4):
        message(path, mid, mid, analyzed=1)
    parts = await analyzer.run_digest(None, hours=12, return_raw=True)
    assert len(parts) == 1 and parts[0].category == "articles" and parts[0].digest_id is None
    assert parts[0].result.count("Channel:") == 1
    assert "MATCH article 1" not in parts[0].result
    assert _marks(path)[1] == 0
    assert "category crypto disabled" in caplog.text and "category missing disabled" in caplog.text
    assert await analyzer.generate_digest(hours=12, return_raw=True) == parts[0].result
    assert await analyzer.run_digest("off", hours=12) == []
    with pytest.raises(UnknownDigestError):
        await analyzer.run_digest("absent")


@pytest.mark.asyncio
async def test_old_mixed_trend_does_not_consume_other_category(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, values, _, path = _setup(tmp_path, monkeypatch)
    values["categories"] = categories()
    values["digests"] = digests()
    values["categories"]["articles"].update(template="classic", select="tiers")
    cast(Any, analyzer.llm).router = object()
    seed_sources(path)
    message(path, 1, 1, analyzed=1)
    message(path, 2, 2, analyzed=1)
    conn = get_db(path)
    conn.execute("INSERT INTO trends (id, topic, unique_sources, status) VALUES (1, 'mixed', 3, 'hot')")
    conn.executemany("INSERT INTO trend_messages (trend_id, message_id) VALUES (1, ?)", [(1,), (2,)])
    conn.commit()
    conn.close()
    assert len(await analyzer.run_digest("morning", hours=12)) == 2


def test_missing_crypto_quota_is_zero() -> None:
    assert select_with_quotas([{"content_type": "crypto", "value_score": 10, "temperature": 10}],
                              {"quotas": {"practical": 5}}) == []


def test_cross_dedup_reads_only_named_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, _, _, path = _setup(tmp_path, monkeypatch)
    conn = get_db(path)
    now = datetime.utcnow().isoformat()
    conn.execute("INSERT INTO digests (content_md, period_start, period_end, name) VALUES (?, ?, ?, 'other')",
                 ("🔹\nA different story", now, now))
    conn.commit()
    conn.close()
    encoder = Mock(side_effect=AssertionError("Other digest must not be embedded"))
    monkeypatch.setattr(analyzer.embedder, "encode", encoder)
    candidates = [{"id": 1, "summary": "fresh"}]
    assert analyzer._dedup_against_previous_digests(candidates, digest_name="morning") == (candidates, [])
    encoder.assert_not_called()


@pytest.mark.asyncio
async def test_emotional_balance_is_a_template_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, values, _, path = _setup(tmp_path, monkeypatch)
    values["categories"] = categories()
    values["categories"]["articles"].update(template="classic", select="tiers")
    values["digests"] = digests()
    cast(Any, analyzer.llm).router = object()
    seed_sources(path)
    for mid in (1, 2, 3, 10):
        message(path, mid, 2, analyzed=1)
    conn = get_db(path)
    conn.executemany("UPDATE messages SET text=? WHERE id=?",
                     [("MATCH hack article 1", 1), ("MATCH hack article 2", 2), ("MATCH hack before", 10)])
    conn.execute("UPDATE messages SET in_digest=1 WHERE id=10")
    conn.commit()
    conn.close()

    async def first_text(balance: bool, keywords: list[str]) -> str:
        values["keywords_alert"] = keywords
        values["categories"]["articles"]["params"] = {"emotional_balance": balance}
        parts = await analyzer.run_digest("morning", hours=12, return_raw=True)
        return str(parts[0].result)

    unbalanced = await first_text(True, [])
    assert "hack" in unbalanced.split("\n\n")[0]  # otherwise the balance check below is vacuous
    assert await first_text(False, ["hack"]) == unbalanced
    balanced = await first_text(True, ["hack"])
    assert "hack" not in balanced.split("\n\n")[0]
