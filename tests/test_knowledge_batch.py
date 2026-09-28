"""One GitHub commit per digest run (knowledge.batch_commit) through the Git Data API."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from analyzer.knowledge_publisher import GitHubPublisher, publish_selected
from database.schema import get_db, init_db

API = "https://api.github.com/repos/owner/repo/"


def github(moves: int = 0, fail_tree: bool = False) -> tuple[httpx.MockTransport, list[tuple[str, str, Any]]]:
    """Fake Git Data API; the ref update answers 422 ``moves`` times (someone pushed meanwhile)."""
    calls: list[tuple[str, str, Any]] = []
    state = {"moves": moves}

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer fake-token"
        path = str(request.url).removeprefix(API)
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, path, body))
        if request.method == "GET" and path == "git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "head"}})
        if request.method == "GET" and path == "git/commits/head":
            return httpx.Response(200, json={"tree": {"sha": "base-tree"}})
        if request.method == "POST" and path == "git/trees":
            return httpx.Response(500 if fail_tree else 201, json={"sha": "new-tree"})
        if request.method == "POST" and path == "git/commits":
            return httpx.Response(201, json={"sha": "new-commit"})
        if request.method == "PATCH" and path == "git/refs/heads/main":
            if state["moves"]:
                state["moves"] -= 1
                return httpx.Response(422, json={"message": "Update is not a fast forward"})
            return httpx.Response(200, json={})
        return httpx.Response(404)

    return httpx.MockTransport(handle), calls


@pytest.mark.asyncio
async def test_commit_files_makes_one_commit() -> None:
    transport, calls = github()
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = GitHubPublisher("owner/repo", "main", "fake-token", client=client, batch=True)
        assert await publisher.commit_files([("knowledge/a.md", "А"), ("knowledge/b.md", "Б")], "msg")
    assert [(method, path) for method, path, _ in calls] == [
        ("GET", "git/ref/heads/main"), ("GET", "git/commits/head"), ("POST", "git/trees"),
        ("POST", "git/commits"), ("PATCH", "git/refs/heads/main")]
    tree = calls[2][2]
    assert tree["base_tree"] == "base-tree"
    assert [(item["path"], item["content"]) for item in tree["tree"]] == [("knowledge/a.md", "А"), ("knowledge/b.md", "Б")]
    assert calls[3][2] == {"message": "msg", "tree": "new-tree", "parents": ["head"]}
    assert calls[4][2] == {"sha": "new-commit", "force": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("moves,fail_tree,ok", [(1, False, True), (3, False, False), (0, True, False)])
async def test_commit_files_retries_moved_branch(moves: int, fail_tree: bool, ok: bool) -> None:
    transport, calls = github(moves, fail_tree)
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = GitHubPublisher("owner/repo", "main", "fake-token", client=client, batch=True)
        assert await publisher.commit_files([("knowledge/a.md", "А")], "msg") is ok
    assert sum(1 for method, _, _ in calls if method == "PATCH") == (0 if fail_tree else min(moves + 1, 3))


def seed(path: str) -> list[dict[str, Any]]:
    init_db(path)
    conn = get_db(path)
    conn.execute("INSERT INTO sources (id, type, name) VALUES (1, 'rss', 'feed')")
    for mid in (1, 2):
        conn.execute("INSERT INTO messages (id, source_id, external_id, text) VALUES (?, 1, ?, 'Article')", (mid, str(mid)))
        conn.execute("INSERT INTO analysis (message_id, value_score) VALUES (?, 8)", (mid,))
    conn.commit()
    rows = [dict(row) for row in conn.execute(
        "SELECT m.id, m.text, a.value_score, a.md_path FROM messages m JOIN analysis a ON a.message_id=m.id")]
    conn.close()
    return rows


@pytest.mark.asyncio
@pytest.mark.parametrize("ok", [True, False])
async def test_digest_run_publishes_in_one_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ok: bool) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    path = str(tmp_path / "db.sqlite")
    rows = seed(path)
    llm = AsyncMock()
    llm.complete_json.return_value = {"title": "Статья", "idea": "Суть", "conclusion": "Урок", "tags": ["ai", "agents"]}
    commit = AsyncMock(return_value=ok)
    single = AsyncMock(return_value=True)
    monkeypatch.setattr(GitHubPublisher, "commit_files", commit)
    monkeypatch.setattr(GitHubPublisher, "publish", single)
    cfg = {"knowledge": {"enabled": True, "targets": ["github"], "batch_commit": True, "repo": "owner/repo"}}
    links = await publish_selected(llm, rows, cfg, None, path)
    commit.assert_awaited_once()
    single.assert_not_awaited()
    files, message = commit.call_args.args
    assert len(files) == 2 and message == "docs(knowledge): add 2 article summaries"
    conn = get_db(path)
    stored = [row[0] for row in conn.execute("SELECT md_path FROM analysis ORDER BY message_id")]
    conn.close()
    if ok:
        assert set(links) == {"1", "2"} and all(stored)
        assert links["1"].endswith(stored[0]) and "github.com/owner/repo/blob/main/" in links["1"]
    else:
        # A failed commit leaves no links and no md_path, so the next run retries the articles.
        assert links == {} and stored == [None, None]
