"""Digest splitting for Telegram's per-message limit (pure, no bot image needed)."""
from __future__ import annotations

from bot.digest_schedule import split_message


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
