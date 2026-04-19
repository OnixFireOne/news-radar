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
OPENCLAW_WEBHOOK_URL = os.environ.get("OPENCLAW_WEBHOOK_URL", "").strip()
OPENCLAW_WEBHOOK_TOKEN = os.environ.get("OPENCLAW_WEBHOOK_TOKEN", "").strip()


async def wake_openclaw(text: str) -> bool:
    """Send a message to OpenClaw via /hooks/wake. Returns True if successful."""
    if not OPENCLAW_WEBHOOK_URL:
        return False
    headers = {"Authorization": f"Bearer {OPENCLAW_WEBHOOK_TOKEN}"} if OPENCLAW_WEBHOOK_TOKEN else {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                OPENCLAW_WEBHOOK_URL,
                json={"text": text, "mode": "now"},
                headers=headers,
            )
            return resp.status_code in (200, 202)
    except Exception as e:
        logger.error(f"OpenClaw wake failed: {e}")
        return False


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
        "I monitor 57+ Telegram channels and analyze news with AI.\n\n"
        "Commands:\n"
        "/hot — trending topics right now\n"
        "/digest — latest AI digest\n"
        "/track <topic> — subscribe to a topic\n"
        "/untrack <topic> — remove subscription\n"
        "/my_tracks — your active subscriptions\n"
        "/ask <question> — ask the AI agent\n"
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
        "/track <topic> — subscribe to a topic (e.g. /track SEC)\n"
        "/untrack <topic> — remove subscription\n"
        "/my_tracks — your active subscriptions\n"
        "/ask <question> — ask the AI agent anything\n"
        "/status — system stats (channels, messages)\n"
        "/help — this message"
    )


async def cmd_track(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Subscribe to a topic. Routes to OpenClaw TaskAgent if configured."""
    if not is_allowed(update.effective_user.id):
        return

    query = " ".join(ctx.args or "").strip()
    if not query:
        await update.message.reply_text("Usage: /track <topic>\nExample: /track SEC")
        return

    user_id = str(update.effective_user.id)

    # Save subscription via API
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{API_URL}/subscriptions", json={"user_id": user_id, "query": query})
            saved = resp.status_code in (200, 201)
    except Exception:
        saved = False

    # Notify OpenClaw TaskAgent
    woke = await wake_openclaw(
        f"[NEWS-RADAR COMMAND: track]\n"
        f"User: {user_id}\n"
        f"Query: {query}\n"
        f"Action: Add subscription and confirm to user."
    )

    if woke:
        await update.message.reply_text(f"✅ Подписка `{query}` активирована. Агент будет следить и уведомлять вас о новостях.", parse_mode="Markdown")
    elif saved:
        await update.message.reply_text(f"✅ Подписка `{query}` сохранена. Будем искать новости для вас каждые 30 мин.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ Не удалось сохранить подписку. API недоступен.")


async def cmd_untrack(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Remove a topic subscription."""
    if not is_allowed(update.effective_user.id):
        return

    query = " ".join(ctx.args or "").strip()
    if not query:
        await update.message.reply_text("Usage: /untrack <topic>\nExample: /untrack SEC")
        return

    user_id = str(update.effective_user.id)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(f"{API_URL}/subscriptions", params={"user_id": user_id, "query": query})
            ok = resp.status_code == 200
    except Exception:
        ok = False

    await wake_openclaw(
        f"[NEWS-RADAR COMMAND: untrack]\n"
        f"User: {user_id}\n"
        f"Query: {query}\n"
        f"Action: Remove subscription and confirm to user."
    )

    if ok:
        await update.message.reply_text(f"❌ Подписка `{query}` удалена.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ Подписка `{query}` не найдена.")


async def cmd_my_tracks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """List active subscriptions for this user."""
    if not is_allowed(update.effective_user.id):
        return

    user_id = str(update.effective_user.id)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{API_URL}/subscriptions", params={"user_id": user_id})
            subs = resp.json() if resp.status_code == 200 else []
    except Exception:
        subs = []

    if not subs:
        await update.message.reply_text("💭 У вас нет активных подписок.\n💡 Добавьте через /track <тема>")
        return

    lines = ["📌 Ваши подписки:\n"]
    for s in subs:
        lines.append(f"• `{s['query']}`")
    lines.append("\nУдалить: /untrack <тема>")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ask the OpenClaw agent a question directly."""
    if not is_allowed(update.effective_user.id):
        return

    question = " ".join(ctx.args or "").strip()
    if not question:
        await update.message.reply_text("Usage: /ask <question>\nExample: /ask What is happening with Ethereum today?")
        return

    user_id = str(update.effective_user.id)
    woke = await wake_openclaw(
        f"[NEWS-RADAR COMMAND: ask]\n"
        f"User: {user_id}\n"
        f"Question: {question}\n"
        f"Action: Answer using news context from news-radar API, reply to the user."
    )

    if woke:
        await update.message.reply_text("🤖 Агент получил вопрос, ответит в ближайшее время...")
    else:
        await update.message.reply_text("⚠️ OpenClaw недоступен. Проверьте OPENCLAW_WEBHOOK_URL в .env")


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
    app.add_handler(CommandHandler("track", cmd_track))
    app.add_handler(CommandHandler("untrack", cmd_untrack))
    app.add_handler(CommandHandler("my_tracks", cmd_my_tracks))
    app.add_handler(CommandHandler("ask", cmd_ask))

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
