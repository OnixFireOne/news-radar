"""The site extra must see its config through the real analyzer path, not a hand-built context."""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

import analyzer.analyzer as module
from analyzer.knowledge_publisher import GitHubPublisher
from config.config_watcher import DEFAULT_CONFIG
from database.schema import get_db, init_db


@pytest.mark.asyncio
async def test_run_digest_commits_site_and_announces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = str(tmp_path / "site.db")
    init_db(path)
    conn = get_db(path)
    conn.execute("INSERT INTO sources (id, type, name) VALUES (1, 'rss', 'feed')")
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO messages (id, source_id, external_id, text, url, analyzed, in_digest, collected_at) "
                 "VALUES (1, 1, '1', 'Article one', 'https://example.org/1', 1, 0, ?)", (now,))
    conn.execute("INSERT INTO analysis (message_id, temperature, content_type, value_score, topic, takeaway) "
                 "VALUES (1, 5, 'tool_release', 8, 'agents', 'Вывод')")
    conn.commit()
    conn.close()

    values: dict[str, Any] = deepcopy(DEFAULT_CONFIG)
    values["categories"] = {"articles": {"enabled": True, "sources": ["rss"], "analyzer": "ai_value", "hooks": [],
                                         "select": "quotas", "template": "ai_value", "extras": ["site"]}}
    values["digests"] = [{"name": "articles", "enabled": True, "categories": ["articles"], "at": ["09:10"],
                          "tz": "Europe/Moscow"}]
    values["digest_templates"]["ai_value"]["telegram"] = "announce"
    values["site"] = dict(values["site"], enabled=True, live=True, branch="main")
    cfg = Mock()
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    cfg.load_topics.return_value = {}
    monkeypatch.setattr(module, "ChromaClient", Mock())
    monkeypatch.setattr(module, "get_embedder", Mock())
    monkeypatch.setattr(module, "LLMLock", nullcontext)
    monkeypatch.setenv("NEURONAVT_GITHUB_TOKEN", "fake")
    commit = AsyncMock(return_value=True)
    monkeypatch.setattr(GitHubPublisher, "commit_files", commit)
    llm = AsyncMock()
    llm.complete_json.return_value = {"items": [{"source_id": "1", "title": "Инструмент", "takeaway": "Вывод",
                                                 "summary": "Суть"}], "lead": "Главное", "highlights": ["Пункт"]}
    llm.router = Mock()

    parts = await module.NewsAnalyzer(path, llm, cfg=cfg).run_digest("articles")

    commit.assert_awaited_once()
    assert parts and parts[0].site_url and parts[0].site_url.startswith("https://neuronavt.blog/posts/")
    assert "Читать выпуск на сайте" in parts[0].result
