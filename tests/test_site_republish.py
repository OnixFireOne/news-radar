"""Stored-run republication and idempotent delivery HTTP contracts; runnable with stdlib unittest in the API image."""
from __future__ import annotations

import contextlib
import importlib
import importlib.util
import os
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from database.schema import get_db, init_db


@unittest.skipUnless(importlib.util.find_spec("api"), "Run in the API image")
class SiteRepublishTests(unittest.IsolatedAsyncioTestCase):
    async def test_republish_and_delivery(self) -> None:
        api: Any = importlib.import_module("api.main")
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ))
            path = str(Path(directory) / "test.db")
            init_db(path)
            conn = get_db(path)
            for digest_id in (1, 2):
                conn.execute("INSERT INTO digests (id, name, run_id, content_md, period_start, period_end, site_url, site_status) "
                             "VALUES (?, 'articles', 'run', 'announce', '2026-10-08', '2026-10-08', 'https://example.org', 'commit_failed')", (digest_id,))
                conn.execute("INSERT INTO site_files (digest_id, kind, path, content, committed_at) VALUES (?, 'digest', ?, ?, CURRENT_TIMESTAMP)",
                             (digest_id, f"post{digest_id}.md", f"bytes{digest_id}"))
            conn.commit()
            config = Path(directory) / "settings.json"
            config.write_text('{"site": {"enabled": true, "live": false}}')
            stack.enter_context(patch.object(api, "DB_PATH", path))
            stack.enter_context(patch.object(api, "CONFIG_PATH", config))
            os.environ["NEURONAVT_GITHUB_TOKEN"] = "fake"
            commit = AsyncMock(return_value=True)
            publisher = Mock(commit_files=commit, last_commit_sha="saved-sha")
            with patch("analyzer.knowledge_publisher.GitHubPublisher", return_value=publisher) as factory:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
                    for _ in range(2):
                        assert (await client.post("/digest/2/delivered?chat_id=-100")).json()["delivered"] == [-100]
                    latest = (await client.get("/digest/latest?name=articles")).json()
                    assert latest["delivered"] == [-100] and len(latest["parts"]) == 2
                    response = await client.post("/digest/site/republish?name=articles")
                    assert response.status_code == 200
                    assert response.json()["site_url"] == "https://example.org"
                    assert all(part["site_status"] == "ok" for part in response.json()["parts"])
                    assert factory.call_args.kwargs["batch"] is True
                    assert conn.execute("SELECT COUNT(*) FROM site_files WHERE commit_sha='saved-sha'").fetchone()[0] == 2
                    commit.assert_awaited_once_with([("post1.md", "bytes1"), ("post2.md", "bytes2")],
                                                  "feat(radar): republish AI radar digest 2026-10-08")
                    assert conn.execute("SELECT COUNT(*) FROM site_files WHERE committed_at IS NOT NULL").fetchone()[0] == 2
                    commit.return_value = False
                    assert (await client.post("/digest/site/republish?name=articles")).json()["site_status"] == "commit_failed"
                    assert (await client.post("/digest/site/republish?name=missing")).status_code == 404
                    conn.execute("DELETE FROM site_files")
                    conn.commit()
                    assert (await client.post("/digest/site/republish?name=articles")).status_code == 404
                    assert (await client.post("/digest/999/delivered?chat_id=1")).status_code == 404
                    os.environ.pop("NEURONAVT_GITHUB_TOKEN")
                    assert (await client.post("/digest/site/republish?name=articles")).status_code == 400
                    os.environ["NEURONAVT_GITHUB_TOKEN"] = "fake"
                    config.write_text('{"site": {"enabled": false}}')
                    assert (await client.post("/digest/site/republish?name=articles")).status_code == 400
            conn.close()
