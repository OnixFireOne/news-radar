"""
collectors.poll_runner — ТЗ #4 И2/И2.1 acceptance: URL dedup on save (relies
on the partial unique index from И1), a message with no URL is skipped
rather than written, collectors are only built when their config flag is
on, and the new sources.max_age_hours/fulltext/hits_per_page keys plus the
DB-backed is_known_url checker are wired into the built collectors.
"""

from datetime import datetime, timezone

from database.schema import get_db, init_db

from collectors.base import RawMessage
from collectors.poll_runner import build_collectors, make_known_url_checker, save_message
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


def test_build_collectors_wires_max_age_and_fulltext_limits() -> None:
    collectors = build_collectors(
        {
            "max_age_hours": 48,
            "fulltext": {"max_per_cycle": 7, "max_per_feed": 2},
            "rss": {"enabled": True, "feeds": ["https://a.example.com/feed.xml"]},
            "hackernews": {"enabled": True, "queries": ["llm"], "hits_per_page": 17},
        }
    )

    rss = next(c for c in collectors if isinstance(c, RssCollector))
    hn = next(c for c in collectors if isinstance(c, HackerNewsCollector))

    assert rss._max_age_hours == 48
    assert hn._max_age_hours == 48
    assert hn._hits_per_page == 17
    # Both collectors share one FullTextFetcher instance built from sources.fulltext.
    assert rss._fetcher is hn._fetcher
    assert rss._fetcher._max_per_cycle == 7
    assert rss._fetcher._max_per_feed == 2


def test_build_collectors_wires_known_url_checker_from_db(tmp_path) -> None:
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    save_message(db_path, _msg(url="https://blog.example.com/already-collected"))

    collectors = build_collectors(
        {"rss": {"enabled": True, "feeds": ["https://a.example.com/feed.xml"]}},
        db_path=db_path,
    )

    rss = collectors[0]
    assert isinstance(rss, RssCollector)
    assert rss._is_known_url("https://blog.example.com/already-collected") is True
    assert rss._is_known_url("https://blog.example.com/never-seen") is False


def test_build_collectors_without_db_path_treats_every_url_as_unknown() -> None:
    collectors = build_collectors({"rss": {"enabled": True, "feeds": ["https://a.example.com/feed.xml"]}})

    rss = collectors[0]
    assert isinstance(rss, RssCollector)
    assert rss._is_known_url("https://anything.example.com/") is False


def test_make_known_url_checker_reflects_saved_messages(tmp_path) -> None:
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    is_known = make_known_url_checker(db_path)

    assert is_known("https://blog.example.com/post-1") is False

    save_message(db_path, _msg())

    assert is_known("https://blog.example.com/post-1") is True
