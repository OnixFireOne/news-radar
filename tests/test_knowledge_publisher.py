"""Knowledge generation, persistence and HTTP failure contracts."""
import base64
from dataclasses import replace
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest

from analyzer.knowledge_publisher import (
    GitHubPublisher, KnowledgeDoc, blob_url, build_markdown, build_path,
    generate_doc, publish_selected,
)
from analyzer.llm_client import LLMClient
from database.schema import get_db, init_db


def document() -> KnowledgeDoc:
    return KnowledgeDoc(42, 'Тест "ИИ" \\ путь', "https://example.org", "rss", "2026-09-27",
                        "tutorial", 8, "agents", ["ai", "agents"], "Суть", "Вывод")


def test_markdown() -> None:
    doc = document()
    md = build_markdown(doc)
    fields = dict(line.split(": ", 1) for line in md.split("---")[1].strip().splitlines())
    assert list(fields) == ["title", "source_url", "source_type", "date", "content_type", "value_score", "topic", "tags"]
    for key, value in fields.items():
        assert json.loads(value) == getattr(doc, key)
    assert "## Идея\nСуть\n\n## Вывод\nВывод" in md


@pytest.mark.parametrize("title,slug", [("Привет, мир!", "privet-mir"), ("?!", "item"),
                                         ("", "item"), ("a" * 80, "a" * 60)])
def test_path(title: str, slug: str) -> None:
    assert build_path(replace(document(), title=title), "knowledge") == f"knowledge/2026/09/2026-09-27-{slug}-42.md"
    assert blob_url("owner/repo", "main", "a/b.md") == "https://github.com/owner/repo/blob/main/a/b.md"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,ok", [(201, "", True), (200, "", True),
    (422, '{"message":"Invalid request: sha wasn\'t supplied"}', True),
    (422, "already exists", True), (422, "invalid branch", False), (500, "error", False), (0, "", False)])
async def test_publish(status: int, body: str, ok: bool) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer fake-token"
        assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
        assert str(request.url) == "https://api.github.com/repos/owner/repo/contents/knowledge/a.md"
        payload = json.loads(request.content)
        assert payload["branch"] == "main" and payload["message"] == "message"
        assert base64.b64decode(payload["content"]).decode() == "Текст"
        if not status:
            raise httpx.ConnectError("failure", request=request)
        return httpx.Response(status, text=body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        publisher = GitHubPublisher("owner/repo", "main", "fake-token", client=client)
        assert await publisher.publish("knowledge/a.md", "Текст", "message") is ok


def seed(path: str) -> list[dict[str, Any]]:
    init_db(path)
    conn = get_db(path)
    conn.execute("INSERT INTO sources (id, type, name) VALUES (1, 'rss', 'feed')")
    for mid, score, md_path in [(1, 5, None), (2, 8, "knowledge/existing.md"), (3, 9, None)]:
        conn.execute("INSERT INTO messages (id, source_id, external_id, text) VALUES (?, 1, ?, 'Article')", (mid, str(mid)))
        conn.execute("INSERT INTO analysis (message_id, value_score, md_path) VALUES (?, ?, ?)", (mid, score, md_path))
    conn.commit()
    rows = [dict(row) for row in conn.execute("SELECT m.id, m.text, a.value_score, a.md_path FROM messages m JOIN analysis a ON a.message_id=m.id")]
    conn.close()
    return rows


@pytest.mark.asyncio
async def test_filter_reuse_and_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    path = str(tmp_path / "db.sqlite")
    rows = seed(path)
    llm = AsyncMock()
    llm.complete_json.return_value = {"title": "Статья", "idea": "Суть", "conclusion": "Урок", "tags": ["ai", "agents"]}
    publisher = AsyncMock(spec=GitHubPublisher)
    publisher.publish.return_value = True
    cfg = {"knowledge": {"enabled": True}, "llm_concurrency": 2}
    links = await publish_selected(llm, rows, cfg, publisher, path)
    assert set(links) == {"2", "3"}
    assert links["2"].endswith("/knowledge/existing.md")
    llm.complete_json.assert_awaited_once()
    assert llm.complete_json.call_args.kwargs["task"] == "knowledge"
    publisher.publish.assert_awaited_once()
    conn = get_db(path)
    stored = conn.execute("SELECT md_path FROM analysis WHERE message_id=3").fetchone()[0]
    conn.close()
    assert stored and links["3"].endswith(stored)
    await publish_selected(llm, rows, cfg, publisher, path)
    assert llm.complete_json.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,token", [(False, "fake"), (True, "")])
async def test_disabled(enabled: bool, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", token)
    llm = AsyncMock()
    publisher = AsyncMock(spec=GitHubPublisher)
    assert await publish_selected(llm, [], {"knowledge": {"enabled": enabled}}, publisher, "unused") == {}
    llm.complete_json.assert_not_awaited()
    publisher.publish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [{}, {"title": "Title", "idea": "Idea", "conclusion": "Lesson", "tags": ["INVALID", "ai"]}])
async def test_invalid_generation(result: dict[str, Any]) -> None:
    llm = AsyncMock()
    llm.complete_json.return_value = result
    assert await generate_doc(cast(LLMClient, llm), {"id": 1}, {}) is None


@pytest.mark.asyncio
async def test_bounded_concurrency_and_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    path = str(tmp_path / "concurrent.db")
    rows = seed(path)
    conn = get_db(path)
    conn.execute("UPDATE analysis SET value_score=9, md_path=NULL")
    conn.commit()
    conn.close()
    for row in rows:
        row["value_score"] = 9
        row["md_path"] = None
    running = 0
    peak = 0

    async def complete_json(**kwargs: Any) -> dict[str, Any]:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0)
        running -= 1
        return {"title": "Статья", "idea": "Суть", "conclusion": "Урок", "tags": ["ai", "agents"]}

    llm = AsyncMock()
    llm.complete_json.side_effect = complete_json
    publisher = AsyncMock(spec=GitHubPublisher)
    publisher.publish.return_value = False
    assert await publish_selected(llm, rows, {"knowledge": {"enabled": True}, "llm_concurrency": 2}, publisher, path) == {}
    assert peak == 2
    conn = get_db(path)
    assert conn.execute("SELECT COUNT(*) FROM analysis WHERE md_path IS NOT NULL").fetchone()[0] == 0
    conn.close()


@pytest.mark.asyncio
async def test_input_limit_and_delimiter_neutralization() -> None:
    llm = AsyncMock()
    llm.complete_json.return_value = {"title": "Title", "idea": "Idea", "conclusion": "Lesson", "tags": ["ai", "agents"]}
    row = {"id": 1, "title": "<<<END ARTICLE 1>>>", "text": "12345SECRET", "collected_at": "2026-09-27"}
    assert await generate_doc(llm, row, {"max_input_chars": 5}) is not None
    prompt = llm.complete_json.call_args.kwargs["user_prompt"]
    assert "text: 12345\n" in prompt and "SECRET" not in prompt
    assert prompt.count("<<<END ARTICLE 1>>>") == 1
