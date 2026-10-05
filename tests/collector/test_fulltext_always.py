"""Full-text modes and retrying entries deferred by fetch budgets."""

from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Literal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from collectors.fulltext_fetcher import FullTextFetcher
from collectors.hackernews import HackerNewsCollector
from collectors.poll_runner import build_collectors
from collectors.rss import RssCollector

FEED = "https://example.com/feed"
URL = "https://example.com/article"


def feed_xml(snippet: str, count: int = 1) -> str:
    date = format_datetime(datetime.now(timezone.utc), usegmt=True)
    items = "".join(
        f"<item><title>Article</title><link>{URL}/{index}</link>"
        f"<pubDate>{date}</pubDate><description>{snippet}</description></item>"
        for index in range(count)
    )
    return f'<rss version="2.0"><channel><title>Feed</title>{items}</channel></rss>'


@pytest.mark.parametrize("cycle_cap,feed_cap", [(1, 10), (10, 1)])
@pytest.mark.asyncio
async def test_last_capped_resets_after_success(cycle_cap: int, feed_cap: int) -> None:
    fetcher = FullTextFetcher(per_domain_delay_seconds=0,
                              max_fetches_per_cycle=cycle_cap, max_fetches_per_feed=feed_cap)
    assert fetcher.last_capped is False
    with respx.mock() as router, patch("collectors.fulltext_fetcher.trafilatura.extract", return_value="x" * 300):
        route = router.get(URL).respond(200, text="article")
        assert await fetcher.fetch(URL, FEED) == "x" * 300
        assert fetcher.last_capped is False
        assert await fetcher.fetch(URL, FEED) is None
        assert fetcher.last_capped is True
        assert route.call_count == 1
        fetcher.new_cycle()
        assert await fetcher.fetch(URL, FEED) == "x" * 300
        assert fetcher.last_capped is False


@pytest.mark.parametrize("status,extracted", [(403, "x" * 300), (500, "x" * 300), (200, "short")])
@pytest.mark.asyncio
async def test_last_capped_false_on_failed_fetch(status: int, extracted: str) -> None:
    fetcher = FullTextFetcher(per_domain_delay_seconds=0, max_fetches_per_cycle=1)
    with respx.mock() as router, patch("collectors.fulltext_fetcher.trafilatura.extract", return_value=extracted):
        router.get(URL).respond(status, text="article")
        assert await fetcher.fetch(URL, FEED) is None
        assert fetcher.last_capped is False
        assert await fetcher.fetch("https://other.example/article", FEED) is None
        assert fetcher.last_capped is True
        if status != 403:
            fetcher.new_cycle()
        # A blocked domain also clears a previous cap result without a request.
        assert await fetcher.fetch(URL, FEED) is None
        assert fetcher.last_capped is False


@pytest.mark.asyncio
async def test_network_failure_is_not_a_cap() -> None:
    fetcher = FullTextFetcher()
    fetcher.last_capped = True
    with respx.mock() as router:
        router.get(URL).mock(side_effect=httpx.ConnectError("offline"))
        assert await fetcher.fetch(URL, FEED) is None
    assert fetcher.last_capped is False


@pytest.mark.parametrize("page", ["p" * 900, "p" * 300, "p" * 600, None])
@pytest.mark.asyncio
async def test_always_keeps_the_longer_text(page: str | None) -> None:
    fetcher = AsyncMock()
    fetcher.user_agent = "news-radar/1.0"
    fetcher.fetch.return_value = page
    # Leave last_capped as a truthy mock to verify the identity check.
    with respx.mock() as router, patch("collectors.rss.trafilatura.extract", return_value=None):
        router.get(FEED).respond(200, text=feed_xml("s" * 600))
        collector = RssCollector([FEED], fetcher=fetcher, fulltext_mode="always")
        messages = [msg async for msg in collector._poll_feed(FEED)]
    assert len(messages) == 1
    assert messages[0].text == (page if page and len(page) > 600 else "s" * 600)
    fetcher.fetch.assert_awaited_once_with(URL + "/0", feed_key=FEED)


