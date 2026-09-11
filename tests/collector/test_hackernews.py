"""
HackerNewsCollector — ТЗ #4 И2/И2.1 acceptance: min_points and the age
window are pushed into the Algolia request (numericFilters/hitsPerPage);
the client-side min_points/age checks remain a safety net; a hit missing
objectID/created_at is skipped with a warning (not fatal); self-posts use
story_text without a full-text fetch; external links do fetch full text;
a URL already known (is_known_url) is skipped before the fetch; the stored
URL is normalized; and dedup by objectID holds across polls.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from collectors.hackernews import HackerNewsCollector

API = "https://hn.algolia.com/api/v1/search_by_date"


def _iso(hours_ago: float = 1.0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _hits(hits: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"hits": hits})


@pytest.mark.asyncio
async def test_min_points_filters_out_low_score_stories() -> None:
    with respx.mock() as router:
        router.get(API).mock(
            return_value=_hits(
                [
                    {"objectID": "1", "title": "low", "points": 5, "created_at": _iso()},
                    {"objectID": "2", "title": "high", "points": 50, "created_at": _iso()},
                ]
            )
        )
        collector = HackerNewsCollector(queries=["llm"], min_points=30)
        messages = [msg async for msg in collector._poll_query("llm")]

    assert [m.external_id for m in messages] == ["2"]


@pytest.mark.asyncio
async def test_hits_missing_id_or_date_are_skipped_not_fatal(caplog) -> None:
    with respx.mock() as router:
        router.get(API).mock(
            return_value=_hits(
                [
                    {"title": "no id", "points": 50, "created_at": _iso()},
                    {"objectID": "3", "title": "no date", "points": 50},
                    {"objectID": "4", "title": "ok", "points": 50, "created_at": _iso()},
                ]
            )
        )
        collector = HackerNewsCollector(queries=["llm"], min_points=30)
        messages = [msg async for msg in collector._poll_query("llm")]

    assert [m.external_id for m in messages] == ["4"]
    assert any("objectid" in r.message.lower() for r in caplog.records)
    assert any("created_at" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_self_post_uses_story_text_without_full_text_fetch() -> None:
    with respx.mock() as router:
        router.get(API).mock(
            return_value=_hits(
                [
                    {
                        "objectID": "5",
                        "title": "Ask HN: something",
                        "points": 50,
                        "created_at": _iso(),
                        "story_text": "the actual question body",
                        "url": None,
                    }
                ]
            )
        )
        fetcher = AsyncMock()
        fetcher.user_agent = "news-radar/1.0"
        collector = HackerNewsCollector(queries=["llm"], min_points=30, fetcher=fetcher)
        messages = [msg async for msg in collector._poll_query("llm")]

    assert len(messages) == 1
    assert "the actual question body" in messages[0].text
    assert messages[0].url == "https://news.ycombinator.com/item?id=5"
    fetcher.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_external_link_triggers_full_text_fetch_and_uses_result() -> None:
    with respx.mock() as router:
        router.get(API).mock(
            return_value=_hits(
                [
                    {
                        "objectID": "6",
                        "title": "Cool tool",
                        "points": 50,
                        "created_at": _iso(),
                        "url": "https://example.com/tool",
                    }
                ]
            )
        )
        fetcher = AsyncMock()
        fetcher.fetch.return_value = "full extracted article " * 20
        fetcher.user_agent = "news-radar/1.0"
        collector = HackerNewsCollector(queries=["llm"], min_points=30, fetcher=fetcher)
        messages = [msg async for msg in collector._poll_query("llm")]

    assert len(messages) == 1
    assert "full extracted article" in messages[0].text
    assert messages[0].url == "https://example.com/tool"
    fetcher.fetch.assert_called_once_with("https://example.com/tool", feed_key="llm")


@pytest.mark.asyncio
async def test_same_story_not_yielded_twice_across_polls() -> None:
    with respx.mock() as router:
        router.get(API).mock(return_value=_hits([{"objectID": "7", "title": "ok", "points": 50, "created_at": _iso()}]))
        collector = HackerNewsCollector(queries=["llm"], min_points=30)
        first = [msg async for msg in collector._poll_query("llm")]
        second = [msg async for msg in collector._poll_query("llm")]

    assert len(first) == 1
    assert len(second) == 0


@pytest.mark.asyncio
async def test_request_includes_points_and_age_filters_and_hits_per_page() -> None:
    with respx.mock() as router:
        route = router.get(API).mock(return_value=_hits([]))
        collector = HackerNewsCollector(queries=["llm"], min_points=42, hits_per_page=17, max_age_hours=48)
        [msg async for msg in collector._poll_query("llm")]

    request = route.calls.last.request
    params = parse_qs(str(request.url.params))
    assert params["hitsPerPage"] == ["17"]
    numeric_filters = params["numericFilters"][0]
    assert "points>=42" in numeric_filters
    assert "created_at_i>" in numeric_filters


@pytest.mark.asyncio
async def test_hits_older_than_max_age_are_skipped_as_safety_net(caplog) -> None:
    stale_iso = _iso(hours_ago=200)
    with respx.mock() as router:
        router.get(API).mock(
            return_value=_hits(
                [
                    {"objectID": "8", "title": "fresh", "points": 50, "created_at": _iso(hours_ago=1)},
                    {"objectID": "9", "title": "stale", "points": 50, "created_at": stale_iso},
                ]
            )
        )
        with caplog.at_level("INFO"):
            collector = HackerNewsCollector(queries=["llm"], min_points=30, max_age_hours=72)
            messages = [msg async for msg in collector._poll_query("llm")]

    assert [m.external_id for m in messages] == ["8"]
    info_lines = [r.message for r in caplog.records if r.levelname == "INFO" and "skipped 1" in r.message.lower()]
    assert len(info_lines) == 1


@pytest.mark.asyncio
async def test_known_url_from_db_is_skipped_before_fetch() -> None:
    with respx.mock() as router:
        router.get(API).mock(
            return_value=_hits(
                [{"objectID": "10", "title": "known", "points": 50, "created_at": _iso(), "url": "https://example.com/known"}]
            )
        )
        fetcher = AsyncMock()
        fetcher.user_agent = "news-radar/1.0"
        collector = HackerNewsCollector(
            queries=["llm"], min_points=30, fetcher=fetcher, is_known_url=lambda url: url == "https://example.com/known"
        )
        messages = [msg async for msg in collector._poll_query("llm")]

    assert messages == []
    fetcher.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_url_is_normalized_before_storage() -> None:
    with respx.mock() as router:
        router.get(API).mock(
            return_value=_hits(
                [
                    {
                        "objectID": "11",
                        "title": "tracked",
                        "points": 50,
                        "created_at": _iso(),
                        "url": "https://example.com/tool/?utm_source=hn&fbclid=abc",
                    }
                ]
            )
        )
        fetcher = AsyncMock()
        fetcher.fetch.return_value = None
        fetcher.user_agent = "news-radar/1.0"
        collector = HackerNewsCollector(queries=["llm"], min_points=30, fetcher=fetcher)
        messages = [msg async for msg in collector._poll_query("llm")]

    assert len(messages) == 1
    assert messages[0].url == "https://example.com/tool"
