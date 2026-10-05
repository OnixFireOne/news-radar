"""
RssCollector — poll-based RSS/Atom collector (AI blogs, Habr, dev.to, ...).

Feeds come from settings.json -> sources.rss.feeds (poll_minutes controls
the interval). Poll mode: listen() polls every feed, yields entries not
seen yet, then sleeps — see BaseCollector / ТЗ #4 section 2. A feed that's
unreachable, or a single entry missing a link/date, is skipped with a
warning; it never aborts the whole poll cycle.

ТЗ #4 И2.1/И3.1: entries are checked, in order, against the in-memory seen
set, the max_age_hours window, and the caller-supplied is_known_url
(already stored in the DB from a previous run) — all before the expensive
full-text fetch. The age check runs before is_known_url (a SQLite lookup)
so an archive-heavy feed's stale entries are rejected without touching the
DB. Entries are walked newest-first so the shared fetch budget goes to the
freshest ones first.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Callable, Literal

import feedparser
import httpx
import trafilatura

from collectors.base import BaseCollector, RawMessage
from collectors.fulltext_fetcher import FullTextFetcher
from collectors.url_utils import normalize_url

logger = logging.getLogger(__name__)

# Below this many characters, the feed-provided snippet is treated as "just a
# teaser" and a full-text fetch is attempted. Measured against the CLEANED
# text (see _clean_snippet_html), not the raw HTML — otherwise a snippet
# that's mostly <p>/<img>/<a> markup around a short teaser never crosses the
# threshold and a full fetch never happens (ТЗ #4 И3.1).
_MIN_SNIPPET_CHARS_FOR_FULL_FETCH = 500

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


class RssCollector(BaseCollector):
    source_type = "rss"

    def __init__(
        self,
        feeds: list[str],
        poll_minutes: int = 60,
        fetcher: FullTextFetcher | None = None,
        max_age_hours: int = 72,
        is_known_url: Callable[[str], bool] | None = None,
        fulltext_mode: Literal["short_only", "always"] = "short_only",
    ) -> None:
        self._feeds = feeds
        self._poll_seconds = poll_minutes * 60
        self._fetcher = fetcher or FullTextFetcher()
        self._max_age_hours = max_age_hours
        self._fulltext_mode = fulltext_mode
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
        deferred_count = 0

        for entry, link, timestamp in candidates:
            normalized_link = normalize_url(link)

            if normalized_link in self._seen_urls:
                continue
            # Age check before is_known_url: is_known_url hits SQLite per
            # entry, and an archive-heavy feed can offer ~thousands of stale
            # entries per cycle (ТЗ #4 И3.1) — the timestamp comparison is
            # free and rejects those without touching the DB at all. Net
            # effect is unchanged: an old entry is still dropped either way.
            if timestamp < cutoff:
                old_count += 1
                continue
            if self._is_known_url(normalized_link):
                self._seen_urls.add(normalized_link)
                continue

            raw_snippet = (entry.get("summary") or "").strip()
            snippet = _clean_snippet_html(raw_snippet)
            text = snippet
            if self._fulltext_mode == "always" or len(snippet) < _MIN_SNIPPET_CHARS_FOR_FULL_FETCH:
                full_text = await self._fetcher.fetch(normalized_link, feed_key=feed_url)
                if self._fetcher.last_capped is True:
                    deferred_count += 1
                    continue
                if full_text and (self._fulltext_mode == "short_only" or len(full_text) > len(snippet)):
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

        if deferred_count:
            logger.info("Deferred %s entries in feed %s due to full-text fetch caps", deferred_count, feed_url)
        if old_count:
            logger.info(f"Skipped {old_count} entries older than {self._max_age_hours}h in feed {feed_url}")


def _parse_entry_date(entry: Any) -> datetime | None:
    parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed_time:
        return None
    year, month, day, hour, minute, second = parsed_time[:6]
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def _clean_snippet_html(raw_html: str) -> str:
    """Turn a feed-provided snippet's HTML into plain text.

    Habr, dev.to and Simon Willison's feed put <p>/<img>/<a> markup straight
    into <description>/<summary> — left as-is, that markup flows into
    messages.text and then verbatim into the classifier prompt (ТЗ #4 И3.1).

    trafilatura.extract() targets whole documents — boilerplate-removal
    heuristics and a minimum-content-length check tuned for full pages — and
    routinely returns None on a short RSS teaser, so it's tried first for the
    rare snippet it does handle, and a deterministic tag-strip +
    entity-decode + whitespace-collapse fallback guarantees plain text
    either way, including on fragments trafilatura can't parse at all.
    """
    if not raw_html.strip():
        return ""

    try:
        extracted: str | None = trafilatura.extract(raw_html)
    except Exception:
        # A short, sometimes-unclosed HTML fragment is a different beast
        # from the full pages trafilatura is built for — never let a feed
        # snippet's malformed markup take down the whole poll cycle.
        extracted = None

    text: str
    if extracted:
        text = extracted
    else:
        text = _HTML_TAG_RE.sub(" ", raw_html)

    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip()
