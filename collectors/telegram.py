"""
TelegramCollector — Telethon-based userbot.

Listens to all channels/groups of the authenticated account in real time
and saves messages to SQLite.

First run: will prompt for SMS code from Telegram.
Session is saved to /app/sessions/ — don't lose this folder!
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, MessageReactions

sys.path.insert(0, str(Path(__file__).parent.parent))

from collectors.base import BaseCollector, RawMessage
from database.schema import get_db, init_db
from config.config_watcher import ConfigWatcher

logger = logging.getLogger(__name__)


def _parse_reactions(reactions) -> tuple[int, str | None]:
    """
    Parse Telethon MessageReactions into (total_count, json_string).

    Returns:
        (0, None) if no reactions.
        (12, '{"👍": 10, "🔥": 2}') if reactions present.

    Safe to call with None — returns (0, None).
    """
    if not reactions or not getattr(reactions, "results", None):
        return 0, None

    counts: dict[str, int] = {}
    total = 0

    for result in reactions.results:
        try:
            # ReactionEmoji has .emoticon, ReactionCustomEmoji has .document_id
            emoji = getattr(result.reaction, "emoticon", None) or "?"
            count = getattr(result, "count", 0) or 0
            counts[emoji] = count
            total += count
        except Exception:
            continue

    return total, (json.dumps(counts, ensure_ascii=False) if counts else None)


class TelegramCollector(BaseCollector):
    """
    Userbot that reads all channels of the current account.
    Authenticates via API ID + Hash (not a bot token!).
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
        self._folder_peers: set[int] = set()

    async def start(self) -> None:
        """Connect to Telegram. First run will ask for phone number and SMS code."""
        Path(self.session_path).parent.mkdir(parents=True, exist_ok=True)

        self.client = TelegramClient(
            self.session_path,
            self.api_id,
            self.api_hash,
        )

        logger.info("Connecting to Telegram...")
        await self.client.start()

        me = await self.client.get_me()
        logger.info(f"Logged in as: {me.first_name} (@{me.username})")

        # Initialize DB schema + migrations (creates tables if needed)
        init_db(self.db_path)
        self._running = True

    async def stop(self) -> None:
        """Disconnect from Telegram."""
        self._running = False
        if self.client:
            await self.client.disconnect()
            logger.info("Disconnected from Telegram")

    async def _get_folder_peers(self, folder_name: str) -> set[int]:
        """
        Get peer IDs of all chats in a specific Telegram folder.
        Returns empty set if folder not found (will fall back to all dialogs).
        """
        try:
            from telethon.tl.functions.messages import GetDialogFiltersRequest
            result = await self.client(GetDialogFiltersRequest())

            for folder in result.filters:
                # Skip default "All Chats" folder (no title attribute)
                title = getattr(folder, "title", None)
                if not title:
                    continue

                # Support both string title and InputPeerEmpty title objects
                folder_title = title if isinstance(title, str) else getattr(title, "text", str(title))

                if folder_title.lower() == folder_name.lower():
                    peer_ids = set()
                    for peer in getattr(folder, "include_peers", []):
                        # Extract numeric ID from different peer types
                        peer_id = getattr(peer, "channel_id", None) \
                               or getattr(peer, "chat_id", None) \
                               or getattr(peer, "user_id", None)
                        if peer_id:
                            peer_ids.add(peer_id)
                    logger.info(f"Found folder '{folder_title}' with {len(peer_ids)} peers")
                    return peer_ids

            logger.warning(f"Folder '{folder_name}' not found — will use all dialogs")
            return set()

        except Exception as e:
            logger.error(f"Error reading folders: {e}")
            return set()

    async def _sync_and_catchup(
        self,
        new_source_limit: int = 100,
        force_reload: bool = False,
        folder_name: str = "",
    ) -> None:
        """
        Single-pass startup routine: iterates dialogs ONCE and:
          1. Upserts channel metadata into sources.
          2. Checks unread_count for each channel:
             - unread_count > 0  → fetch missed messages (min_id = read_inbox_max_id)
             - First time ever    → fetch initial history (limit = new_source_limit)
             - unread_count == 0  → skip (no API call needed)

        Args:
            folder_name: Telegram folder name from settings.json (e.g. "Ton/DeFi").
                         If set, only dialogs in that folder are processed.
                         If empty, all channels/groups are processed.
        """
        folder_peers: set[int] = set()

        if folder_name:
            logger.info(f"Filtering by folder: '{folder_name}'")
            folder_peers = await self._get_folder_peers(folder_name)
            self._folder_peers = folder_peers  # Save for real-time listener
            if not folder_peers:
                logger.warning(f"Folder '{folder_name}' not found or empty — processing all")
        else:
            self._folder_peers = set()
            logger.info("No telegram_folder configured — processing all channels")

        conn = get_db(self.db_path)
        meta_count = 0
        new_count = 0       # brand-new channels (never seen before)
        catchup_count = 0   # channels with unread msgs we fetched
        skip_count = 0      # channels already up-to-date (unread=0)
        msgs_saved = 0      # total messages actually saved across all channels

        try:
            async for dialog in self.client.iter_dialogs():
                # Only channels and groups — skip DMs
                if not isinstance(dialog.entity, (Channel, Chat)):
                    continue

                entity = dialog.entity

                # Folder filter
                if folder_peers:
                    entity_id = getattr(entity, "id", None)
                    if entity_id not in folder_peers:
                        continue

                name = getattr(entity, "username", None) or str(entity.id)
                display_name = dialog.name

                # ── 1. Upsert channel metadata ──
                subscribers     = getattr(entity, "participants_count", 0) or 0
                description     = getattr(entity, "about", None) or ""
                verified        = int(getattr(entity, "verified", False))
                scam            = int(getattr(entity, "scam", False))
                linked_chat_id  = getattr(entity, "linked_chat_id", None)
                channel_created = getattr(
                    getattr(entity, "date", None), "isoformat", lambda: None
                )()

                conn.execute("""
                    INSERT OR IGNORE INTO sources
                        (type, name, display_name, active,
                         subscribers, description, verified, scam,
                         linked_chat_id, channel_created, meta_updated)
                    VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    "telegram", name, display_name,
                    subscribers, description, verified, scam,
                    linked_chat_id, channel_created,
                ))
                conn.execute("""
                    UPDATE sources SET
                        display_name = ?, subscribers = ?, description = ?,
                        verified = ?, scam = ?, linked_chat_id = ?,
                        channel_created = ?, meta_updated = CURRENT_TIMESTAMP
                    WHERE name = ? AND type = 'telegram'
                """, (
                    display_name, subscribers, description, verified, scam,
                    linked_chat_id, channel_created, name,
                ))
                meta_count += 1
                # ── Commit metadata BEFORE fetch_history ──
                # _save_message() opens its own write connection.
                # If we hold uncommitted metadata writes here, SQLite WAL
                # refuses the second writer → "database is locked".
                # Committing here releases the write lock before fetch_history.
                conn.commit()

                if force_reload:
                    # Explicit full reload — fetch last N regardless of unread state
                    await self.fetch_history(name, limit=new_source_limit)
                    new_count += 1
                    continue

                # ── 2. Smart catch-up based on unread count ──
                unread = getattr(dialog, "unread_count", 0) or 0
                last_read_id = getattr(
                    getattr(dialog, "dialog", None), "read_inbox_max_id", 0
                ) or 0
                # Last message ID in this dialog (used to mark-as-read even for skipped posts)
                dialog_last_id = getattr(
                    getattr(dialog, "message", None), "id", 0
                ) or 0

                # Check if we've ever collected from this source
                is_new = self._get_last_message_id(name) == 0

                if is_new:
                    # Brand-new channel — fetch initial history
                    saved = await self.fetch_history(name, limit=new_source_limit)
                    msgs_saved += len(saved)
                    new_count += 1
                elif unread > 0 and last_read_id > 0:
                    # Missed messages while collector was down
                    logger.info(
                        f"[{name}] {unread} unread — catching up from id={last_read_id}"
                    )
                    msgs = await self.fetch_history(name, min_id=last_read_id)
                    msgs_saved += len(msgs)
                    if msgs:
                        catchup_count += 1
                    else:
                        skip_count += 1  # unread but media-only / too short

                    # Always mark the channel as read up to the last known message,
                    # even if we saved nothing (media-only / too short).
                    if dialog_last_id and dialog_last_id > last_read_id:
                        entity_id = int(name) if name.lstrip("-").isdigit() else name
                        try:
                            await self.client.send_read_acknowledge(
                                entity_id, max_id=dialog_last_id
                            )
                        except Exception as e:
                            logger.debug(f"[{name}] Could not mark as read: {e}")
                else:
                    # Already up to date — skip without any API call
                    skip_count += 1

                await asyncio.sleep(0.2)  # brief pause between channels

        # conn has no pending writes (committed per-channel above)

        except Exception as e:
            logger.error(f"Error in sync_and_catchup: {e}")
        finally:
            conn.close()

        logger.info(
            f"Startup sync done: {meta_count} channels, "
            f"{new_count} new channels, {catchup_count} caught up, "
            f"{msgs_saved} messages saved, {skip_count} up-to-date"
        )

    async def _sync_dialogs(self, folder_name: str = "") -> None:
        """
        Alias for hot-reload compatibility (called from on_folder_change).
        Metadata-only sync — no catch-up needed on config change.
        """
        await self._sync_and_catchup(
            new_source_limit=0,
            force_reload=False,
            folder_name=folder_name,
        )


    async def listen(self) -> AsyncIterator[RawMessage]:
        """Listen for new messages in real time from all account channels."""

        queue: asyncio.Queue[RawMessage] = asyncio.Queue()

        @self.client.on(events.NewMessage)
        async def handler(event):
            try:
                # Apply folder filter in real-time
                if self._folder_peers:
                    chat = event.chat if event.chat else await event.get_chat()
                    if getattr(chat, "id", None) not in self._folder_peers:
                        return

                msg = await self._event_to_message(event)
                if msg is None:
                    return  # no text — silently skip
                if not msg.is_valid(self.min_length):
                    logger.debug(f"Skipped short/command message ({len(msg.text)} chars) from {msg.source_name}")
                    return
                await queue.put(msg)
                self._save_message(msg)

                # Mark message as read on the Telegram account.
                # This makes the userbot behave like a real reader:
                # unread counters drop to 0, blue ticks appear where applicable.
                # Non-fatal: if it fails (e.g. permissions), we just log and continue.
                try:
                    await self.client.send_read_acknowledge(event.chat_id, max_id=event.id)
                except Exception as read_err:
                    logger.debug(f"Could not mark read in {msg.source_name}: {read_err}")

            except Exception as e:
                logger.error(f"Error handling message: {e}")

        logger.info("Listening for new messages...")

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
        min_id: int = 0,
    ) -> list[RawMessage]:
        """
        Load message history from a specific channel.

        Args:
            source_name: @username or numeric channel id.
            limit:   Max messages to fetch. Ignored when min_id > 0
                     (in that case we fetch ALL missed messages).
            min_id:  If > 0, fetch only messages with ID > min_id.
                     This is the "catch-up" mode: only what we missed.
                     min_id = 0 means "fetch the latest N messages".
        """
        messages = []
        skipped = 0

        # Numeric-only names must be passed as int, not str
        entity = int(source_name) if source_name.lstrip("-").isdigit() else source_name

        if min_id > 0:
            logger.info(f"[{source_name}] Catching up from message id={min_id}...")
        else:
            logger.info(f"[{source_name}] New source — fetching last {limit} messages")

        try:
            last_msg_id = 0

            iter_kwargs = {"min_id": min_id} if min_id > 0 else {"limit": limit}
            async for msg in self.client.iter_messages(entity, **iter_kwargs):
                if not msg.text:
                    skipped += 1
                    continue

                # Parse engagement metadata
                reactions_count, reactions_json = _parse_reactions(getattr(msg, "reactions", None))
                replies_count = getattr(getattr(msg, "replies", None), "replies", 0) or 0

                forward_from_channel = None
                forward_from_msg_id = None
                if getattr(msg, "fwd_from", None):
                    fwd_id = getattr(getattr(msg.fwd_from, "from_id", None), "channel_id", None)
                    if fwd_id:
                        forward_from_channel = str(fwd_id)
                    forward_from_msg_id = getattr(msg.fwd_from, "channel_post", None)

                raw = RawMessage(
                    external_id=str(msg.id),
                    source_name=source_name,
                    source_type="telegram",
                    text=msg.text or "",
                    views=getattr(msg, "views", 0) or 0,
                    forwards=getattr(msg, "forwards", 0) or 0,
                    reactions_count=reactions_count,
                    reactions_json=reactions_json,
                    replies_count=replies_count,
                    post_author=getattr(msg, "post_author", None),
                    forward_from_channel=forward_from_channel,
                    forward_from_msg_id=forward_from_msg_id,
                    edit_date=getattr(msg, "edit_date", None),
                    timestamp=msg.date,
                )
                if raw.is_valid(self.min_length):
                    messages.append(raw)
                    self._save_message(raw)
                    last_msg_id = max(last_msg_id, msg.id)
                else:
                    skipped += 1

            # Mark channel as read up to the highest fetched message id
            if last_msg_id:
                try:
                    await self.client.send_read_acknowledge(entity, max_id=last_msg_id)
                except Exception as read_err:
                    logger.debug(f"Could not mark {source_name} as read: {read_err}")

        except Exception as e:
            logger.error(f"Error fetching history from {source_name}: {e}")

        action = "caught up" if min_id > 0 else "fetched"
        logger.info(
            f"[{source_name}] {action}: {len(messages)} new, {skipped} skipped (no text / too short)"
        )
        return messages

    def _get_last_message_id(self, source_name: str) -> int:
        """
        Return the highest Telegram message ID we have stored for a source.
        Returns 0 if no messages exist yet (new source).
        Used as watermark for catch-up sync on restart.
        """
        conn = get_db(self.db_path)
        try:
            row = conn.execute("""
                SELECT MAX(CAST(external_id AS INTEGER)) as last_id
                FROM messages m
                JOIN sources s ON s.id = m.source_id
                WHERE s.name = ? AND s.type = 'telegram'
            """, (source_name,)).fetchone()
            return int(row["last_id"]) if row and row["last_id"] else 0
        finally:
            conn.close()


    async def _event_to_message(self, event) -> RawMessage | None:
        """Convert a Telethon event into a RawMessage with full metadata."""
        if not event.text:
            return None

        # Resolve source name
        try:
            chat = await event.get_chat()
            source_name = getattr(chat, "username", None) or str(chat.id)
        except Exception:
            source_name = str(event.chat_id)

        # Detect media type
        media_type = None
        if event.photo:
            media_type = "photo"
        elif event.video:
            media_type = "video"
        elif event.document:
            media_type = "document"

        msg = event.message

        # Reactions: parse emoji counts into a dict
        reactions_count, reactions_json = _parse_reactions(getattr(msg, "reactions", None))

        # Reply count (only available on channel posts with comments enabled)
        replies_count = 0
        if getattr(msg, "replies", None):
            replies_count = getattr(msg.replies, "replies", 0) or 0

        # Forward origin
        forward_from_channel = None
        forward_from_msg_id = None
        if getattr(msg, "fwd_from", None):
            fwd = msg.fwd_from
            if getattr(fwd, "from_id", None):
                fwd_id = getattr(fwd.from_id, "channel_id", None)
                if fwd_id:
                    forward_from_channel = str(fwd_id)
            forward_from_msg_id = getattr(fwd, "channel_post", None)

        return RawMessage(
            external_id=str(event.id),
            source_name=source_name,
            source_type="telegram",
            text=event.text,
            views=getattr(msg, "views", 0) or 0,
            forwards=getattr(msg, "forwards", 0) or 0,
            reactions_count=reactions_count,
            reactions_json=reactions_json,
            replies_count=replies_count,
            post_author=getattr(msg, "post_author", None),
            forward_from_channel=forward_from_channel,
            forward_from_msg_id=forward_from_msg_id,
            edit_date=getattr(msg, "edit_date", None),
            media_type=media_type,
            timestamp=event.date,
        )

    def _save_message(self, msg: RawMessage) -> None:
        """Persist a message to SQLite with all available metadata."""
        conn = get_db(self.db_path)
        try:
            # Look up source_id by name
            row = conn.execute(
                "SELECT id FROM sources WHERE name = ? AND type = ?",
                (msg.source_name, "telegram"),
            ).fetchone()

            if not row:
                cursor = conn.execute(
                    "INSERT INTO sources (type, name, display_name) VALUES (?, ?, ?)",
                    ("telegram", msg.source_name, msg.source_name),
                )
                source_id = cursor.lastrowid
            else:
                source_id = row["id"]

            conn.execute("""
                INSERT OR IGNORE INTO messages
                    (source_id, external_id, text, media_type,
                     views, forwards,
                     reactions_count, reactions_json, replies_count,
                     post_author,
                     forward_from_channel, forward_from_msg_id,
                     edit_date, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                source_id,
                msg.external_id,
                msg.text,
                msg.media_type,
                msg.views,
                msg.forwards,
                getattr(msg, "reactions_count", 0),
                getattr(msg, "reactions_json", None),
                getattr(msg, "replies_count", 0),
                getattr(msg, "post_author", None),
                getattr(msg, "forward_from_channel", None),
                getattr(msg, "forward_from_msg_id", None),
                getattr(msg, "edit_date", None),
                msg.timestamp or datetime.utcnow(),
            ))
            conn.commit()

        except Exception as e:
            logger.error(f"Error saving message {msg.external_id}: {e}")
        finally:
            conn.close()


