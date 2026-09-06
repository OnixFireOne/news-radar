"""
HackerNewsCollector — poll-based collector over the Algolia HN Search API
(free, no API key: https://hn.algolia.com/api/v1/search_by_date).

Queries and min_points come from settings.json -> sources.hackernews. A
story below min_points is skipped (not an error); a hit missing objectID
or created_at is skipped with a warning, never aborting the whole cycle.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, AsyncIterator

import httpx

from collectors.base import BaseCollector, RawMessage
from collectors.fulltext_fetcher import FullTextFetcher

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://hn.algolia.com/api/v1"


class HackerNewsCollector(BaseCollector):
    source_type = "hackernews"

    def __init__(
        self,
        queries: list[str],
        min_points: int = 30,
        poll_minutes: int = 60,
        fetcher: FullTextFetcher | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._queries = queries
        self._min_points = min_points
        self._poll_seconds = poll_minutes * 60
        self._fetcher = fetcher or FullTextFetcher()
        self._base_url = base_url.rstrip("/")
        self._seen_ids: set[str] = set()
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
            for query in self._queries:
                async for msg in self._poll_query(query):
                    yield msg
            await asyncio.sleep(self._poll_seconds)

    async def fetch_history(self, source_name: str, limit: int = 100) -> list[RawMessage]:
        """One-shot fetch for a single query (source_name = the HN search query)."""
        messages: list[RawMessage] = []
        async for msg in self._poll_query(source_name):
            messages.append(msg)
            if len(messages) >= limit:
                break
        return messages

    async def _poll_query(self, query: str) -> AsyncIterator[RawMessage]:
        try:
            async with httpx.AsyncClient(
                timeout=15.0, headers={"User-Agent": self._fetcher.user_agent}
            ) as client:
                resp = await client.get(
                    f"{self._base_url}/search_by_date",
                    params={"query": query, "tags": "story"},
                )
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
        except Exception as e:
            logger.warning(f"Failed to query Hacker News for '{query}': {e}")
            return

        for hit in data.get("hits", []):
            object_id = hit.get("objectID")
            if not object_id:
                logger.warning("Skipping Hacker News hit with no objectID")
                continue
            if object_id in self._seen_ids:
                continue

            points = hit.get("points") or 0
            if points < self._min_points:
                continue

            timestamp = _parse_hn_date(hit.get("created_at"))
            if timestamp is None:
                logger.warning(f"Skipping Hacker News hit {object_id} with no created_at")
                continue

            title = (hit.get("title") or "").strip()
            external_url = hit.get("url")
            url = external_url or f"https://news.ycombinator.com/item?id={object_id}"

            body = (hit.get("story_text") or "").strip()
            if external_url:
                full_text = await self._fetcher.fetch(external_url)
                if full_text:
                    body = full_text

            text = f"{title}\n\n{body}".strip() if body else title
            if not text:
                logger.warning(f"Skipping Hacker News hit {object_id} with no usable text")
                continue

            self._seen_ids.add(object_id)

            yield RawMessage(
                external_id=str(object_id),
                source_name="Hacker News",
                source_type="hackernews",
                text=text,
                url=url,
                timestamp=timestamp,
            )


def _parse_hn_date(created_at: str | None) -> datetime | None:
    if not created_at:
        return None
    try:
        return datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
