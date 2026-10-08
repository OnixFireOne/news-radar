"""Offline channel routing and administrator recovery contracts."""
from __future__ import annotations

import importlib
import importlib.util
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch


@unittest.skipUnless(importlib.util.find_spec("telegram"), "Run in the bot image")
class ChannelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.module: Any = importlib.import_module("bot.telegram_bot")
        self.settings: dict[str, Any] = {"channels": [{"chat_id": -100, "digests": ["articles"], "enabled": True}],
                         "site": {"wait_for_page_sec": 37}}
        self.part: dict[str, Any] = {"id": 1, "content_md": "announce", "parse_mode": "HTML", "site_status": "ok",
                     "site_url": "https://example.org/post", "delivered": []}
        self.bot = Mock(send_message=AsyncMock())
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=7), message=Mock(reply_text=AsyncMock()))
        self.ctx = SimpleNamespace(args=["articles", "site"], bot=self.bot)
        self.client = AsyncMock()
        self.response = Mock(status_code=200, json=Mock(return_value={**self.part, "stats": "stats"}))
        self.client.post.return_value = self.response
        self.manager = AsyncMock()
        self.manager.__aenter__.return_value = self.client

    async def test_scheduled_routing_and_failures(self) -> None:
        for name, status, ready, expected in [("articles", "ok", True, [-100, 7]),
                                             ("articles", "commit_failed", True, [7, 7]),
                                             ("articles", "ok", False, [7, 7]),
                                             ("crypto", "ok", True, [9, 7])]:
            with self.subTest(name=name, status=status, ready=ready):
                self.bot.send_message.reset_mock()
                part = dict(self.part, site_status=status, delivered=[])
                self.response.json.return_value = dict(part, stats="stats")
                with patch.object(self.module, "fetch_api", AsyncMock(return_value=self.settings)), \
                     patch.object(self.module.httpx, "AsyncClient", return_value=self.manager), \
                     patch.object(self.module, "wait_for_parts", AsyncMock(return_value=[ready])), \
                     patch.object(self.module, "ALLOWED_USERS", {9}), patch.object(self.module, "ADMIN_USERS", {7}):
                    await self.module.perform_scheduled_digest(SimpleNamespace(bot=self.bot), name)
                calls = self.bot.send_message.call_args_list
                self.assertEqual([call.kwargs["chat_id"] for call in calls], expected)
                if name == "articles" and (status != "ok" or not ready):
                    self.assertIn("/digest articles site", calls[0].kwargs["text"])
                if expected[0] == -100:
                    self.assertIn("/digest/1/delivered?chat_id=-100", self.client.post.call_args.args[0])

    async def test_recovery_open_and_already_delivered(self) -> None:
        for delivered, count in [([], 1), ([-100], 0)]:
            self.bot.send_message.reset_mock()
            self.update.message.reply_text.reset_mock()
            with patch.object(self.module, "fetch_api", AsyncMock(side_effect=[dict(self.part, delivered=delivered), self.settings])), \
                 patch.object(self.module, "wait_for_site_page", AsyncMock(return_value=True)), \
                 patch.object(self.module.httpx, "AsyncClient", return_value=self.manager), \
                 patch.object(self.module, "ADMIN_USERS", {7}):
                await self.module.cmd_digest(self.update, self.ctx)
            self.assertEqual(self.bot.send_message.await_count, count)
            self.assertIn("announce", [call.args[0] for call in self.update.message.reply_text.call_args_list])
            self.assertFalse(any("republish" in call.args[0] for call in self.client.post.call_args_list))

    async def test_republish_success_and_failure(self) -> None:
        for status, count in [("ok", 1), ("commit_failed", 0)]:
            self.bot.send_message.reset_mock()
            self.response.json.return_value = dict(self.part, site_status=status)
            with patch.object(self.module, "fetch_api", AsyncMock(side_effect=[dict(self.part), self.settings])), \
                 patch.object(self.module, "wait_for_site_page", AsyncMock(return_value=False)), \
                 patch.object(self.module, "wait_for_parts", AsyncMock(return_value=[True])), \
                 patch.object(self.module.httpx, "AsyncClient", return_value=self.manager), \
                 patch.object(self.module, "ADMIN_USERS", {7}):
                await self.module.cmd_digest(self.update, self.ctx)
            channels = [call for call in self.bot.send_message.call_args_list if call.kwargs["chat_id"] == -100]
            self.assertEqual(len(channels), count)
            if not count:
                self.assertIn("commit_failed", self.bot.send_message.call_args.kwargs["text"])

    async def test_arguments_and_invalid_channels(self) -> None:
        for args, admins, expected in [(["site"], {7}, "Usage:"), (["articles", "site"], set(), "Admins only")]:
            self.ctx.args = args
            with patch.object(self.module, "ADMIN_USERS", admins):
                await self.module.cmd_digest(self.update, self.ctx)
            self.assertTrue(self.update.message.reply_text.call_args.args[0].startswith(expected))
        settings = {"channels": [None, {"chat_id": True, "digests": []}, {"chat_id": 4, "digests": "articles"},
                                 *self.settings["channels"], *self.settings["channels"]]}
        self.assertEqual(self.module.channels_for(settings, "articles"), [-100])
