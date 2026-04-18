"""
BaseCollector — abstract base class for all data source collectors.

Architecture pattern: each source (Telegram, Discord, Twitter...)
implements this interface. The Analyzer doesn't know where data came from.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator


@dataclass
class RawMessage:
    """
    Normalized message from any data source.
    All optional fields default to None/0 so existing collectors
    that don't fill them still work without changes.
    """
    external_id:          str                 # Message ID in the source
    source_name:          str                 # @channel_username or server name
    source_type:          str                 # 'telegram', 'discord', 'twitter'
    text:                 str                 # Message text content

    # Core metrics
    views:                int = 0
    forwards:             int = 0
    media_type:           str | None = None   # photo, video, document, or None
    timestamp:            datetime | None = None

    # Engagement metrics (Telegram-specific, others can leave as default)
    reactions_count:      int = 0             # total reactions across all emojis
    reactions_json:       str | None = None   # JSON: {"\ud83d\udc4d": 12, "\ud83d\udd25": 5, ...}
    replies_count:        int = 0             # number of comments/replies

    # Authorship
    post_author:          str | None = None   # editor signature in multi-author channels

    # Forward tracking (content origin for deduplication)
    forward_from_channel: str | None = None   # source channel @name or numeric id
    forward_from_msg_id:  int | None = None   # original message id in source

    # Post lifecycle
    edit_date:            datetime | None = None  # last edit time (None = never edited)

    def is_valid(self, min_length: int = 30) -> bool:
        """Check if the message is worth analyzing."""
        return (
            len(self.text.strip()) >= min_length
            and not self.text.startswith("/")   # skip bot commands
        )


class BaseCollector(ABC):
    """
    Abstract collector. Implement this class for each data source.

    Usage example:
        collector = TelegramCollector(api_id, api_hash)
        await collector.start()
        async for msg in collector.listen():
            save_to_db(msg)
    """

    source_type: str = "unknown"

    @abstractmethod
    async def start(self) -> None:
        """Initialize and connect to the data source."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully disconnect from the source."""
        ...

    @abstractmethod
    async def listen(self) -> AsyncIterator[RawMessage]:
        """
        Async generator that yields new messages in real time.
        Runs indefinitely until stop() is called.
        """
        ...

    @abstractmethod
    async def fetch_history(
        self,
        source_name: str,
        limit: int = 100,
    ) -> list[RawMessage]:
        """Load historical messages from the source."""
        ...

    def normalize(self, raw: dict) -> RawMessage:
        """
        Override for custom normalization logic.
        By default, constructs RawMessage from a dict.
        """
        return RawMessage(**raw)
