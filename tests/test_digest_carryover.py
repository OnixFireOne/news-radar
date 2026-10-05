"""Carry-over pool: articles not yet in a digest compete for carryover_days, not just since the last digest."""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

import analyzer.analyzer as module
from config.config_watcher import DEFAULT_CONFIG
from database.schema import get_db, init_db


def stamp(days: float) -> str:
    return (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.mark.asyncio
@pytest.mark.parametrize("carryover", [0, 7])
async def test_pool_keeps_unselected_articles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, carryover: int) -> None:
    path = str(tmp_path / "pool.db")
    init_db(path)
    conn = get_db(path)
    conn.execute("INSERT INTO sources (id, type, name) VALUES (1, 'rss', 'feed')")
    # id: (published days ago, value_score, in_digest)
    articles = {1: (0.1, 6, 0),   # fresh
                2: (2.0, 9, 0),   # published before the last digest, collected after it: was lost
                3: (6.5, 8, 0),   # old but inside 7 days
                4: (8.0, 10, 0),  # expired
                5: (1.0, 10, 1)}  # already in a digest
    for mid, (age, score, in_digest) in articles.items():
        conn.execute("INSERT INTO messages (id, source_id, external_id, text, url, analyzed, in_digest, collected_at) "
                     "VALUES (?, 1, ?, ?, ?, 1, ?, ?)",
                     (mid, str(mid), f"Article MARK{mid}X", f"https://example.org/{mid}", in_digest, stamp(age)))
        conn.execute("INSERT INTO analysis (message_id, temperature, content_type, value_score, topic, takeaway) "
                     "VALUES (?, 5, 'practical_case', ?, 'agents', 'Вывод')", (mid, score))
    # The previous digest ended 12 hours ago: without carry-over only article 1 is in the window.
    conn.execute("INSERT INTO digests (content_md, period_start, period_end, name, category, created_at) "
                 "VALUES ('old', ?, ?, 'articles', 'articles', ?)", (stamp(1.5), stamp(0.5), stamp(0.5)))
    conn.commit()
    conn.close()

    values: dict[str, Any] = deepcopy(DEFAULT_CONFIG)
    values["categories"] = {"articles": {"enabled": True, "sources": ["rss"], "analyzer": "ai_value", "hooks": [],
                                         "select": "quotas", "template": "ai_value", "extras": []}}
    values["digests"] = [{"name": "articles", "enabled": True, "categories": ["articles"], "at": ["09:10"],
                          "tz": "Europe/Moscow"}]
    values["digest_templates"]["ai_value"]["carryover_days"] = carryover
    values["knowledge"]["enabled"] = False
    cfg = Mock()
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    cfg.load_topics.return_value = {}
    monkeypatch.setattr(module, "ChromaClient", Mock())
    monkeypatch.setattr(module, "get_embedder", Mock())
    monkeypatch.setattr(module, "LLMLock", nullcontext)
    llm = AsyncMock()
    seen: list[str] = []

    async def complete_json(**kwargs: Any) -> dict[str, Any]:
        prompt = kwargs["user_prompt"]
        ids = [mid for mid in articles if f"MARK{mid}X" in prompt]
        seen.extend(map(str, ids))
        return {"items": [{"source_id": str(i), "title": f"T{mid}", "takeaway": "Вывод", "summary": "Суть"}
                          for i, mid in enumerate(ids, 1)]}

    llm.complete_json.side_effect = complete_json
    llm.router = Mock()
    analyzer = module.NewsAnalyzer(path, llm, cfg=cfg)
    parts = await analyzer.run_digest("articles")
    assert parts
    assert sorted(seen) == (["1"] if carryover == 0 else ["1", "2", "3"])
    conn = get_db(path)
    chosen = {int(row[0]) for row in conn.execute("SELECT id FROM messages WHERE in_digest=1 AND id != 5")}
    conn.close()
    assert chosen == ({1} if carryover == 0 else {1, 2, 3})
