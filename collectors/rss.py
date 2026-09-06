"""
RssCollector — poll-based RSS/Atom collector (AI blogs, Habr, dev.to, ...).

Feeds come from settings.json -> sources.rss.feeds (poll_minutes controls
the interval). Poll mode: listen() polls every feed, yields entries not
seen yet, then sleeps — see BaseCollector / ТЗ #4 section 2. A feed that's
unreachable, or a single entry missing a link/date, is skipped with a
warning; it never aborts the whole poll cycle.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import feedparser
import httpx

from collectors.base import BaseCollector, RawMessage
from collectors.fulltext_fetcher import FullTextFetcher

logger = logging.getLogger(__name__)

# Below this many characters, the feed-provided snippet is treated as "just a
# teaser" and a full-text fetch is attempted.
_MIN_SNIPPET_CHARS_FOR_FULL_FETCH = 500


class RssCollector(BaseCollector):
    source_type = "rss"

    def __init__(
        self,
        feeds: list[str],
        poll_minutes: int = 60,
        fetcher: FullTextFetcher | None = None,
    ) -> None:
        self._feeds = feeds
        self._poll_seconds = poll_minutes * 60
        self._fetcher = fetcher or FullTextFetcher()
        self._seen_urls: set[str] = set()
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    # BaseCollector.listen has no `yield` in its abstract body, so mypy infers
    # its type as a coroutine returning an AsyncIterator rather than an async
    # generator — a pre-existing base.py quirk, not something to fix here.
    async def listen(self) -> AsyncIterator[RawMessage]:  # type: ignore[override, misc]
        while self._running:
            self._fetcher.new_cycle()
            for feed_url in self._feeds:
                async for msg in self._poll_feed(feed_url):
                    yield msg
            await asyncio.sleep(self._poll_seconds)

    async def fetch_history(self, source_name: str, limit: int = 100) -> list[RawMessage]:
        """One-shot fetch of a single feed's current entries (source_name = feed URL)."""
        messages: list[RawMessage] = []
        async for msg in self._poll_feed(source_name):
            messages.append(msg)
            if len(messages) >= limit:
                break
        return messages

    async def _poll_feed(self, feed_url: str) -> AsyncIterator[RawMessage]:
        try:
            async with httpx.AsyncClient(
                timeout=15.0, headers={"User-Agent": self._fetcher.user_agent}
            ) as client:
                resp = await client.get(feed_url)
                resp.raise_for_status()
                raw = resp.content
        except Exception as e:
            logger.warning(f"Failed to fetch feed {feed_url}: {e}")
            return

        parsed: Any = feedparser.parse(raw)
        source_name = str(parsed.feed.get("title") or feed_url) if parsed.feed else feed_url

        for entry in parsed.entries:
            link = entry.get("link")
            if not link:
                logger.warning(f"Skipping RSS entry with no link in feed {feed_url}")
                continue
            if link in self._seen_urls:
                continue

            timestamp = _parse_entry_date(entry)
            if timestamp is None:
                logger.warning(f"Skipping RSS entry with no date: {link}")
                continue

            snippet = (entry.get("summary") or "").strip()
            text = snippet
            if len(snippet) < _MIN_SNIPPET_CHARS_FOR_FULL_FETCH:
                full_text = await self._fetcher.fetch(link)
                if full_text:
                    text = full_text

            if not text:
                logger.warning(f"Skipping RSS entry with no usable text: {link}")
                continue

            self._seen_urls.add(link)

            yield RawMessage(
                external_id=str(entry.get("id") or link),
                source_name=source_name,
                source_type="rss",
                text=text,
                url=link,
                timestamp=timestamp,
            )


def _parse_entry_date(entry: Any) -> datetime | None:
    parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed_time:
        return None
    year, month, day, hour, minute, second = parsed_time[:6]
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
