"""
collectors.poll_runner — ТЗ #4 И2 acceptance: URL dedup on save (relies on
the partial unique index from И1), a message with no URL is skipped rather
than written, and collectors are only built when their config flag is on.
"""

from datetime import datetime, timezone

from database.schema import get_db, init_db

from collectors.base import RawMessage
from collectors.poll_runner import build_collectors, save_message
from collectors.rss import RssCollector
from collectors.hackernews import HackerNewsCollector


def _msg(**overrides) -> RawMessage:
    base = dict(
        external_id="ext-1",
        source_name="Example Blog",
        source_type="rss",
        text="some article text",
        url="https://blog.example.com/post-1",
        timestamp=datetime(2025, 9, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return RawMessage(**base)


def test_save_message_dedups_by_url(tmp_path) -> None:
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    save_message(db_path, _msg())
    save_message(db_path, _msg(external_id="ext-2"))  # same url, different external_id

    conn = get_db(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE url = ?", ("https://blog.example.com/post-1",)
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 1


def test_save_message_skips_message_with_no_url(tmp_path, caplog) -> None:
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    save_message(db_path, _msg(url=None))

    conn = get_db(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        conn.close()

    assert count == 0
    assert any("no url" in r.message.lower() for r in caplog.records)


def test_build_collectors_respects_enabled_flags() -> None:
    assert build_collectors({"rss": {"enabled": False}, "hackernews": {"enabled": False}}) == []

    collectors = build_collectors(
        {
            "rss": {"enabled": True, "feeds": ["https://a.example.com/feed.xml"]},
            "hackernews": {"enabled": False},
        }
    )
    assert len(collectors) == 1
    assert isinstance(collectors[0], RssCollector)

    collectors = build_collectors(
        {
            "rss": {"enabled": False},
            "hackernews": {"enabled": True, "queries": ["llm"]},
        }
    )
    assert len(collectors) == 1
    assert isinstance(collectors[0], HackerNewsCollector)
