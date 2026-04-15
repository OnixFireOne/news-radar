"""
Telegram Bot — вывод дайджестов пользователю.

Это НЕ userbot — это обычный бот через @BotFather.
Команды:
  /start   — приветствие
  /status  — статистика системы
  /hot     — горячие темы прямо сейчас
  /digest  — последний дайджест
  /help    — помощь

Авто-рассылка дайджеста каждые DIGEST_INTERVAL_HOURS часов.
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
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

# Разрешённые пользователи (Telegram user_id через @userinfobot)
ALLOWED_USERS: set[int] = set()


def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True  # если список пустой — разрешаем всем
    return user_id in ALLOWED_USERS


API_URL = os.environ.get("API_URL", "http://localhost:8000")


async def fetch_api(path: str) -> dict | list | None:
    """Получить данные из API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{API_URL}{path}")
            if resp.status_code == 200:
                return resp.json()
            return None
    except Exception as e:
        logger.error(f"API error ({path}): {e}")
        return None


# ──────────────────────────────────────────────
# КОМАНДЫ
# ──────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text(
        "📡 *News Radar* запущен\\!\n\n"
        "Я слежу за Telegram-каналами и анализирую новости через AI\\.\n\n"
        "*Команды:*\n"
        "/hot — горячие темы прямо сейчас\n"
        "/digest — последний дайджест\n"
        "/status — статистика системы\n"
        "/help — помощь",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    stats = await fetch_api("/stats")
    if not stats:
        await update.message.reply_text("❌ API недоступен")
        return

    text = (
        f"📊 *Статус News Radar*\n\n"
        f"📨 Всего сообщений: `{stats['total_messages']}`\n"
        f"✅ Проанализировано: `{stats['analyzed_messages']}`\n"
        f"⏳ В очереди: `{stats['pending_messages']}`\n\n"
        f"📡 Источников: `{stats['active_sources']}` активных\n"
        f"⚡ За час: `{stats['messages_last_hour']}` сообщений\n"
        f"📅 За 24ч: `{stats['messages_last_24h']}` сообщений"
    )

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_hot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Горячие темы за последние 6 часов."""
    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text("🔍 Ищу горячие темы...")

    topics = await fetch_api("/topics?hours=6&limit=5")
    if not topics:
        await update.message.reply_text("Нет данных — возможно, анализ ещё не запускался")
        return

    lines = ["🔥 *Горячие темы за 6 часов*\n"]
    for i, topic in enumerate(topics, 1):
        temp = topic["avg_temperature"]
        emoji = "🔴" if temp >= 8 else "🟠" if temp >= 6 else "🟡"
        lines.append(
            f"{emoji} *{topic['topic']}* — `{temp}/10`\n"
            f"   {topic['message_count']} сообщений\n"
        )
        if topic.get("top_message"):
            preview = topic["top_message"][:100] + "..." if len(topic["top_message"]) > 100 else topic["top_message"]
            lines.append(f"   _{preview}_\n")

    # Экранируем специальные символы для MarkdownV2
    text = "\n".join(lines)
    # Отправляем как обычный текст чтобы не было проблем с экранированием
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=None,  # plain text безопаснее
    )


async def cmd_digest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Последний AI-дайджест."""
    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text("📝 Загружаю дайджест...")

    digest = await fetch_api("/digest/latest")
    if not digest:
        await update.message.reply_text(
            "Дайджест ещё не сгенерирован. Попробуй через 30 минут."
        )
        return

    content = digest["content_md"]
    
    # Telegram ограничивает 4096 символов
    if len(content) > 4000:
        content = content[:4000] + "\n\n... (обрезано)"

    await update.message.reply_text(content, parse_mode=None)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text(
        "📡 News Radar — AI-агрегатор новостей\n\n"
        "Команды:\n"
        "/hot — горячие темы прямо сейчас\n"
        "/digest — последний AI-дайджест\n"
        "/status — статистика (сколько каналов, сообщений)\n"
        "/help — это сообщение\n\n"
        "Авто-дайджест приходит каждые 3 часа."
    )


# ──────────────────────────────────────────────
# АВТОДАЙДЖЕСТ
# ──────────────────────────────────────────────

async def send_auto_digest(app: Application) -> None:
    """Отправить автоматический дайджест всем разрешённым пользователям."""
    digest = await fetch_api("/digest/latest")
    if not digest:
        return

    content = digest["content_md"]
    if len(content) > 4000:
        content = content[:4000] + "\n\n..."

    text = f"⏰ Авто-дайджест\n\n{content}"

    # Отправляем всем разрешённым пользователям
    users = list(ALLOWED_USERS) if ALLOWED_USERS else []
    for user_id in users:
        try:
            await app.bot.send_message(chat_id=user_id, text=text)
        except Exception as e:
            logger.error(f"Failed to send digest to {user_id}: {e}")


async def main():
    """Запуск бота."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    interval_hours = int(os.environ.get("DIGEST_INTERVAL_HOURS", "3"))

    # Загружаем разрешённых пользователей
    allowed_raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
    for uid in allowed_raw.split(","):
        uid = uid.strip()
        if uid.isdigit():
            ALLOWED_USERS.add(int(uid))

    logger.info(f"Allowed users: {ALLOWED_USERS or 'ALL'}")

    app = Application.builder().token(token).build()

    # Регистрируем команды
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("hot", cmd_hot))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("help", cmd_help))

    # Устанавливаем меню команд в Telegram
    await app.bot.set_my_commands([
        BotCommand("hot", "Горячие темы прямо сейчас"),
        BotCommand("digest", "Последний дайджест"),
        BotCommand("status", "Статистика системы"),
        BotCommand("help", "Помощь"),
    ])

    # Авто-дайджест через Job Queue
    app.job_queue.run_repeating(
        callback=lambda ctx: send_auto_digest(app),
        interval=interval_hours * 3600,
        first=interval_hours * 3600,  # первый через N часов
    )

    logger.info("✅ News Radar Bot started")
    await app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
