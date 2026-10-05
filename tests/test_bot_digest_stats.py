"""Digest accounting is private to the explicit admin allowlist."""
from __future__ import annotations

import importlib
import importlib.util
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch


@unittest.skipUnless(importlib.util.find_spec('telegram'), 'Run in the bot image')
class BotDigestStatsTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduled_and_manual_stats_only_to_admins(self) -> None:
        bot: Any = importlib.import_module('bot.telegram_bot')
        telegram = Mock(send_message=AsyncMock())
        app = SimpleNamespace(bot=telegram)
        update = SimpleNamespace(effective_user=SimpleNamespace(id=123), message=Mock(reply_text=AsyncMock()))
        ctx = SimpleNamespace(args=['new', 'articles'], bot=telegram)
        digest = {'content_md': 'news', 'parse_mode': 'HTML', 'stats': 'private stats'}
        client = AsyncMock()
        client.post.return_value = Mock(status_code=200, json=Mock(return_value=digest))
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        with patch.object(bot, 'ALLOWED_USERS', {123}), patch.object(bot, 'ADMIN_USERS', {456, 789}), \
             patch.object(bot, 'fetch_api', AsyncMock(return_value={})), \
             patch.object(bot.httpx, 'AsyncClient', return_value=manager):
            await bot.perform_scheduled_digest(app, 'articles')
            calls = telegram.send_message.call_args_list
            self.assertEqual([call.kwargs['chat_id'] for call in calls if call.kwargs['text'] == 'private stats'], [456, 789])
            self.assertTrue(all(call.kwargs['parse_mode'] is None for call in calls if call.kwargs['text'] == 'private stats'))
            telegram.send_message.reset_mock()
            await bot.cmd_digest(update, ctx)
            self.assertEqual([call.kwargs['chat_id'] for call in telegram.send_message.call_args_list], [456, 789])
            self.assertFalse(any(call.args[0] == 'private stats' for call in update.message.reply_text.call_args_list))
            telegram.send_message.reset_mock()
            with patch.object(bot, 'ADMIN_USERS', set()):
                await bot.send_digest_stats(telegram, 'private stats')
            telegram.send_message.assert_not_awaited()

    async def test_command_admin_gate_default_and_name(self) -> None:
        bot: Any = importlib.import_module('bot.telegram_bot')
        update = SimpleNamespace(effective_user=SimpleNamespace(id=123), message=Mock(reply_text=AsyncMock()))
        ctx = SimpleNamespace(args=['AI news'])
        fetch = AsyncMock(return_value={'text': 'stats'})
        with patch.object(bot, 'ADMIN_USERS', {456}), patch.object(bot, 'fetch_api', fetch):
            await bot.cmd_stats(update, ctx)
            fetch.assert_not_awaited()
            update.effective_user.id = 456
            await bot.cmd_stats(update, ctx)
            fetch.assert_awaited_with('/digest/stats?name=AI+news')
            update.message.reply_text.assert_awaited_with('stats', parse_mode=None)
            ctx.args = []
            await bot.cmd_stats(update, ctx)
            fetch.assert_awaited_with('/digest/stats')

    async def test_one_admin_failure_does_not_block_others(self) -> None:
        bot: Any = importlib.import_module('bot.telegram_bot')
        telegram = Mock(send_message=AsyncMock(side_effect=[RuntimeError('blocked'), None]))
        with patch.object(bot, 'ADMIN_USERS', {1, 2}):
            await bot.send_digest_stats(telegram, 'stats')
        self.assertEqual(telegram.send_message.await_count, 2)
