"""
FullTextFetcher — ТЗ #4 И2/И2.1 acceptance: polite fetch (timeout/UA already
covered by construction), per-domain pacing, per-cycle and per-feed caps,
a domain-level circuit breaker after 401/403, and junk/empty extraction
falling back to None (not an error) instead of failing.
"""

import httpx
import pytest
import respx

from collectors.fulltext_fetcher import FullTextFetcher

URL = "https://example.com/article-1"
FEED = "feed-a"


@pytest.mark.asyncio
async def test_returns_extracted_text_when_long_enough(mocker) -> None:
    mocker.patch("collectors.fulltext_fetcher.trafilatura.extract", return_value="x" * 300)
    with respx.mock() as router:
        router.get(URL).mock(return_value=httpx.Response(200, text="<html>ok</html>"))
        fetcher = FullTextFetcher()
        result = await fetcher.fetch(URL, feed_key=FEED)
    assert result == "x" * 300


@pytest.mark.asyncio
async def test_junk_or_empty_extraction_falls_back_to_none_not_error(mocker) -> None:
    mocker.patch("collectors.fulltext_fetcher.trafilatura.extract", return_value="too short")
    with respx.mock() as router:
        router.get(URL).mock(return_value=httpx.Response(200, text="<html>nav only</html>"))
        fetcher = FullTextFetcher()
        result = await fetcher.fetch(URL, feed_key=FEED)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_failure_falls_back_to_none_not_error(mocker) -> None:
    mocker.patch("collectors.fulltext_fetcher.trafilatura.extract", return_value="x" * 300)
    with respx.mock() as router:
        router.get(URL).mock(side_effect=httpx.ConnectError("boom"))
        fetcher = FullTextFetcher()
        result = await fetcher.fetch(URL, feed_key=FEED)
    assert result is None


@pytest.mark.asyncio
async def test_per_cycle_cap_stops_fetching_without_hitting_network(mocker) -> None:
    mocker.patch("collectors.fulltext_fetcher.trafilatura.extract", return_value="x" * 300)
    with respx.mock() as router:
        route = router.get(url__regex=r".*").mock(return_value=httpx.Response(200, text="<html>ok</html>"))
        fetcher = FullTextFetcher(max_fetches_per_cycle=2, max_fetches_per_feed=10)
        fetcher.new_cycle()
        assert await fetcher.fetch("https://a.example.com/1", feed_key=FEED) == "x" * 300
        assert await fetcher.fetch("https://b.example.com/2", feed_key=FEED) == "x" * 300
        assert await fetcher.fetch("https://c.example.com/3", feed_key=FEED) is None  # cap reached, no request made
        assert route.call_count == 2


@pytest.mark.asyncio
async def test_new_cycle_resets_the_cap(mocker) -> None:
    mocker.patch("collectors.fulltext_fetcher.trafilatura.extract", return_value="x" * 300)
    with respx.mock() as router:
        router.get(url__regex=r".*").mock(return_value=httpx.Response(200, text="<html>ok</html>"))
        fetcher = FullTextFetcher(max_fetches_per_cycle=1, max_fetches_per_feed=10)
        fetcher.new_cycle()
        assert await fetcher.fetch("https://a.example.com/1", feed_key=FEED) is not None
        assert await fetcher.fetch("https://a.example.com/2", feed_key=FEED) is None  # cap reached
        fetcher.new_cycle()
        assert await fetcher.fetch("https://a.example.com/3", feed_key=FEED) is not None  # cap reset


@pytest.mark.asyncio
async def test_same_domain_waits_out_the_configured_delay(mocker) -> None:
    mocker.patch("collectors.fulltext_fetcher.trafilatura.extract", return_value="x" * 300)
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    mocker.patch("collectors.fulltext_fetcher.asyncio.sleep", side_effect=fake_sleep)

    with respx.mock() as router:
        router.get(url__regex=r".*").mock(return_value=httpx.Response(200, text="<html>ok</html>"))
        fetcher = FullTextFetcher(per_domain_delay_seconds=5.0)
        await fetcher.fetch("https://same-domain.example.com/1", feed_key=FEED)
        await fetcher.fetch("https://same-domain.example.com/2", feed_key=FEED)

    assert len(sleep_calls) == 1
    assert 0 < sleep_calls[0] <= 5.0


@pytest.mark.asyncio
async def test_per_feed_cap_stops_fetching_but_other_feeds_keep_their_budget(mocker) -> None:
    mocker.patch("collectors.fulltext_fetcher.trafilatura.extract", return_value="x" * 300)
    with respx.mock() as router:
        route = router.get(url__regex=r".*").mock(return_value=httpx.Response(200, text="<html>ok</html>"))
        fetcher = FullTextFetcher(max_fetches_per_cycle=20, max_fetches_per_feed=1)
        fetcher.new_cycle()
        assert await fetcher.fetch("https://a.example.com/1", feed_key="feed-a") == "x" * 300
        assert await fetcher.fetch("https://a.example.com/2", feed_key="feed-a") is None  # feed-a cap reached
        assert await fetcher.fetch("https://b.example.com/1", feed_key="feed-b") == "x" * 300  # feed-b untouched
        assert route.call_count == 2


@pytest.mark.asyncio
async def test_403_blocks_the_domain_for_the_rest_of_the_cycle(mocker) -> None:
    mocker.patch("collectors.fulltext_fetcher.trafilatura.extract", return_value="x" * 300)
    with respx.mock() as router:
        route = router.get(url__regex=r".*").mock(return_value=httpx.Response(403))
        fetcher = FullTextFetcher(max_fetches_per_feed=10)
        fetcher.new_cycle()
        assert await fetcher.fetch("https://blocked.example.com/1", feed_key=FEED) is None
        assert await fetcher.fetch("https://blocked.example.com/2", feed_key=FEED) is None
        # Second call never hit the network — blocked at the domain circuit breaker.
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_domain_block_is_cleared_by_new_cycle(mocker) -> None:
    mocker.patch("collectors.fulltext_fetcher.trafilatura.extract", return_value="x" * 300)
    with respx.mock() as router:
        route = router.get(url__regex=r".*").mock(return_value=httpx.Response(403))
        fetcher = FullTextFetcher(max_fetches_per_feed=10)
        fetcher.new_cycle()
        assert await fetcher.fetch("https://blocked.example.com/1", feed_key=FEED) is None
        route.mock(return_value=httpx.Response(200, text="<html>ok</html>"))
        fetcher.new_cycle()
        assert await fetcher.fetch("https://blocked.example.com/2", feed_key=FEED) == "x" * 300
