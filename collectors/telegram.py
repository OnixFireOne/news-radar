"""
TelegramCollector — userbot на Telethon.
Слушает все каналы/группы аккаунта в реальном времени
и сохраняет сообщения в SQLite.

Запуск первый раз: потребует SMS-код от Telegram.
Сессия сохраняется в /app/sessions/ — не теряй эту папку!
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, PeerChannel

# Добавляем корень проекта в path
sys.path.insert(0, str(Path(__file__).parent.parent))

from collectors.base import BaseCollector, RawMessage
from database.schema import get_db, init_db

logger = logging.getLogger(__name__)


class TelegramCollector(BaseCollector):
    """
    Userbot который читает все каналы текущего аккаунта.
    Авторизуется через API ID + Hash (не бот-токен!).
    """

    source_type = "telegram"

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_name: str = "news_radar",
        session_dir: str = "/app/sessions",
        db_path: str = "/app/data/news.db",
        min_message_length: int = 30,
    ):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_path = str(Path(session_dir) / session_name)
        self.db_path = db_path
        self.min_length = min_message_length
        self.client: TelegramClient | None = None
        self._running = False

    async def start(self) -> None:
        """Подключиться к Telegram. Первый раз попросит SMS-код."""
        Path(self.session_path).parent.mkdir(parents=True, exist_ok=True)
        
        self.client = TelegramClient(
            self.session_path,
            self.api_id,
            self.api_hash,
        )
        
        logger.info("Connecting to Telegram...")
        await self.client.start()
        
        me = await self.client.get_me()
        logger.info(f"✅ Logged in as: {me.first_name} (@{me.username})")
        
        # Инициализируем БД и загружаем источники
        init_db(self.db_path)
        await self._sync_dialogs()
        self._running = True

    async def stop(self) -> None:
        """Отключиться."""
        self._running = False
        if self.client:
            await self.client.disconnect()
            logger.info("Disconnected from Telegram")

    async def _sync_dialogs(self) -> None:
        """
        Синхронизировать список каналов из аккаунта в таблицу sources.
        Добавляет новые, не трогает существующие.
        """
        logger.info("Syncing channels from account dialogs...")
        conn = get_db(self.db_path)
        count = 0
        
        try:
            async for dialog in self.client.iter_dialogs():
                # Берём только каналы и группы, не личку
                if not isinstance(dialog.entity, (Channel, Chat)):
                    continue

                entity = dialog.entity
                name = getattr(entity, "username", None) or str(entity.id)
                display_name = dialog.name

                # Upsert источника
                conn.execute("""
                    INSERT OR IGNORE INTO sources (type, name, display_name, active)
                    VALUES (?, ?, ?, 1)
                """, ("telegram", name, display_name))
                count += 1

            conn.commit()
            logger.info(f"✅ Synced {count} sources from account")
        except Exception as e:
            logger.error(f"Error syncing dialogs: {e}")
        finally:
            conn.close()

    async def listen(self) -> AsyncIterator[RawMessage]:
        """Слушать новые сообщения в реальном времени."""
        
        queue: asyncio.Queue[RawMessage] = asyncio.Queue()

        @self.client.on(events.NewMessage)
        async def handler(event):
            try:
                msg = await self._event_to_message(event)
                if msg and msg.is_valid(self.min_length):
                    await queue.put(msg)
                    self._save_message(msg)
            except Exception as e:
                logger.error(f"Error handling message: {e}")

        logger.info("👂 Listening for new messages...")

        while self._running:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield msg
            except asyncio.TimeoutError:
                continue

    async def fetch_history(
        self,
        source_name: str,
        limit: int = 100,
    ) -> list[RawMessage]:
        """Загрузить историю из конкретного канала."""
        messages = []
        logger.info(f"Fetching {limit} messages from {source_name}...")

        try:
            async for msg in self.client.iter_messages(source_name, limit=limit):
                if not msg.text:
                    continue
                raw = RawMessage(
                    external_id=str(msg.id),
                    source_name=source_name,
                    source_type="telegram",
                    text=msg.text or "",
                    views=getattr(msg, "views", 0) or 0,
                    forwards=getattr(msg, "forwards", 0) or 0,
                    timestamp=msg.date,
                )
                if raw.is_valid(self.min_length):
                    messages.append(raw)
                    self._save_message(raw)

        except Exception as e:
            logger.error(f"Error fetching history from {source_name}: {e}")

        logger.info(f"✅ Fetched {len(messages)} messages from {source_name}")
        return messages

    async def _event_to_message(self, event) -> RawMessage | None:
        """Конвертировать Telethon event в RawMessage."""
        if not event.text:
            return None

        # Получаем название источника
        try:
            chat = await event.get_chat()
            source_name = getattr(chat, "username", None) or str(chat.id)
        except Exception:
            source_name = str(event.chat_id)

        # Определяем тип медиа
        media_type = None
        if event.photo:
            media_type = "photo"
        elif event.video:
            media_type = "video"
        elif event.document:
            media_type = "document"

        return RawMessage(
            external_id=str(event.id),
            source_name=source_name,
            source_type="telegram",
            text=event.text,
            views=getattr(event.message, "views", 0) or 0,
            forwards=getattr(event.message, "forwards", 0) or 0,
            media_type=media_type,
            timestamp=event.date,
        )

    def _save_message(self, msg: RawMessage) -> None:
        """Сохранить сообщение в SQLite."""
        conn = get_db(self.db_path)
        try:
            # Найти source_id
            row = conn.execute(
                "SELECT id FROM sources WHERE name = ? AND type = ?",
                (msg.source_name, "telegram"),
            ).fetchone()

            if not row:
                # Создать источник если нет
                cursor = conn.execute(
                    "INSERT INTO sources (type, name, display_name) VALUES (?, ?, ?)",
                    ("telegram", msg.source_name, msg.source_name),
                )
                source_id = cursor.lastrowid
            else:
                source_id = row["id"]

            conn.execute("""
                INSERT OR IGNORE INTO messages
                    (source_id, external_id, text, media_type, views, forwards, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                source_id,
                msg.external_id,
                msg.text,
                msg.media_type,
                msg.views,
                msg.forwards,
                msg.timestamp or datetime.utcnow(),
            ))
            conn.commit()

        except Exception as e:
            logger.error(f"Error saving message {msg.external_id}: {e}")
        finally:
            conn.close()


