"""
FullTextFetcher — polite full-article-text fetcher shared by rss.py and
hackernews.py, for feed/API entries that only carry a short snippet.

Politeness rules (ТЗ #4 И2, И2.1):
  - fixed timeout and a distinct User-Agent on every request
  - a minimum delay between two requests to the same domain
  - a hard cap on fetches per poll cycle (reset via new_cycle()), so a large
    feed's first run can't hammer one site or fetch unbounded pages
  - a per-feed/query cap (feed_key), so one feed early in iteration order
    can't eat the whole cycle's budget
  - a domain that answers 401/403 is skipped for the rest of the cycle —
    retrying it per-entry just burns the cycle budget on a site that will
    never let us in (found in И2 acceptance: openai.com 403s on every UA)
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
        max_fetches_per_feed: int = 5,
    ) -> None:
        self.user_agent = user_agent
        self._timeout = timeout
        self._per_domain_delay = per_domain_delay_seconds
        self._max_per_cycle = max_fetches_per_cycle
        self._max_per_feed = max_fetches_per_feed
        self._last_request_at: dict[str, float] = {}
        self._fetches_this_cycle = 0
        self._fetches_this_feed: dict[str, int] = {}
        self._blocked_domains: set[str] = set()

    def new_cycle(self) -> None:
        """Call once at the start of each poll iteration to reset all per-cycle limits."""
        self._fetches_this_cycle = 0
        self._fetches_this_feed.clear()
        self._blocked_domains.clear()

    async def fetch(self, url: str, feed_key: str) -> str | None:
        """Fetch `url` and return extracted article text, or None to keep the snippet.

        `feed_key` identifies the feed/query the URL came from, for the
        per-feed fetch cap — it keeps one large feed from consuming the
        whole cycle's fetch budget before other feeds get a turn.
        """
        domain = urlparse(url).netloc

        if domain in self._blocked_domains:
            logger.info(f"Domain {domain} blocked for this cycle (earlier 401/403) — skipping {url}")
            return None
        if self._fetches_this_cycle >= self._max_per_cycle:
            logger.info(f"Full-text fetch cap reached ({self._max_per_cycle}/cycle) — skipping {url}")
            return None
        if self._fetches_this_feed.get(feed_key, 0) >= self._max_per_feed:
            logger.info(f"Full-text fetch cap reached ({self._max_per_feed}/feed) for {feed_key} — skipping {url}")
            return None

        await self._wait_for_domain(domain)

        self._fetches_this_cycle += 1
        self._fetches_this_feed[feed_key] = self._fetches_this_feed.get(feed_key, 0) + 1
        self._last_request_at[domain] = time.monotonic()

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, headers={"User-Agent": self.user_agent}
            ) as client:
                resp = await client.get(url, follow_redirects=True)
                resp.raise_for_status()
                html = resp.text
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                self._blocked_domains.add(domain)
                logger.info(f"Domain {domain} returned {e.response.status_code} — blocking it for this cycle")
            else:
                logger.info(f"Full-text fetch failed for {url}: {e} — keeping snippet")
            return None
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
