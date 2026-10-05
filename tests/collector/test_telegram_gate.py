"""
TelegramCollector entry point — ТЗ #4 И3: crypto is paused by config, not by
deleting the collector. `sources.telegram.enabled` gates whether the service
collects at all; absent config must keep prod's current behavior, and
flipping the key back must be the entire rollback.
"""

from collectors.telegram import _is_telegram_enabled


def test_missing_sources_block_keeps_collecting() -> None:
    """A settings.json that predates this key must not silently go quiet."""
    assert _is_telegram_enabled({}) is True


def test_missing_telegram_key_keeps_collecting() -> None:
    assert _is_telegram_enabled({"rss": {"enabled": True}}) is True


def test_explicit_false_pauses_collection() -> None:
    assert _is_telegram_enabled({"telegram": {"enabled": False}}) is False


def test_explicit_true_collects() -> None:
    assert _is_telegram_enabled({"telegram": {"enabled": True}}) is True


def test_null_telegram_block_keeps_collecting() -> None:
    """`"telegram": null` is malformed config, not an intent to pause."""
    assert _is_telegram_enabled({"telegram": None}) is True
