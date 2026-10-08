"""Wait until a freshly committed site page is deployed before announcing it in Telegram."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)


async def wait_for_site_page(url: str, timeout: float, interval: float = 15) -> bool:
    """Poll with one bounded request per interval; a slow or failed deployment is nonfatal."""
    deadline = time.monotonic() + max(0.0, timeout)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        while time.monotonic() < deadline:
            started = time.monotonic()
            try:
                response = await client.get(url, timeout=min(10.0, deadline - started))
                if response.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            remaining = deadline - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(min(max(0.0, interval - (time.monotonic() - started)), remaining))
    logger.warning("Site digest page wait timed out: %s", url)
    return False


async def wait_for_parts(parts: list[dict[str, object]], settings: object) -> None:
    """Block until every part's site page answers; the API sets ``site_url`` only when the site is live."""
    site = settings.get("site") if isinstance(settings, dict) else None
    timeout = float(site.get("wait_for_page_sec", 300)) if isinstance(site, dict) else 300.0
    for part in parts:
        url = part.get("site_url")
        if isinstance(url, str) and url:
            await wait_for_site_page(url, timeout)
