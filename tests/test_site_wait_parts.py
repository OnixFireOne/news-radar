"""Bot site-page probe and per-part wait results (pytest, run with the bot folder mounted)."""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest


@pytest.mark.asyncio
async def test_zero_timeout_probe_and_part_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot import site_wait
    client = AsyncMock()
    client.get.return_value = Mock(status_code=200)
    manager = AsyncMock()
    manager.__aenter__.return_value = client
    with patch("bot.site_wait.httpx.AsyncClient", return_value=manager):
        assert await site_wait.wait_for_site_page("https://example.org", 0)
        client.get.assert_awaited_once()
    wait = AsyncMock(return_value=False)
    monkeypatch.setattr(site_wait, "wait_for_site_page", wait)
    assert await site_wait.wait_for_parts([{}, {"site_url": "https://example.org"}], {}) == [True, False]
