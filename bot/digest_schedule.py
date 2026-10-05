"""Lightweight schedule parsing for the bot image (which has no analyzer package)."""
from collections.abc import Callable
from datetime import time
from html import unescape
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo


def schedule_slots(cfg: Mapping[str, Any]) -> list[tuple[str | None, time]]:
    digests = cfg.get("digests", [])
    if not digests:
        return [(None, time(hour=hour, tzinfo=ZoneInfo("Europe/Moscow"))) for hour in (12, 20)]
    slots: list[tuple[str | None, time]] = []
    names: set[str] = set()
    for digest in digests:
        name = str(digest["name"])
        if name in names:
            raise ValueError(f"Duplicate digest name: {name}")
        names.add(name)
        zone = ZoneInfo(str(digest.get("tz", "Europe/Moscow")))
        for value in digest.get("at", []):
            slot = time.fromisoformat(value)
            if slot.tzinfo is not None:
                raise ValueError(f"Digest {name}: at must be local times without offsets")
            if digest.get("enabled", True):
                slots.append((name, slot.replace(tzinfo=zone)))
    return list(dict.fromkeys(slots))


def slot_key(name: str | None, slot: time) -> tuple[str | None, str, str]:
    # time equality alone does not capture ZoneInfo changes for DST zones.
    return name, slot.strftime("%H:%M:%S"), str(slot.tzinfo)


TELEGRAM_MESSAGE_LIMIT = 4096


_TAG = re.compile(r"<[^>]*>")


def telegram_len(text: str, html: bool = False) -> int:
    """Length as Telegram counts it: UTF-16 code units of the visible text (tags parsed away)."""
    visible = unescape(_TAG.sub("", text)) if html else text
    return len(visible.encode("utf-16-le")) // 2


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT, html: bool = False) -> list[str]:
    """Split a digest into Telegram-sized chunks on blank lines between items.

    Items are separated by blank lines and keep their HTML tags balanced, so
    splitting there never breaks markup. With ``html`` the limit applies to the
    visible text, as in Telegram: link URLs and tags do not count. Otherwise the
    raw length is measured. A single block longer than the limit falls back to
    line splits, then to hard cuts of the raw text.
    """
    size: Callable[[str], int] = (lambda chunk: telegram_len(chunk, html=True)) if html else len
    if size(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if size(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if size(block) <= limit:
            current = block
            continue
        for line in block.split("\n"):
            candidate = f"{current}\n{line}" if current else line
            if size(candidate) <= limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line
    if current:
        chunks.append(current)
    return chunks