async def main():
    """Точка входа для Docker."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    session_name = os.environ.get("TELEGRAM_SESSION_NAME", "news_radar")
    db_path = os.environ.get("DATABASE_PATH", "/app/data/news.db")
    min_length = int(os.environ.get("MIN_MESSAGE_LENGTH", "30"))

    collector = TelegramCollector(
        api_id=api_id,
        api_hash=api_hash,
        session_name=session_name,
        db_path=db_path,
        min_message_length=min_length,
    )

    await collector.start()

    # Сначала подгрузить историю последних 50 сообщений из каждого канала
    logger.info("Loading recent history from all channels...")
    conn = get_db(db_path)
    sources = conn.execute(
        "SELECT name FROM sources WHERE type='telegram' AND active=1"
    ).fetchall()
    conn.close()

    for source in sources:
        try:
            await collector.fetch_history(source["name"], limit=50)
            await asyncio.sleep(1)  # пауза чтобы не спамить Telegram
        except Exception as e:
            logger.warning(f"Could not load history from {source['name']}: {e}")

    logger.info("✅ History loaded. Now listening for new messages...")

    # Слушаем новые сообщения бесконечно
    try:
        async for message in collector.listen():
            logger.info(
                f"📨 [{message.source_name}] {message.text[:80]}..."
                if len(message.text) > 80
                else f"📨 [{message.source_name}] {message.text}"
            )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await collector.stop()


if __name__ == "__main__":
    asyncio.run(main())
