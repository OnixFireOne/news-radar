"""A digest slot missed while the host slept still fires on wake-up; runs in the bot image."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import importlib
import importlib.util
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch


@unittest.skipUnless(importlib.util.find_spec("telegram"), "Run in the bot image")
class DigestMisfireTests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_passes_misfire_grace(self) -> None:
        bot: Any = importlib.import_module("bot.telegram_bot")
        app = SimpleNamespace(bot_data={}, job_queue=Mock())
        app.job_queue.jobs.return_value = []
        settings = {"digests": [{"name": "articles", "enabled": True, "categories": ["articles"],
                                 "at": ["09:10"], "tz": "Europe/Moscow"}]}
        with patch.object(bot, "fetch_api", AsyncMock(return_value=settings)):
            await bot.refresh_digest_schedule(app)
        job_kwargs = app.job_queue.run_daily.call_args.kwargs["job_kwargs"]
        self.assertGreaterEqual(job_kwargs["misfire_grace_time"], 3600)
        self.assertTrue(job_kwargs["coalesce"])

    async def test_late_wakeup_runs_once_within_grace(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        bot: Any = importlib.import_module("bot.telegram_bot")
        calls: list[str] = []

        async def job() -> None:
            calls.append("run")

        scheduler = AsyncIOScheduler(timezone=timezone.utc)
        scheduler.start()
        try:
            # Simulates waking up 30 minutes after the slot: the run time is already in the past.
            scheduler.add_job(job, "date", run_date=datetime.now(timezone.utc) - timedelta(minutes=30),
                              misfire_grace_time=bot.DIGEST_MISFIRE_GRACE_SECONDS, coalesce=True)
            await asyncio.sleep(0.3)
        finally:
            scheduler.shutdown(wait=False)
        self.assertEqual(calls, ["run"])
