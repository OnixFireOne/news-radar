"""/digest/generate tells an empty window (400) from a failed generation (502)."""
from __future__ import annotations

import importlib
import importlib.util
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx


@unittest.skipUnless(importlib.util.find_spec("api"), "Run in the API image")
class ApiDigestErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_follows_failure_kind(self) -> None:
        api: Any = importlib.import_module("api.main")
        config_module: Any = importlib.import_module("config.config_watcher")
        values = {"digests": [{"name": "articles", "enabled": True, "categories": ["articles"]}]}
        cfg = Mock()
        cfg.get.side_effect = lambda key, default=None: values.get(key, default)
        for failures, status, text in [(["no_news"], 400, "No new analyzed news"),
                                       (["no_news", "llm"], 502, "llm error")]:
            fake = Mock(cfg=cfg, run_digest=AsyncMock(return_value=[]), digest_failures=failures)
            with patch.object(api, "build_llm_client", Mock()), \
                 patch.object(api, "NewsAnalyzer", Mock(return_value=fake)), \
                 patch.object(config_module, "ConfigWatcher", Mock(return_value=cfg)):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                                             base_url="http://test") as client:
                    response = await client.post("/digest/generate?name=articles")
            self.assertEqual(response.status_code, status)
            self.assertIn(text, response.json()["detail"])
