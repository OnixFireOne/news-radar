"""
RssCollector — ТЗ #4 И2 acceptance: a broken entry (no link/date) is skipped
with a warning rather than aborting the whole poll cycle; a long-enough
snippet skips the full-text fetch; URL dedup across polls of the same feed.
"""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from collectors.rss import RssCollector

FEED_URL = "https://blog.example.com/feed.xml"


def _rss_xml(items: str) -> str:
    return f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Example Blog</title>
{items}
</channel></rss>"""


_GOOD_ITEM = """
<item>
  <title>A real post</title>
  <link>https://blog.example.com/post-1</link>
  <guid>https://blog.example.com/post-1</guid>
  <pubDate>Mon, 01 Sep 2025 12:00:00 GMT</pubDate>
  <description>{snippet}</description>
</item>
"""

_NO_LINK_ITEM = """
<item>
  <title>Missing link</title>
  <pubDate>Mon, 01 Sep 2025 12:00:00 GMT</pubDate>
  <description>no link here</description>
</item>
"""

_NO_DATE_ITEM = """
<item>
  <title>Missing date</title>
  <link>https://blog.example.com/post-2</link>
  <guid>https://blog.example.com/post-2</guid>
  <description>no date here</description>
</item>
"""


@pytest.mark.asyncio
async def test_broken_entries_are_skipped_not_fatal(caplog) -> None:
    xml = _rss_xml(_GOOD_ITEM.format(snippet="x" * 600) + _NO_LINK_ITEM + _NO_DATE_ITEM)
    with respx.mock() as router:
        router.get(FEED_URL).mock(return_value=httpx.Response(200, text=xml))
        collector = RssCollector(feeds=[FEED_URL])
        messages = [msg async for msg in collector._poll_feed(FEED_URL)]

    assert len(messages) == 1
    assert messages[0].url == "https://blog.example.com/post-1"
    assert any("no link" in r.message.lower() for r in caplog.records)
    assert any("no date" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_long_snippet_skips_full_text_fetch() -> None:
    xml = _rss_xml(_GOOD_ITEM.format(snippet="x" * 600))
    with respx.mock() as router:
        router.get(FEED_URL).mock(return_value=httpx.Response(200, text=xml))
        fetcher = AsyncMock()
        fetcher.user_agent = "news-radar/1.0"
        collector = RssCollector(feeds=[FEED_URL], fetcher=fetcher)
        messages = [msg async for msg in collector._poll_feed(FEED_URL)]

    assert len(messages) == 1
    assert messages[0].text == "x" * 600
    fetcher.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_short_snippet_triggers_full_text_fetch_and_uses_result() -> None:
    xml = _rss_xml(_GOOD_ITEM.format(snippet="short teaser"))
    with respx.mock() as router:
        router.get(FEED_URL).mock(return_value=httpx.Response(200, text=xml))
        fetcher = AsyncMock()
        fetcher.fetch.return_value = "full article text " * 50
        fetcher.user_agent = "news-radar/1.0"
        collector = RssCollector(feeds=[FEED_URL], fetcher=fetcher)
        messages = [msg async for msg in collector._poll_feed(FEED_URL)]

    assert len(messages) == 1
    assert messages[0].text == "full article text " * 50
    fetcher.fetch.assert_called_once_with("https://blog.example.com/post-1")


@pytest.mark.asyncio
async def test_same_url_not_yielded_twice_across_polls() -> None:
    xml = _rss_xml(_GOOD_ITEM.format(snippet="x" * 600))
    with respx.mock() as router:
        router.get(FEED_URL).mock(return_value=httpx.Response(200, text=xml))
        collector = RssCollector(feeds=[FEED_URL])
        first = [msg async for msg in collector._poll_feed(FEED_URL)]
        second = [msg async for msg in collector._poll_feed(FEED_URL)]

    assert len(first) == 1
    assert len(second) == 0


@pytest.mark.asyncio
async def test_unreachable_feed_yields_nothing_without_raising(caplog) -> None:
    with respx.mock() as router:
        router.get(FEED_URL).mock(side_effect=httpx.ConnectError("boom"))
        collector = RssCollector(feeds=[FEED_URL])
        messages = [msg async for msg in collector._poll_feed(FEED_URL)]

    assert messages == []
    assert any("failed to fetch feed" in r.message.lower() for r in caplog.records)
