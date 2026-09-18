"""
RssCollector — ТЗ #4 И2/И2.1 acceptance: a broken entry (no link/date) is
skipped with a warning rather than aborting the whole poll cycle; a
long-enough snippet skips the full-text fetch; URL dedup across polls of
the same feed; entries older than max_age_hours are skipped before the
fetch; a URL already known (is_known_url) is skipped before the fetch; the
stored URL is normalized; and a snippet-less, fetch-less entry still saves
its title instead of being dropped.
"""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from collectors.rss import RssCollector

FEED_URL = "https://blog.example.com/feed.xml"


def _pub_date(hours_ago: float = 1.0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return format_datetime(dt, usegmt=True)


def _rss_xml(items: str) -> str:
    return f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Example Blog</title>
{items}
</channel></rss>"""


_GOOD_ITEM = """
<item>
  <title>A real post</title>
  <link>{link}</link>
  <guid>{link}</guid>
  <pubDate>{pub_date}</pubDate>
  <description>{snippet}</description>
</item>
"""

_NO_LINK_ITEM = f"""
<item>
  <title>Missing link</title>
  <pubDate>{_pub_date()}</pubDate>
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


def _good_item(snippet: str, link: str = "https://blog.example.com/post-1", hours_ago: float = 1.0) -> str:
    return _GOOD_ITEM.format(link=link, snippet=snippet, pub_date=_pub_date(hours_ago))


@pytest.mark.asyncio
async def test_broken_entries_are_skipped_not_fatal(caplog) -> None:
    xml = _rss_xml(_good_item("x" * 600) + _NO_LINK_ITEM + _NO_DATE_ITEM)
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
    xml = _rss_xml(_good_item("x" * 600))
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
    xml = _rss_xml(_good_item("short teaser"))
    with respx.mock() as router:
        router.get(FEED_URL).mock(return_value=httpx.Response(200, text=xml))
        fetcher = AsyncMock()
        fetcher.fetch.return_value = "full article text " * 50
        fetcher.user_agent = "news-radar/1.0"
        collector = RssCollector(feeds=[FEED_URL], fetcher=fetcher)
        messages = [msg async for msg in collector._poll_feed(FEED_URL)]

    assert len(messages) == 1
    assert messages[0].text == "full article text " * 50
    fetcher.fetch.assert_called_once_with("https://blog.example.com/post-1", feed_key=FEED_URL)


@pytest.mark.asyncio
async def test_same_url_not_yielded_twice_across_polls() -> None:
    xml = _rss_xml(_good_item("x" * 600))
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


@pytest.mark.asyncio
async def test_entries_older_than_max_age_are_skipped_with_one_info_line(caplog) -> None:
    xml = _rss_xml(
        _good_item("x" * 600, link="https://blog.example.com/fresh", hours_ago=1)
        + _good_item("x" * 600, link="https://blog.example.com/stale", hours_ago=200)
    )
    with respx.mock() as router:
        router.get(FEED_URL).mock(return_value=httpx.Response(200, text=xml))
        with caplog.at_level("INFO"):
            collector = RssCollector(feeds=[FEED_URL], max_age_hours=72)
            messages = [msg async for msg in collector._poll_feed(FEED_URL)]

    assert [m.url for m in messages] == ["https://blog.example.com/fresh"]
    info_lines = [r.message for r in caplog.records if r.levelname == "INFO" and "skipped 1" in r.message.lower()]
    assert len(info_lines) == 1


@pytest.mark.asyncio
async def test_known_url_from_db_is_skipped_before_fetch() -> None:
    xml = _rss_xml(_good_item("short teaser"))
    with respx.mock() as router:
        router.get(FEED_URL).mock(return_value=httpx.Response(200, text=xml))
        fetcher = AsyncMock()
        fetcher.user_agent = "news-radar/1.0"
        collector = RssCollector(
            feeds=[FEED_URL], fetcher=fetcher, is_known_url=lambda url: url == "https://blog.example.com/post-1"
        )
        messages = [msg async for msg in collector._poll_feed(FEED_URL)]

    assert messages == []
    fetcher.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_url_is_normalized_before_storage() -> None:
    xml = _rss_xml(
        _good_item("x" * 600, link="https://blog.example.com/post-1/?utm_source=rss&utm_medium=feed#comments")
    )
    with respx.mock() as router:
        router.get(FEED_URL).mock(return_value=httpx.Response(200, text=xml))
        collector = RssCollector(feeds=[FEED_URL])
        messages = [msg async for msg in collector._poll_feed(FEED_URL)]

    assert len(messages) == 1
    assert messages[0].url == "https://blog.example.com/post-1"


@pytest.mark.asyncio
async def test_falls_back_to_title_when_no_snippet_and_fetch_fails() -> None:
    xml = _rss_xml(_good_item(""))
    with respx.mock() as router:
        router.get(FEED_URL).mock(return_value=httpx.Response(200, text=xml))
        fetcher = AsyncMock()
        fetcher.fetch.return_value = None
        fetcher.user_agent = "news-radar/1.0"
        collector = RssCollector(feeds=[FEED_URL], fetcher=fetcher)
        messages = [msg async for msg in collector._poll_feed(FEED_URL)]

    assert len(messages) == 1
    assert messages[0].text == "A real post"
