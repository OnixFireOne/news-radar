"""Bot integration checks; also runnable with stdlib unittest in the bot image."""
from __future__ import annotations

import importlib
import importlib.util
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch


@unittest.skipUnless(importlib.util.find_spec("telegram"), "Run in the bot image")
class BotNamedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot: Any = importlib.import_module("bot.telegram_bot")
        self.app = SimpleNamespace(bot_data={}, job_queue=Mock(), bot=Mock(send_message=AsyncMock()))
        self.app.job_queue.jobs.return_value = []

    async def test_schedule_reload_and_outage(self) -> None:
        old = Mock(name="old")
        old.name = "digest:legacy:12:00"
        other = Mock(name="other")
        other.name = "refresh-digest-schedule"
        self.app.job_queue.jobs.return_value = [old, other]
        settings = {"digests": [{"name": "articles", "enabled": True, "categories": ["articles"],
                                 "at": ["09:10"], "tz": "Europe/Moscow"}]}
        with patch.object(self.bot, "fetch_api", AsyncMock(return_value=settings)):
            await self.bot.refresh_digest_schedule(self.app)
            await self.bot.refresh_digest_schedule(self.app)
        old.schedule_removal.assert_called_once()
        other.schedule_removal.assert_not_called()
        self.app.job_queue.run_daily.assert_called_once()
        kwargs = self.app.job_queue.run_daily.call_args.kwargs
        self.assertEqual(kwargs["name"], "digest:articles:09:10")
        self.assertEqual(kwargs["data"], "articles")
        self.assertEqual(str(kwargs["time"].tzinfo), "Europe/Moscow")
        with patch.object(self.bot, "fetch_api", AsyncMock(return_value=None)):
            await self.bot.refresh_digest_schedule(self.app)
        self.app.job_queue.run_daily.assert_called_once()
        settings["digests"][0]["enabled"] = False
        with patch.object(self.bot, "fetch_api", AsyncMock(return_value=settings)):
            await self.bot.refresh_digest_schedule(self.app)
        self.assertEqual(self.app.bot_data["digest_slots"], frozenset())

    async def test_startup_outage_uses_legacy(self) -> None:
        with patch.object(self.bot, "fetch_api", AsyncMock(return_value=None)):
            await self.bot.refresh_digest_schedule(self.app)
        self.assertEqual(self.app.job_queue.run_daily.call_count, 2)
        self.assertEqual([call.kwargs["time"].hour for call in self.app.job_queue.run_daily.call_args_list], [12, 20])
        self.assertTrue(all(call.kwargs["data"] is None for call in self.app.job_queue.run_daily.call_args_list))

    async def test_scheduled_sends_every_part(self) -> None:
        parts = [{"content_md": "one", "parse_mode": "HTML"}, {"content_md": "two", "parse_mode": "Markdown"}]
        client = AsyncMock()
        client.post.return_value = Mock(status_code=200, json=Mock(return_value={**parts[0], "parts": parts}))
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        with patch.object(self.bot, "fetch_api", AsyncMock(return_value={})), \
             patch.object(self.bot.httpx, "AsyncClient", return_value=manager), \
             patch.object(self.bot, "ALLOWED_USERS", {123}):
            await self.bot.perform_scheduled_digest(self.app, "AI news")
        client.post.assert_awaited_once_with(self.bot.API_URL + "/digest/generate?name=AI+news")
        calls = self.app.bot.send_message.call_args_list
        self.assertEqual([(call.kwargs["text"], call.kwargs["parse_mode"]) for call in calls],
                         [("one", "HTML"), ("two", "Markdown")])

    async def test_manual_name_hours_force_and_parts(self) -> None:
        update = SimpleNamespace(effective_user=SimpleNamespace(id=123), message=Mock(reply_text=AsyncMock()))
        ctx = SimpleNamespace(args=["new", "Articles", "6", "force"])
        parts = [{"content_md": "one", "parse_mode": "HTML"}, {"content_md": "two", "parse_mode": "Markdown"}]
        client = AsyncMock()
        client.post.return_value = Mock(status_code=200, json=Mock(return_value={**parts[0], "parts": parts}))
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        fetch = AsyncMock(side_effect=[None, {}])
        with patch.object(self.bot, "fetch_api", fetch), \
             patch.object(self.bot.httpx, "AsyncClient", return_value=manager), \
             patch.object(self.bot, "ALLOWED_USERS", {123}):
            await self.bot.cmd_digest(update, ctx)
        self.assertEqual(fetch.call_args_list[0].args[0], "/digest/latest?name=Articles")
        client.post.assert_awaited_once_with(self.bot.API_URL + "/digest/generate?force=true&hours=6&name=Articles")
        self.assertEqual([call.args[0] for call in update.message.reply_text.call_args_list[-2:]], ["one", "two"])
