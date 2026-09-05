"""
ТЗ #4 И1 acceptance: DB migration is idempotent and safe on a populated DB
(nullable new columns, no forced backfill, second run does not raise or
touch existing data). Seeds a DB using only the base SCHEMA (no ALTERs
applied yet) to simulate a real pre-И1 database, mirroring how the app's
own migration runner works — not a purpose-built shortcut.
"""

import sqlite3

import pytest

from database.schema import SCHEMA, get_db, init_db


def _seed_pre_i1_db(path: str) -> None:
    """Populate a DB using only the base CREATE TABLE statements — no migrations yet."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO sources (id, name, type, active) VALUES (1, 'test_channel', 'telegram', 1)")
    conn.execute(
        "INSERT INTO messages (id, external_id, source_id, text, analyzed, chroma_synced, is_ad) "
        "VALUES (1, 'm1', 1, 'first message text here, long enough', 1, 1, 0)"
    )
    conn.execute(
        "INSERT INTO messages (id, external_id, source_id, text, analyzed, chroma_synced, is_ad) "
        "VALUES (2, 'm2', 1, 'second message text here, long enough too', 0, 0, 0)"
    )
    conn.execute(
        "INSERT INTO analysis (id, message_id, temperature, topic, summary, keywords, sentiment) "
        "VALUES (1, 1, 7.5, 'bitcoin', 'BTC pumped', '[\"btc\"]', 'positive')"
    )
    conn.commit()
    conn.close()


def test_migration_adds_new_columns_without_touching_existing_data(tmp_path) -> None:
    db_path = str(tmp_path / "prod_like.db")
    _seed_pre_i1_db(db_path)

    init_db(db_path)  # first run: applies all pending migrations, including the new И1 ones

    conn = get_db(db_path)
    try:
        analysis_cols = {row["name"] for row in conn.execute("PRAGMA table_info(analysis)")}
        assert {"content_type", "value_score", "has_outcome", "takeaway", "md_path"} <= analysis_cols

        message_cols = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
        assert "url" in message_cols

        # Existing data untouched
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM analysis").fetchone()[0] == 1
        row = conn.execute("SELECT temperature, topic FROM analysis WHERE id=1").fetchone()
        assert row["temperature"] == 7.5
        assert row["topic"] == "bitcoin"

        # New columns are nullable / zero-default for pre-existing rows — no forced backfill
        analysis_row = conn.execute(
            "SELECT content_type, value_score, has_outcome, takeaway, md_path FROM analysis WHERE id=1"
        ).fetchone()
        assert analysis_row["content_type"] is None
        assert analysis_row["value_score"] is None
        assert analysis_row["has_outcome"] == 0
        assert analysis_row["takeaway"] is None
        assert analysis_row["md_path"] is None

        assert conn.execute("SELECT url FROM messages WHERE id=1").fetchone()["url"] is None
    finally:
        conn.close()

    # Second run on the same (already-migrated) DB must not raise or change row counts.
    init_db(db_path)
    conn = get_db(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM analysis").fetchone()[0] == 1
    finally:
        conn.close()


def test_messages_url_index_allows_nulls_but_dedups_real_urls(tmp_path) -> None:
    db_path = str(tmp_path / "url_dedup.db")
    _seed_pre_i1_db(db_path)
    init_db(db_path)

    conn = get_db(db_path)
    try:
        # Existing rows both have url=NULL — the partial unique index must not
        # treat them as duplicates of each other.
        conn.execute("INSERT INTO sources (id, name, type, active) VALUES (2, 'src2', 'rss', 1)")
        conn.execute(
            "INSERT INTO messages (external_id, source_id, text, url) "
            "VALUES ('m3', 2, 'third message here, long enough', 'https://example.com/a')"
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO messages (external_id, source_id, text, url) "
                "VALUES ('m4', 2, 'fourth message here, long enough', 'https://example.com/a')"
            )
            conn.commit()
    finally:
        conn.close()
