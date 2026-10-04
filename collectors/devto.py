"""Poll DEV Community articles with reader reactions through its public API."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable

import httpx

from collectors.base import BaseCollector, RawMessage
from collectors.url_utils import normalize_url

logger = logging.getLogger(__name__)
DEFAULT_BASE_URL = "https://dev.to/api"


class DevtoCollector(BaseCollector):
    source_type = "devto"

    def __init__(
        self,
        tags: list[str] | None = None,
        top_days: int = 3,
        min_reactions: int = 10,
        min_age_hours: int = 24,
        max_age_hours: int = 72,
        skip_ai_disclosure: list[str] | None = None,
        poll_minutes: int = 60,
        per_page: int = 100,
        max_details_per_cycle: int = 30,
        base_url: str = DEFAULT_BASE_URL,
        is_known_url: Callable[[str], bool] | None = None,
    ) -> None:
        self._tags = tags if tags is not None else ["ai"]
        self._top_days = top_days
        self._min_reactions = min_reactions
        self._min_age_hours = min_age_hours
        self._max_age_hours = max_age_hours
        self._skip_ai_disclosure = set(skip_ai_disclosure if skip_ai_disclosure is not None else ["fully_autonomous"])
        self._poll_seconds = poll_minutes * 60
        self._per_page = per_page
        self._max_details_per_cycle = max_details_per_cycle
        self._base_url = base_url.rstrip("/")
        self._is_known_url = is_known_url or (lambda _url: False)
        self._seen_ids: set[str] = set()
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    # BaseCollector.listen is inferred as a coroutine instead of an async generator.
    async def listen(self) -> AsyncIterator[RawMessage]:  # type: ignore[override, misc]
        while self._running:
            async for message in self._poll_cycle():
                yield message
            await asyncio.sleep(self._poll_seconds)

    async def fetch_history(self, source_name: str, limit: int = 100) -> list[RawMessage]:
        messages: list[RawMessage] = []
        async for message in self._poll_cycle([source_name]):
            messages.append(message)
            if len(messages) >= limit:
                break
        return messages

    async def _poll_cycle(self, tags: list[str] | None = None) -> AsyncIterator[RawMessage]:
        details_count = 0
        now = datetime.now(timezone.utc)
        async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": "news-radar/1.0"}) as client:
            for tag in tags if tags is not None else self._tags:
                try:
                    response = await client.get(
                        f"{self._base_url}/articles",
                        params={"tag": tag, "top": self._top_days, "per_page": self._per_page, "page": 1},
                    )
                    response.raise_for_status()
                    articles: list[dict[str, Any]] = response.json()
                except Exception as exc:
                    logger.warning("Failed to query DEV Community tag '%s': %s", tag, exc)
                    continue

                for article in articles:
                    article_id = article.get("id")
                    if article_id is None:
                        logger.warning("Skipping DEV Community article with no id")
                        continue
                    key = str(article_id)
                    if key in self._seen_ids:
                        continue
                    if (article.get("public_reactions_count") or 0) < self._min_reactions:
                        continue
                    timestamp = _parse_date(article.get("published_at"))
                    if timestamp is None:
                        logger.warning("Skipping DEV Community article %s with no published_at", key)
                        continue
                    age_hours = (now - timestamp).total_seconds() / 3600
                    if age_hours < self._min_age_hours:
                        continue
                    if age_hours > self._max_age_hours:
                        self._seen_ids.add(key)
                        continue
                    if article.get("ai_disclosure_level") in self._skip_ai_disclosure:
                        self._seen_ids.add(key)
                        continue
                    raw_url = article.get("url")
                    if not raw_url:
                        logger.warning("Skipping DEV Community article %s with no url", key)
                        continue
                    url = normalize_url(raw_url)
                    if self._is_known_url(url):
                        self._seen_ids.add(key)
                        continue
                    if details_count >= self._max_details_per_cycle:
                        continue
                    if details_count:
                        await asyncio.sleep(0.5)
                    details_count += 1
                    try:
                        detail_response = await client.get(f"{self._base_url}/articles/{key}")
                        detail_response.raise_for_status()
                        detail: dict[str, Any] = detail_response.json()
                        title = (detail.get("title") or article.get("title") or "").strip()
                        body = (detail.get("body_markdown") or "").strip()
                        if not title:
                            logger.warning("Skipping DEV Community article %s with no title", key)
                            continue
                    except Exception as exc:
                        logger.warning("Failed to fetch DEV Community article %s: %s", key, exc)
                        continue
                    self._seen_ids.add(key)
                    yield RawMessage(
                        external_id=key,
                        source_name="DEV Community",
                        source_type="devto",
                        text=f"{title}\n\n{body}",
                        timestamp=timestamp,
                        url=url,
                        reactions_count=article.get("public_reactions_count") or 0,
                        replies_count=article.get("comments_count") or 0,
                        post_author=(article.get("user") or {}).get("username"),
                    )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
