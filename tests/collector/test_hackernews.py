"""
HackerNewsCollector — ТЗ #4 И2 acceptance: min_points filter, a hit missing
objectID/created_at is skipped with a warning (not fatal), self-posts use
story_text without a full-text fetch, external links do fetch full text,
and dedup by objectID across polls.
"""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from collectors.hackernews import HackerNewsCollector

API = "https://hn.algolia.com/api/v1/search_by_date"


def _hits(hits: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"hits": hits})


@pytest.mark.asyncio
async def test_min_points_filters_out_low_score_stories() -> None:
    with respx.mock() as router:
        router.get(API).mock(
            return_value=_hits(
                [
                    {"objectID": "1", "title": "low", "points": 5, "created_at": "2025-09-01T12:00:00.000Z"},
                    {"objectID": "2", "title": "high", "points": 50, "created_at": "2025-09-01T12:00:00.000Z"},
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
                    {"title": "no id", "points": 50, "created_at": "2025-09-01T12:00:00.000Z"},
                    {"objectID": "3", "title": "no date", "points": 50},
                    {"objectID": "4", "title": "ok", "points": 50, "created_at": "2025-09-01T12:00:00.000Z"},
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
                        "created_at": "2025-09-01T12:00:00.000Z",
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
                        "created_at": "2025-09-01T12:00:00.000Z",
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
    fetcher.fetch.assert_called_once_with("https://example.com/tool")


@pytest.mark.asyncio
async def test_same_story_not_yielded_twice_across_polls() -> None:
    with respx.mock() as router:
        router.get(API).mock(
            return_value=_hits(
                [{"objectID": "7", "title": "ok", "points": 50, "created_at": "2025-09-01T12:00:00.000Z"}]
            )
        )
        collector = HackerNewsCollector(queries=["llm"], min_points=30)
        first = [msg async for msg in collector._poll_query("llm")]
        second = [msg async for msg in collector._poll_query("llm")]

    assert len(first) == 1
    assert len(second) == 0