async def main():
    """Docker entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    session_name = os.environ.get("TELEGRAM_SESSION_NAME", "news_radar")
    db_path = os.environ.get("DATABASE_PATH", "/app/data/news.db")

    # How many messages to fetch from a truly NEW channel (never seen before)
    new_source_limit = int(os.environ.get("NEW_SOURCE_LIMIT", "100"))

    # Load config (file + env vars)
    cfg = ConfigWatcher("/app/config/settings.json")

    collector = TelegramCollector(
        api_id=api_id,
        api_hash=api_hash,
        session_name=session_name,
        db_path=db_path,
        min_message_length=cfg.get("min_message_length", 30),
    )

    # Hot-reload: update min_length when config changes
    def on_min_length_change(new_val: int):
        collector.min_length = new_val
        logger.info(f"Hot-reloaded: min_message_length = {new_val}")

    # Hot-reload: re-sync sources when folder changes
    async def on_folder_change(new_folder: str):
        logger.info(f"Hot-reloaded: telegram_folder = '{new_folder}', re-syncing sources...")
        await collector._sync_dialogs(folder_name=new_folder)

    cfg.on_change("min_message_length", on_min_length_change)
    cfg.on_change("telegram_folder", on_folder_change)

    await collector.start()

    force_reload = os.environ.get("LOAD_HISTORY", "").lower() == "true"
    # Read folder from config (settings.json), not from env
    folder_name = cfg.get("telegram_folder", "").strip()

    async def listen_loop():
        # Start the real-time listener BEFORE catchup — eliminates the race
        # condition where messages arriving during ~30s startup could be missed.
        # INSERT OR IGNORE in _save_message handles duplicates safely.
        async for message in collector.listen():
            preview = message.text[:80] + "..." if len(message.text) > 80 else message.text
            logger.info(f"[{message.source_name}] {preview}")

    async def catchup_then_done():
        # Startup catchup runs concurrently with listen_loop
        await collector._sync_and_catchup(
            new_source_limit=new_source_limit,
            force_reload=force_reload,
            folder_name=folder_name,
        )
        logger.info("Listening for new messages in real time...")

    try:
        # All three run concurrently from the moment the connection opens:
        #  - listen_loop:       real-time handler (registered immediately)
        #  - catchup_then_done: startup sync for missed messages
        #  - cfg.watch:         hot-reload config watcher
        await asyncio.gather(
            listen_loop(),
            catchup_then_done(),
            cfg.watch(),
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await collector.stop()


if __name__ == "__main__":
    asyncio.run(main())
