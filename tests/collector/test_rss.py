"""
RssCollector — ТЗ #4 И2/И2.1/И3.1 acceptance: a broken entry (no link/date)
is skipped with a warning rather than aborting the whole poll cycle; a
long-enough snippet skips the full-text fetch; URL dedup across polls of
the same feed; entries older than max_age_hours are skipped before the
fetch AND before is_known_url (a DB lookup an archive-heavy feed would
otherwise pay thousands of times per cycle); a URL already known
(is_known_url) is skipped before the fetch; the stored URL is normalized;
a snippet-less, fetch-less entry still saves its title instead of being
dropped; and a feed snippet's raw HTML (Habr/dev.to/Simon Willison style
<p>/<img>/<a>) is cleaned to plain text before it's measured against the
full-fetch threshold or stored in messages.text.
"""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from collectors.rss import RssCollector, _clean_snippet_html

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


# Real feeds (Habr, dev.to, Simon Willison) put raw HTML in <description>,
# wrapped in CDATA so the surrounding XML stays well-formed despite the
# embedded '<'/'&' — <description>{snippet}</description> above only works
# for the plain-text snippets the other tests use.
_GOOD_ITEM_CDATA = """
<item>
  <title>A real post</title>
  <link>{link}</link>
  <guid>{link}</guid>
  <pubDate>{pub_date}</pubDate>
  <description><![CDATA[{snippet}]]></description>
</item>
"""


def _good_item_html(snippet_html: str, link: str = "https://blog.example.com/post-1", hours_ago: float = 1.0) -> str:
    return _GOOD_ITEM_CDATA.format(link=link, snippet=snippet_html, pub_date=_pub_date(hours_ago))


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


# ── ТЗ #4 И3.1: HTML snippet cleaning ────────────────────────────────────────


def test_clean_snippet_html_strips_tags_decodes_entities_collapses_whitespace(mocker) -> None:
    # trafilatura.extract() targets whole documents and routinely returns
    # None on a short fragment like this (see _clean_snippet_html's own
    # docstring) — mocked explicitly so this test exercises the
    # deterministic fallback rather than depending on the installed
    # trafilatura version's exact heuristics on a tiny snippet.
    mocker.patch("collectors.rss.trafilatura.extract", return_value=None)
    habr_snippet = (
        "<p>Каждый раз, когда я вижу очередной &quot;революционный&quot; "
        "подход, я вспоминаю про &laquo;serverless&raquo;.</p>\n"
        '<img src="https://habrastorage.org/getpro/habr/upload_files/1.png" alt="" />\n'
        '<p>Читать далее &mdash; <a href="https://habr.com/ru/post/123/#habracut">на Хабре</a></p>'
    )

    cleaned = _clean_snippet_html(habr_snippet)

    assert "<" not in cleaned and ">" not in cleaned
    assert '"революционный"' in cleaned
    assert "«serverless»" in cleaned
    assert "—" in cleaned
    assert "на Хабре" in cleaned
    assert "  " not in cleaned  # runs of whitespace collapsed to one space
    assert cleaned == cleaned.strip()


def test_clean_snippet_html_empty_or_broken_input_does_not_raise(mocker) -> None:
    mocker.patch("collectors.rss.trafilatura.extract", side_effect=RuntimeError("boom"))
    assert _clean_snippet_html("") == ""
    assert _clean_snippet_html("   ") == ""
    assert _clean_snippet_html("<p>unclosed") == "unclosed"


@pytest.mark.asyncio
async def test_snippet_length_is_measured_after_html_is_stripped_not_before(mocker) -> None:
    """ТЗ #4 И3.1: markup padding must not hide a short teaser from the
    full-fetch threshold — Habr-style snippets are mostly <p>/<img> markup
    around a short teaser and were previously measured (wrongly) as HTML."""
    mocker.patch("collectors.rss.trafilatura.extract", return_value=None)
    padded_html = "<p>Короткий текст.</p>" + "".join(
        f'<img src="https://cdn.example.com/filler-image-{i}.png" alt="not real article text" />'
        for i in range(8)
    )
    assert len(padded_html) > 500  # sanity: raw HTML alone crosses the threshold
    xml = _rss_xml(_good_item_html(padded_html))
    with respx.mock() as router:
        router.get(FEED_URL).mock(return_value=httpx.Response(200, text=xml))
        fetcher = AsyncMock()
        fetcher.fetch.return_value = "full article text " * 50
        fetcher.user_agent = "news-radar/1.0"
        collector = RssCollector(feeds=[FEED_URL], fetcher=fetcher)
        messages = [msg async for msg in collector._poll_feed(FEED_URL)]

    assert len(messages) == 1
    fetcher.fetch.assert_called_once_with("https://blog.example.com/post-1", feed_key=FEED_URL)
    assert messages[0].text == "full article text " * 50


@pytest.mark.asyncio
async def test_long_cleaned_text_skips_full_text_fetch_even_with_markup(mocker) -> None:
    """The inverse of the above: once markup is stripped, a genuinely long
    snippet still skips the fetch, and messages.text carries no leftover
    tags."""
    mocker.patch("collectors.rss.trafilatura.extract", return_value=None)
    snippet_html = "<p>" + "x" * 600 + "</p>" + '<img src="https://cdn.example.com/pic.png" alt="filler" />'
    xml = _rss_xml(_good_item_html(snippet_html))
    with respx.mock() as router:
        router.get(FEED_URL).mock(return_value=httpx.Response(200, text=xml))
        fetcher = AsyncMock()
        fetcher.user_agent = "news-radar/1.0"
        collector = RssCollector(feeds=[FEED_URL], fetcher=fetcher)
        messages = [msg async for msg in collector._poll_feed(FEED_URL)]

    assert len(messages) == 1
    assert messages[0].text == "x" * 600
    assert "<" not in messages[0].text
    fetcher.fetch.assert_not_called()


# ── ТЗ #4 И3.1: age check before is_known_url ────────────────────────────────


@pytest.mark.asyncio
async def test_stale_entry_is_dropped_without_calling_is_known_url() -> None:
    """is_known_url is a SQLite lookup; an archive-heavy feed can offer
    thousands of stale entries per cycle, so the (free) age check must run
    first and skip the DB lookup entirely for anything older than
    max_age_hours."""
    xml = _rss_xml(_good_item("x" * 600, link="https://blog.example.com/stale", hours_ago=200))
    calls: list[str] = []

    def spy_is_known_url(url: str) -> bool:
        calls.append(url)
        return False

    with respx.mock() as router:
        router.get(FEED_URL).mock(return_value=httpx.Response(200, text=xml))
        collector = RssCollector(feeds=[FEED_URL], max_age_hours=72, is_known_url=spy_is_known_url)
        messages = [msg async for msg in collector._poll_feed(FEED_URL)]

    assert messages == []
    assert calls == []


@pytest.mark.asyncio
async def test_fresh_entry_still_checked_against_is_known_url() -> None:
    """The reorder must not skip is_known_url altogether for entries that
    pass the age check."""
    xml = _rss_xml(_good_item("x" * 600, link="https://blog.example.com/fresh", hours_ago=1))
    calls: list[str] = []

    def spy_is_known_url(url: str) -> bool:
        calls.append(url)
        return True

    with respx.mock() as router:
        router.get(FEED_URL).mock(return_value=httpx.Response(200, text=xml))
        collector = RssCollector(feeds=[FEED_URL], max_age_hours=72, is_known_url=spy_is_known_url)
        messages = [msg async for msg in collector._poll_feed(FEED_URL)]

    assert messages == []
    assert calls == ["https://blog.example.com/fresh"]
