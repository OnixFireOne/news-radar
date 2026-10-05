"""Digest splitting for Telegram's per-message limit (pure, no bot image needed)."""
from __future__ import annotations

from bot.digest_schedule import split_message, telegram_len


def _item(i: int, size: int) -> str:
    return f"<b>Item {i}</b>\n<blockquote expandable>{'x' * size}</blockquote>"


def test_short_text_is_one_chunk() -> None:
    assert split_message("hello") == ["hello"]


def test_long_digest_splits_between_items_without_breaking_html() -> None:
    text = "\n\n".join(_item(i, 700) for i in range(8))  # ~6000 chars, like the live digest
    chunks = split_message(text)
    assert len(chunks) == 2
    assert all(len(chunk) <= 4096 for chunk in chunks)
    for chunk in chunks:
        assert chunk.count("<blockquote") == chunk.count("</blockquote>")
        assert chunk.count("<b>") == chunk.count("</b>")
    assert "\n\n".join(chunks) == text


def test_oversized_block_is_cut_to_the_limit() -> None:
    chunks = split_message("a" * 50 + "\n" + "b" * 250, limit=100)
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == "a" * 50 + "b" * 250


def _linked_item(i: int) -> str:
    url = f"https://github.com/OnixFireOne/news-radar/blob/main/knowledge/2026/09/item-{i}-{'s' * 60}.md"
    return f'<b>{i}. Title</b>\n{"x" * 300}\n<a href="{url}">разбор (md)</a> · <a href="{url}">source</a>'


def test_html_digest_counts_visible_text_not_link_urls() -> None:
    # Live 29.09: 5478 raw chars but 3813 visible, so Telegram would take it in one message.
    text = "\n\n".join(_linked_item(i) for i in range(8))
    assert len(text) > 4096
    assert split_message(text, html=True) == [text]
    assert len(split_message(text)) == 2  # raw measure stays the default


def test_html_visible_limit_still_splits_long_digest() -> None:
    text = "\n\n".join(_linked_item(i) for i in range(14))
    chunks = split_message(text, html=True)
    assert len(chunks) == 2
    assert all(telegram_len(chunk, html=True) <= 4096 for chunk in chunks)
    assert "\n\n".join(chunks) == text


def test_telegram_len_counts_utf16_and_entities() -> None:
    assert telegram_len("<b>a</b> &amp; 😀", html=True) == len("a & ") + 2
