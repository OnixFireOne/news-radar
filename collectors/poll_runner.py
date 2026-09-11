"""
poll_runner — orchestrates poll-mode collectors (rss, hackernews; github and
reddit join in a later iteration).

Reads sources.rss / sources.hackernews from settings.json, starts every
enabled collector's listen() loop concurrently, and persists each yielded
RawMessage the same way collectors/telegram.py does: upsert the source row,
then INSERT OR IGNORE the message. URL-based dedup is enforced by the
partial unique index on messages.url (ТЗ #4 И1) — a message whose URL is
already stored is silently skipped, same as Telegram's dedup-by-external_id.

ТЗ #4 И2.1: collectors also get a `known_url_checker` bound to this DB, so
a URL already stored on a previous run is skipped before the expensive
full-text fetch, not just at the INSERT OR IGNORE stage. Without it, every
restart re-spent the fetch budget on URLs that were already collected.
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

from collectors.base import BaseCollector, RawMessage
from collectors.fulltext_fetcher import DEFAULT_USER_AGENT, FullTextFetcher
from collectors.hackernews import HackerNewsCollector
from collectors.rss import RssCollector
from config.config_watcher import ConfigWatcher
from database.schema import get_db, init_db

logger = logging.getLogger(__name__)


def make_known_url_checker(db_path: str) -> Callable[[str], bool]:
    """Build an is_known_url callback backed by a `SELECT 1` against this DB."""

    def is_known_url(url: str) -> bool:
        conn = get_db(db_path)
        try:
            row = conn.execute("SELECT 1 FROM messages WHERE url = ?", (url,)).fetchone()
            return row is not None
        finally:
            conn.close()

    return is_known_url


def save_message(db_path: str, msg: RawMessage) -> None:
    """Persist a poll-collector message. Requires msg.url — these sources have no other identity."""
    if not msg.url:
        logger.warning(f"Skipping {msg.source_type} message with no url: {msg.external_id}")
        return

    conn = get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM sources WHERE name = ? AND type = ?",
            (msg.source_name, msg.source_type),
        ).fetchone()

        if not row:
            cursor = conn.execute(
                "INSERT INTO sources (type, name, display_name) VALUES (?, ?, ?)",
                (msg.source_type, msg.source_name, msg.source_name),
            )
            source_id = cursor.lastrowid
        else:
            source_id = row["id"]

        conn.execute(
            """
            INSERT OR IGNORE INTO messages
                (source_id, external_id, text, url, collected_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                source_id,
                msg.external_id,
                msg.text,
                msg.url,
                msg.timestamp or datetime.utcnow(),
            ),
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error saving {msg.source_type} message {msg.external_id} ({msg.url}): {e}")
    finally:
        conn.close()


async def run_collector(collector: BaseCollector, db_path: str) -> None:
    await collector.start()
    try:
        # See the type: ignore[override] note in rss.py/hackernews.py: BaseCollector's
        # abstract listen() type-checks as coroutine-returning, not an async generator.
        async for msg in collector.listen():  # type: ignore[attr-defined]
            save_message(db_path, msg)
    finally:
        await collector.stop()


def build_collectors(sources_cfg: dict[str, Any], db_path: str | None = None) -> list[BaseCollector]:
    fulltext_cfg = sources_cfg.get("fulltext", {})
    fetcher = FullTextFetcher(
        user_agent=DEFAULT_USER_AGENT,
        max_fetches_per_cycle=fulltext_cfg.get("max_per_cycle", 20),
        max_fetches_per_feed=fulltext_cfg.get("max_per_feed", 5),
    )
    max_age_hours = sources_cfg.get("max_age_hours", 72)
    is_known_url = make_known_url_checker(db_path) if db_path else None
    collectors: list[BaseCollector] = []

    rss_cfg = sources_cfg.get("rss", {})
    if rss_cfg.get("enabled"):
        collectors.append(
            RssCollector(
                feeds=rss_cfg.get("feeds", []),
                poll_minutes=rss_cfg.get("poll_minutes", 60),
                fetcher=fetcher,
                max_age_hours=max_age_hours,
                is_known_url=is_known_url,
            )
        )
        logger.info(f"RSS collector enabled: {len(rss_cfg.get('feeds', []))} feed(s)")
    else:
        logger.info("RSS collector disabled (sources.rss.enabled=false)")

    hn_cfg = sources_cfg.get("hackernews", {})
    if hn_cfg.get("enabled"):
        collectors.append(
            HackerNewsCollector(
                queries=hn_cfg.get("queries", []),
                min_points=hn_cfg.get("min_points", 30),
                poll_minutes=hn_cfg.get("poll_minutes", 60),
                fetcher=fetcher,
                hits_per_page=hn_cfg.get("hits_per_page", 50),
                max_age_hours=max_age_hours,
                is_known_url=is_known_url,
            )
        )
        logger.info(f"Hacker News collector enabled: {hn_cfg.get('queries', [])}")
    else:
        logger.info("Hacker News collector disabled (sources.hackernews.enabled=false)")

    return collectors


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    db_path = os.environ.get("DATABASE_PATH", "/app/data/news.db")
    init_db(db_path)

    cfg = ConfigWatcher("/app/config/settings.json")
    collectors = build_collectors(cfg.get("sources", {}), db_path=db_path)

    if not collectors:
        logger.info("No poll collectors enabled (sources.rss/hackernews both off) — idling.")
        while True:
            await asyncio.sleep(3600)

    await asyncio.gather(*(run_collector(c, db_path) for c in collectors))


if __name__ == "__main__":
    asyncio.run(main())
