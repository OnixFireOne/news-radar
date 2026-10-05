"""Named digest HTTP contracts; runnable with stdlib unittest in the API image."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from analyzer.pipeline.categories import DigestPart, UnknownDigestError
from database.schema import get_db, init_db


@unittest.skipUnless(importlib.util.find_spec("api"), "Run in the API image")
class ApiNamedTests(unittest.IsolatedAsyncioTestCase):
    async def test_named_parts_filters_and_errors(self) -> None:
        api: Any = importlib.import_module("api.main")
        config_module: Any = importlib.import_module("config.config_watcher")
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "api.db")
            init_db(path)
            conn = get_db(path)
            stamp = "2026-09-27T08:00:00"
            conn.executemany("INSERT INTO digests (id, content_md, parse_mode, period_start, period_end, name, category) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)",
                             [(1, "one", "HTML", stamp, stamp, "morning", "articles"),
                              (2, "two", "Markdown", stamp, stamp, "morning", "crypto"),
                              (3, "other", "HTML", stamp, stamp, "evening", "articles")])
            conn.commit()
            conn.close()
            values = {"digests": [{"name": "morning", "enabled": True, "categories": ["articles", "crypto"],
                                    "at": ["09:10"], "tz": "Europe/Moscow"}]}
            cfg = Mock()
            cfg.get.side_effect = lambda key, default=None: values.get(key, default)
            fake = Mock(cfg=cfg, run_digest=AsyncMock(return_value=[DigestPart("articles", "one", 1),
                                                                  DigestPart("crypto", "two", 2)]))
            settings_path = Path(directory) / "settings.json"
            settings_path.write_text('{"digests": [{"name": "morning"}], "categories": {}}')
            with patch.object(api, "DB_PATH", path), \
                 patch.object(api, "build_llm_client", Mock()), \
                 patch.object(api, "NewsAnalyzer", Mock(return_value=fake)), \
                 patch.object(config_module, "ConfigWatcher", Mock(return_value=cfg)), \
                 patch.object(api, "CONFIG_PATH", settings_path):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
                    response = await client.post("/digest/generate?name=morning&hours=6&force=true")
                    self.assertEqual(response.status_code, 200)
                    body = response.json()
                    self.assertEqual((body["id"], body["name"], body["category"]), (1, "morning", "articles"))
                    self.assertEqual([(part["id"], part["parse_mode"]) for part in body["parts"]], [(1, "HTML"), (2, "Markdown")])
                    fake.run_digest.assert_awaited_once_with("morning", 6, True)
                    response = await client.post("/digest/raw?name=morning")
                    self.assertEqual(response.json(), {"raw_text": "one", "parts": [
                        {"category": "articles", "raw_text": "one"}, {"category": "crypto", "raw_text": "two"}]})
                    self.assertEqual((await client.get("/digest/latest?name=morning")).json()["id"], 2)
                    self.assertEqual([part["id"] for part in (await client.get("/digest?name=morning")).json()], [2, 1])
                    self.assertIn("digests", (await client.get("/settings")).json())
                    fake.run_digest.return_value = []
                    self.assertEqual((await client.post("/digest/generate?name=morning")).status_code, 400)
                    self.assertEqual((await client.post("/digest/raw?name=morning")).status_code, 400)
                    self.assertEqual((await client.post("/digest/generate?name=missing")).status_code, 404)
                    fake.run_digest.side_effect = UnknownDigestError("Unknown digest: missing")
                    self.assertEqual((await client.post("/digest/raw?name=missing")).status_code, 404)
