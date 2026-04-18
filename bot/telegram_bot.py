"""
Telegram Bot — digest delivery to the user.

This is a regular bot (via @BotFather), NOT a userbot.
Commands:
  /start   — welcome message
  /status  — system statistics
  /hot     — trending topics right now
  /digest  — most recent AI digest
  /help    — help

Auto-sends a digest every DIGEST_INTERVAL_HOURS hours.
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

logger = logging.getLogger(__name__)

# Set of allowed Telegram user IDs (get yours from @userinfobot)
ALLOWED_USERS: set[int] = set()


def is_allowed(user_id: int) -> bool:
    """Check if the user is authorized to use this bot."""
    if not ALLOWED_USERS:
        return True  # if empty — allow everyone (dev mode)
    return user_id in ALLOWED_USERS


API_URL = os.environ.get("API_URL", "http://localhost:8000")


async def fetch_api(path: str) -> dict | list | None:
    """Fetch data from the News Radar API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{API_URL}{path}")
            if resp.status_code == 200:
                return resp.json()
            return None
    except Exception as e:
        logger.error(f"API request failed ({path}): {e}")
        return None


# ──────────────────────────────────────────────
# COMMAND HANDLERS
# ──────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text(
        "📡 News Radar is running!\n\n"
        "I monitor Telegram channels and analyze news with AI.\n\n"
        "Commands:\n"
        "/hot — trending topics right now\n"
        "/digest — latest AI digest\n"
        "/status — system statistics\n"
        "/help — this message"
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    stats = await fetch_api("/stats")
    if not stats:
        await update.message.reply_text("❌ API is unavailable")
        return

    await update.message.reply_text(
        f"📊 News Radar Status\n\n"
        f"📨 Total messages: {stats['total_messages']}\n"
        f"✅ Analyzed: {stats['analyzed_messages']}\n"
        f"⏳ Pending: {stats['pending_messages']}\n\n"
        f"📡 Active sources: {stats['active_sources']}\n"
        f"⚡ Last hour: {stats['messages_last_hour']} messages\n"
        f"📅 Last 24h: {stats['messages_last_24h']} messages"
    )


async def cmd_hot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show trending topics for the last 6 hours."""
    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text("🔍 Looking for hot topics...")

    topics = await fetch_api("/topics?hours=6&limit=5")
    if not topics:
        await update.message.reply_text("No data yet — analysis may still be running")
        return

    lines = ["🔥 Hot Topics (last 6 hours)\n"]
    for i, topic in enumerate(topics, 1):
        temp = topic["avg_temperature"]
        emoji = "🔴" if temp >= 8 else "🟠" if temp >= 6 else "🟡"
        lines.append(f"{emoji} {topic['topic']} — {temp}/10 ({topic['message_count']} msgs)")

        if topic.get("top_message"):
            preview = topic["top_message"][:120]
            if len(topic["top_message"]) > 120:
                preview += "..."
            lines.append(f"   → {preview}")
        lines.append("")

    await update.message.reply_text("\n".join(lines))


async def cmd_digest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Send the latest AI-generated digest."""
    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text("📝 Loading digest...")

    digest = await fetch_api("/digest/latest")

    args = ctx.args or []
    force_new = "new" in map(str.lower, args)

    if not digest or force_new:
        await update.message.reply_text("⚙️ Generating new digest... This may take up to a minute.")
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{API_URL}/digest/generate?hours=6")
                if resp.status_code == 200:
                    digest = resp.json()
                else:
                    await update.message.reply_text(f"❌ API Error: {resp.text}")
                    return
        except Exception as e:
            logger.error(f"Failed to generate digest: {e}")
            await update.message.reply_text("❌ Failed to contact API.")
            return

    if not digest:
        await update.message.reply_text("❌ Digest not found and could not generate one.")
        return

    content = digest["content_md"]

    # Telegram message limit is 4096 chars
    if len(content) > 4000:
        content = content[:4000] + "\n\n... (truncated)"

    await update.message.reply_text(content, parse_mode="Markdown")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text(
        "📡 News Radar — AI news aggregator\n\n"
        "Commands:\n"
        "/hot — trending topics right now\n"
        "/digest — latest AI digest\n"
        "/status — system stats (channels, messages)\n"
        "/help — this message\n\n"
        "Auto-digest is sent every 3 hours."
    )


# ──────────────────────────────────────────────
# AUTO-DIGEST (scheduled)
# ──────────────────────────────────────────────

async def send_auto_digest(app: Application) -> None:
    """Send automatic digest to all authorized users."""
    digest = await fetch_api("/digest/latest")
    if not digest:
        return

    content = digest["content_md"]
    if len(content) > 4000:
        content = content[:4000] + "\n\n..."

    text = f"⏰ Auto Digest\n\n{content}"

    for user_id in list(ALLOWED_USERS):
        try:
            await app.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send digest to {user_id}: {e}")


def main():
    """Bot entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    interval_hours = int(os.environ.get("DIGEST_INTERVAL_HOURS", "3"))

    # Load allowed user IDs from env
    allowed_raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
    for uid in allowed_raw.split(","):
        uid = uid.strip()
        if uid.isdigit():
            ALLOWED_USERS.add(int(uid))

    logger.info(f"Allowed users: {ALLOWED_USERS or 'ALL (dev mode)'}")

    app = Application.builder().token(token).build()

    # Register command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("hot", cmd_hot))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("help", cmd_help))

    # Schedule auto-digest
    app.job_queue.run_repeating(
        callback=lambda ctx: send_auto_digest(app),
        interval=interval_hours * 3600,
        first=interval_hours * 3600,
    )

    logger.info("News Radar Bot started")

    # python-telegram-bot v20+ manages its own event loop
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
