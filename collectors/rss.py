"""
RssCollector — poll-based RSS/Atom collector (AI blogs, Habr, dev.to, ...).

Feeds come from settings.json -> sources.rss.feeds (poll_minutes controls
the interval). Poll mode: listen() polls every feed, yields entries not
seen yet, then sleeps — see BaseCollector / ТЗ #4 section 2. A feed that's
unreachable, or a single entry missing a link/date, is skipped with a
warning; it never aborts the whole poll cycle.

ТЗ #4 И2.1: entries are checked, in order, against the in-memory seen set,
the caller-supplied is_known_url (already stored in the DB from a previous
run), and the max_age_hours window — all before the expensive full-text
fetch. Entries are walked newest-first so the shared fetch budget goes to
the freshest ones first.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Callable

import feedparser
import httpx

from collectors.base import BaseCollector, RawMessage
from collectors.fulltext_fetcher import FullTextFetcher
from collectors.url_utils import normalize_url

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
        max_age_hours: int = 72,
        is_known_url: Callable[[str], bool] | None = None,
    ) -> None:
        self._feeds = feeds
        self._poll_seconds = poll_minutes * 60
        self._fetcher = fetcher or FullTextFetcher()
        self._max_age_hours = max_age_hours
        self._is_known_url = is_known_url or (lambda _url: False)
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

        candidates: list[tuple[Any, str, datetime]] = []
        for entry in parsed.entries:
            link = entry.get("link")
            if not link:
                logger.warning(f"Skipping RSS entry with no link in feed {feed_url}")
                continue

            timestamp = _parse_entry_date(entry)
            if timestamp is None:
                logger.warning(f"Skipping RSS entry with no date: {link}")
                continue

            candidates.append((entry, link, timestamp))

        candidates.sort(key=lambda c: c[2], reverse=True)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._max_age_hours)
        old_count = 0

        for entry, link, timestamp in candidates:
            normalized_link = normalize_url(link)

            if normalized_link in self._seen_urls:
                continue
            if self._is_known_url(normalized_link):
                self._seen_urls.add(normalized_link)
                continue
            if timestamp < cutoff:
                old_count += 1
                continue

            snippet = (entry.get("summary") or "").strip()
            text = snippet
            if len(snippet) < _MIN_SNIPPET_CHARS_FOR_FULL_FETCH:
                full_text = await self._fetcher.fetch(normalized_link, feed_key=feed_url)
                if full_text:
                    text = full_text

            if not text:
                text = (entry.get("title") or "").strip()

            if not text:
                logger.warning(f"Skipping RSS entry with no usable text: {normalized_link}")
                continue

            self._seen_urls.add(normalized_link)

            yield RawMessage(
                external_id=str(entry.get("id") or normalized_link),
                source_name=source_name,
                source_type="rss",
                text=text,
                url=normalized_link,
                timestamp=timestamp,
            )

        if old_count:
            logger.info(f"Skipped {old_count} entries older than {self._max_age_hours}h in feed {feed_url}")


def _parse_entry_date(entry: Any) -> datetime | None:
    parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed_time:
        return None
    year, month, day, hour, minute, second = parsed_time[:6]
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
