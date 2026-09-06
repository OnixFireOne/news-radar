"""
FullTextFetcher — polite full-article-text fetcher shared by rss.py and
hackernews.py, for feed/API entries that only carry a short snippet.

Politeness rules (ТЗ #4 И2):
  - fixed timeout and a distinct User-Agent on every request
  - a minimum delay between two requests to the same domain
  - a hard cap on fetches per poll cycle (reset via new_cycle()), so a large
    feed's first run can't hammer one site or fetch unbounded pages
Failure or low-quality extraction is not an error: fetch() returns None and
the caller keeps the feed/API snippet instead.
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse

import httpx
import trafilatura

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "news-radar/1.0"

# Below this many characters, trafilatura's output is treated as junk/empty
# (nav-only pages, paywalls, JS-rendered shells) — fall back to the snippet.
_MIN_EXTRACTED_CHARS = 200


class FullTextFetcher:
    """Fetches a URL's HTML and extracts readable article text via trafilatura."""

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 15.0,
        per_domain_delay_seconds: float = 2.0,
        max_fetches_per_cycle: int = 20,
    ) -> None:
        self.user_agent = user_agent
        self._timeout = timeout
        self._per_domain_delay = per_domain_delay_seconds
        self._max_per_cycle = max_fetches_per_cycle
        self._last_request_at: dict[str, float] = {}
        self._fetches_this_cycle = 0

    def new_cycle(self) -> None:
        """Call once at the start of each poll iteration to reset the per-cycle cap."""
        self._fetches_this_cycle = 0

    async def fetch(self, url: str) -> str | None:
        """Fetch `url` and return extracted article text, or None to keep the snippet."""
        if self._fetches_this_cycle >= self._max_per_cycle:
            logger.info(f"Full-text fetch cap reached ({self._max_per_cycle}/cycle) — skipping {url}")
            return None

        domain = urlparse(url).netloc
        await self._wait_for_domain(domain)

        self._fetches_this_cycle += 1
        self._last_request_at[domain] = time.monotonic()

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, headers={"User-Agent": self.user_agent}
            ) as client:
                resp = await client.get(url, follow_redirects=True)
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            logger.info(f"Full-text fetch failed for {url}: {e} — keeping snippet")
            return None

        extracted: str | None = trafilatura.extract(html)
        if not extracted or len(extracted.strip()) < _MIN_EXTRACTED_CHARS:
            logger.info(f"Full-text extraction too short/empty for {url} — keeping snippet")
            return None

        result: str = extracted.strip()
        return result

    async def _wait_for_domain(self, domain: str) -> None:
        last = self._last_request_at.get(domain)
        if last is None:
            return
        remaining = self._per_domain_delay - (time.monotonic() - last)
        if remaining > 0:
            await asyncio.sleep(remaining)
