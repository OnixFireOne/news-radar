"""Acceptance tests for the DEV Community API collector."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from collectors.base import RawMessage
from collectors.devto import DevtoCollector
from collectors.poll_runner import build_collectors

API = "https://dev.to/api/articles"


def article(article_id: int, hours_ago: float = 30, reactions: int = 10, disclosure: str = "no_ai") -> dict[str, object]:
    return {
        "id": article_id,
        "title": f"Title {article_id}",
        "url": f"https://dev.to/user/post-{article_id}?utm_source=feed",
        "published_at": (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(),
        "public_reactions_count": reactions,
        "comments_count": 3,
        "user": {"username": "writer"},
        "ai_disclosure_level": disclosure,
    }


def detail(article_id: int) -> httpx.Response:
    return httpx.Response(200, json={"title": f"Title {article_id}", "body_markdown": "Full article"})


async def poll(collector: DevtoCollector) -> list[RawMessage]:
    return [message async for message in collector._poll_cycle()]


@pytest.mark.asyncio
async def test_reactions_and_young_articles_are_rechecked() -> None:
    low = article(1, reactions=9)
    young = article(2, hours_ago=1)
    with respx.mock() as router:
        listing = router.get(API).mock(side_effect=[
            httpx.Response(200, json=[low, young]),
            httpx.Response(200, json=[article(1, reactions=10), article(2, hours_ago=25)]),
        ])
        router.get(f"{API}/1").mock(return_value=detail(1))
        router.get(f"{API}/2").mock(return_value=detail(2))
        collector = DevtoCollector()
        assert await poll(collector) == []
        with patch("collectors.devto.asyncio.sleep", new_callable=AsyncMock):
            messages = await poll(collector)
    assert listing.call_count == 2
    assert [message.external_id for message in messages] == ["1", "2"]


@pytest.mark.asyncio
async def test_old_disclosed_and_known_articles_skip_details() -> None:
    # The detail route exists only to prove it is never called.
    with respx.mock(assert_all_called=False) as router:
        router.get(API).mock(return_value=httpx.Response(200, json=[
            article(1, hours_ago=100), article(2, disclosure="fully_autonomous"), article(3),
        ]))
        detail_route = router.get(f"{API}/3").mock(return_value=detail(3))
        collector = DevtoCollector(is_known_url=lambda url: url == "https://dev.to/user/post-3")
        assert await poll(collector) == []
        assert await poll(collector) == []
    assert detail_route.call_count == 0
    assert collector._seen_ids == {"1", "2", "3"}


@pytest.mark.asyncio
async def test_duplicate_tags_and_message_fields() -> None:
    with respx.mock() as router:
        router.get(API).mock(return_value=httpx.Response(200, json=[article(4)]))
        details = router.get(f"{API}/4").mock(return_value=detail(4))
        collector = DevtoCollector(tags=["ai", "machinelearning"])
        messages = await poll(collector)
    assert len(messages) == 1
    message = messages[0]
    assert message.text == "Title 4\n\nFull article"
    assert message.url == "https://dev.to/user/post-4"
    assert (message.reactions_count, message.replies_count, message.post_author) == (10, 3, "writer")
    assert details.call_count == 1


@pytest.mark.asyncio
async def test_detail_failure_does_not_stop_next_article() -> None:
    with respx.mock() as router:
        router.get(API).mock(return_value=httpx.Response(200, json=[article(1), article(2)]))
        router.get(f"{API}/1").mock(return_value=httpx.Response(500))
        router.get(f"{API}/2").mock(return_value=detail(2))
        collector = DevtoCollector()
        with patch("collectors.devto.asyncio.sleep", new_callable=AsyncMock):
            messages = await poll(collector)
    assert [message.external_id for message in messages] == ["2"]


@pytest.mark.asyncio
async def test_detail_budget_waits_until_next_cycle() -> None:
    with respx.mock() as router:
        router.get(API).mock(return_value=httpx.Response(200, json=[article(1), article(2)]))
        first = router.get(f"{API}/1").mock(return_value=detail(1))
        second = router.get(f"{API}/2").mock(return_value=detail(2))
        collector = DevtoCollector(max_details_per_cycle=1)
        assert [message.external_id for message in await poll(collector)] == ["1"]
        assert [message.external_id for message in await poll(collector)] == ["2"]
    assert first.call_count == second.call_count == 1


@pytest.mark.asyncio
async def test_list_failure_does_not_stop_next_tag() -> None:
    with respx.mock() as router:
        router.get(API).mock(side_effect=[httpx.Response(500), httpx.Response(200, json=[article(5)])])
        router.get(f"{API}/5").mock(return_value=detail(5))
        messages = await poll(DevtoCollector(tags=["ai", "llm"]))
    assert [message.external_id for message in messages] == ["5"]


def test_build_collectors_uses_enabled_flag() -> None:
    assert not any(isinstance(item, DevtoCollector) for item in build_collectors({"devto": {"enabled": False}}))
    collectors = build_collectors({"devto": {"enabled": True, "tags": ["ai"]}})
    assert len(collectors) == 1
    assert isinstance(collectors[0], DevtoCollector)
