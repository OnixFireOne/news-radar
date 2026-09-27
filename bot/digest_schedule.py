"""Lightweight schedule parsing for the bot image (which has no analyzer package)."""
from datetime import time
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
