"""
BaseCollector — абстрактный базовый класс для всех источников.

Архитектурный паттерн: каждый источник (Telegram, Discord, Twitter...)
реализует этот интерфейс. Analyzer не знает откуда пришли данные.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator


@dataclass
class RawMessage:
    """Нормализованное сообщение из любого источника."""
    external_id: str          # ID в источнике (message_id в Telegram)
    source_name: str          # @channel_username
    source_type: str          # 'telegram', 'discord', 'twitter' ...
    text: str                 # текст сообщения
    views: int = 0
    forwards: int = 0
    media_type: str | None = None   # photo, video, document
    timestamp: datetime | None = None

    def is_valid(self, min_length: int = 30) -> bool:
        """Проверить что сообщение стоит анализировать."""
        return (
            len(self.text.strip()) >= min_length
            and not self.text.startswith("/")   # команды бота
        )


class BaseCollector(ABC):
    """
    Абстрактный коллектор. Реализуй этот класс для каждого источника.

    Пример использования:
        collector = TelegramCollector(api_id, api_hash)
        await collector.start()
        async for msg in collector.listen():
            save_to_db(msg)
    """

    source_type: str = "unknown"

    @abstractmethod
    async def start(self) -> None:
        """Инициализация и подключение к источнику."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Корректное отключение."""
        ...

    @abstractmethod
    async def listen(self) -> AsyncIterator[RawMessage]:
        """
        Асинхронный генератор новых сообщений в реальном времени.
        Работает бесконечно пока не вызван stop().
        """
        ...

    @abstractmethod
    async def fetch_history(
        self,
        source_name: str,
        limit: int = 100,
    ) -> list[RawMessage]:
        """Загрузить историю сообщений из источника."""
        ...

    def normalize(self, raw: dict) -> RawMessage:
        """
        Переопределить при необходимости кастомной нормализации.
        По умолчанию просто возвращает RawMessage из dict.
        """
        return RawMessage(**raw)
