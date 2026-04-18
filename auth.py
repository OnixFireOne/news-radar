"""
One-time authorization script for the Telegram userbot.

Run this ONCE on your machine (not in Docker) to create the session file.
After authorization, copy the session file to data/sessions/

Usage:
    pip install telethon
    python auth.py
"""

import asyncio
import os
from pathlib import Path
from telethon import TelegramClient

# Credentials (same as in .env)
API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"
SESSION_NAME = "news_radar_session"

# Session will be saved in data/sessions/
SESSION_DIR = Path("./data/sessions")
SESSION_DIR.mkdir(parents=True, exist_ok=True)
SESSION_PATH = str(SESSION_DIR / SESSION_NAME)


async def main():
    print("=" * 50)
    print("  News Radar — Telegram Userbot Authorization")
    print("=" * 50)
    print(f"\nSession will be saved to: {SESSION_PATH}.session")
    print("\nYou will be asked for your phone number and SMS code.\n")

    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)

    await client.start()  # prompts for phone + SMS code

    me = await client.get_me()
    print("\n" + "=" * 50)
    print(f"  SUCCESS!")
    print(f"  Logged in as: {me.first_name} (@{me.username})")
    print(f"  Session saved to: {SESSION_PATH}.session")
    print("=" * 50)
    print("\nYou can now start all services with:")
    print("  docker-compose up -d")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