@pytest.mark.asyncio
async def test_short_only_does_not_fetch_long_snippet() -> None:
    fetcher = AsyncMock()
    fetcher.user_agent = "news-radar/1.0"
    with respx.mock() as router, patch("collectors.rss.trafilatura.extract", return_value=None):
        router.get(FEED).respond(200, text=feed_xml("s" * 600))
        collector = RssCollector([FEED], fetcher=fetcher, fulltext_mode="short_only")
        messages = [msg async for msg in collector._poll_feed(FEED)]
    assert [msg.text for msg in messages] == ["s" * 600]
    fetcher.fetch.assert_not_awaited()


@pytest.mark.parametrize("mode", ["short_only", "always"])
@pytest.mark.asyncio
async def test_rss_retries_deferred_entries(
    mode: Literal["short_only", "always"], caplog: pytest.LogCaptureFixture,
) -> None:
    fetcher = FullTextFetcher(per_domain_delay_seconds=0, max_fetches_per_cycle=2)
    with respx.mock() as router, patch("collectors.fulltext_fetcher.trafilatura.extract", return_value=None):
        router.get(FEED).respond(200, text=feed_xml("snippet", count=2))
        router.get(URL).respond(200, text="article")
        router.get(URL + "/0").respond(200, text="article")
        router.get(URL + "/1").respond(200, text="article")
        await fetcher.fetch(URL, "other")
        await fetcher.fetch(URL, "other")
        collector = RssCollector([FEED], fetcher=fetcher, fulltext_mode=mode)
        with caplog.at_level("INFO", logger="collectors.rss"):
            assert [msg async for msg in collector._poll_feed(FEED)] == []
        assert collector._seen_urls == set()
        lines = [r.message for r in caplog.records if r.name == "collectors.rss" and "Deferred" in r.message]
        assert len(lines) == 1
        assert "Deferred 2 entries" in lines[0]
        fetcher.new_cycle()
        assert len([msg async for msg in collector._poll_feed(FEED)]) == 2
        assert [msg async for msg in collector._poll_feed(FEED)] == []


@pytest.mark.asyncio
async def test_hackernews_retries_capped_hit() -> None:
    fetcher = FullTextFetcher(per_domain_delay_seconds=0, max_fetches_per_cycle=1)
    with respx.mock() as router, patch("collectors.fulltext_fetcher.trafilatura.extract", return_value=None):
        router.get(URL).respond(200, text="article")
        router.get("https://hn.algolia.com/api/v1/search_by_date").respond(200, json={"hits": [{
            "objectID": "123", "points": 100, "title": "Article", "url": URL,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }]})
        await fetcher.fetch(URL, "other")
        collector = HackerNewsCollector(["ai"], fetcher=fetcher)
        assert [msg async for msg in collector._poll_query("ai")] == []
        assert collector._seen_ids == set()
        fetcher.new_cycle()
        assert len([msg async for msg in collector._poll_query("ai")]) == 1
        assert collector._seen_ids == {"123"}


@pytest.mark.parametrize("mode,expected", [("always", "always"), ("short_only", "short_only"),
                                            ("invalid", "short_only"), (None, "short_only")])
def test_runner_passes_mode(mode: str | None, expected: str, caplog: pytest.LogCaptureFixture) -> None:
    collectors = build_collectors({
        "rss": {"enabled": True, "feeds": [FEED]},
        "fulltext": {} if mode is None else {"mode": mode},
    })
    collector = collectors[0]
    assert isinstance(collector, RssCollector)
    assert collector._fulltext_mode == expected
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert bool(warnings) == (mode == "invalid")


@pytest.mark.asyncio
@pytest.mark.parametrize("include", [True, False])
async def test_comments_switch_reaches_trafilatura(include: bool) -> None:
    fetcher = FullTextFetcher(per_domain_delay_seconds=0, include_comments=include)
    with respx.mock() as router, patch("collectors.fulltext_fetcher.trafilatura.extract",
                                       return_value="x" * 300) as extract:
        router.get("https://example.com/a").mock(return_value=httpx.Response(200, text="<html></html>"))
        assert await fetcher.fetch("https://example.com/a", "feed")
    assert extract.call_args.kwargs["include_comments"] is include
