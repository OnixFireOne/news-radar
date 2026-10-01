"""Candidate audit list and quota ordering behavior."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from analyzer.knowledge_publisher import GitHubPublisher, KnowledgeTarget, publish_selected
from analyzer.analyzer import NewsAnalyzer
from analyzer.pipeline.context import DigestContext
from analyzer.pipeline.extras import build_candidates_markdown, candidates, knowledge
from analyzer.renderer import render_digest
from analyzer.value_funnel import explain_selection, select_with_quotas
from config.config_watcher import DEFAULT_CONFIG
from database.schema import get_db, init_db


def row(mid: int, score: int, kind: str, date: str, text: str = "Article", **extra: Any) -> dict[str, Any]:
    return {"id": mid, "value_score": score, "temperature": 5, "content_type": kind,
            "collected_at": date, "text": text, "url": f"https://example.org/{mid}", **extra}


def test_oldest_tie_break_and_higher_score() -> None:
    rows = [row(1, 8, "tutorial", "2026-10-01"), row(2, 8, "tutorial", "2026-09-28"),
            row(3, 9, "tutorial", "2026-10-02")]
    cfg = {"max_items": 2, "tie_break": "oldest"}
    assert [item["id"] for item in select_with_quotas(rows, cfg)] == [3, 2]
    assert [item["id"] for item in select_with_quotas(rows, {"max_items": 2})] == [3, 1]


def test_explain_final_selection_and_last_day() -> None:
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    rows = [row(1, 9, "tutorial", "2026-09-26"), row(2, 8, "tutorial", "2026-09-25"),
            row(3, 6, "tutorial", "2026-10-01"), row(4, 8, "hype_news", "2026-10-01"),
            row(5, 8, "crypto", "2026-10-01")]
    cfg = {"min_value_score": 7, "carryover_days": 7, "tie_break": "oldest",
           "quotas": {"hype": 0, "crypto": 1}}
    statuses = explain_selection(rows, cfg, [1], now)
    by_id = {item.row["id"]: item for item in statuses}
    assert [item.row["id"] for item in statuses] == [1, 2, 4, 5, 3]
    assert {mid: item.status for mid, item in by_id.items()} == {
        1: "selected", 2: "not_fitted", 3: "below_threshold", 4: "type_off", 5: "type_off"}
    assert by_id[2].last_day is True
    assert by_id[1].last_day is False
    assert by_id[3].last_day is False
    assert explain_selection(rows, cfg, [2], now)[1].status == "selected"


def test_markdown_numbers_filter_order_and_escaping() -> None:
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    pool = [row(1, 8, "tutorial", "2026-10-01", "  A | [B]\nmore"),
            row(2, 9, "research", "2026-09-28", "Research"),
            row(3, 6, "tutorial", "2026-09-25", "Low"),
            row(4, 8, "hype_news", "2026-10-01", "Hype"),
            row(5, 5, "tutorial", "2026-10-01", "Hidden")]
    pool[0]["url"] = "https://example.org/a)"
    cfg: dict[str, Any] = {"min_value_score": 7, "carryover_days": 7, "tie_break": "oldest",
                           "candidates_list": {"enabled": True, "min_score": 6},
                           "quotas": {"practical": 4, "tools_research": 2, "hype": 0},
                           "types": {"tutorial": {"label": "Туториал"}}}
    content, total = build_candidates_markdown(pool, [pool[1]], cfg, "articles", now)
    assert total == 3
    assert "Выбрано 1 из 3 · порог 7 · очередь 7 дн." in content
    assert content.index("Research") < content.index("A \\| \\[B\\]")
    assert "[A \\| \\[B\\]](https://example.org/a%29)" in content
    assert "| 6 | Туториал | [Low]" in content
    assert "⬇ ниже порога" in content
    assert "🚫 тип отключён" in content
    assert "Hidden" not in content
    assert "✅ в выпуске" in content


@pytest.mark.asyncio
@pytest.mark.parametrize("ok", [True, False])
async def test_extra_files_share_batch_commit_and_leave_db_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ok: bool,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    path = str(tmp_path / "db.sqlite")
    init_db(path)
    conn = get_db(path)
    conn.execute("INSERT INTO sources (id, type, name) VALUES (1, 'rss', 'feed')")
    conn.execute("INSERT INTO messages (id, source_id, external_id, text) VALUES (1, 1, '1', 'Article')")
    conn.execute("INSERT INTO analysis (message_id, value_score) VALUES (1, 8)")
    conn.commit()
    conn.close()
    llm = AsyncMock()
    llm.complete_json.return_value = {"title": "Article", "idea": "Idea", "conclusion": "End", "tags": ["ai", "news"]}
    commit = AsyncMock(return_value=ok)
    monkeypatch.setattr(GitHubPublisher, "commit_files", commit)
    files = [("knowledge/candidates/2026-10-02-articles.md", "# Candidates")]
    delivered: list[str] = []
    cfg = {"knowledge": {"enabled": True, "targets": ["github"], "batch_commit": True, "repo": "owner/repo"}}
    rows = [row(1, 8, "tutorial", "2026-10-01")]
    links = await publish_selected(llm, rows, cfg, None, path, extra_files=files, delivered=delivered)
    staged, _ = commit.call_args.args
    assert len(staged) == 2 and staged[-1] == files[0]
    assert bool(links) is ok
    assert delivered == ([files[0][0]] if ok else [])
    conn = get_db(path)
    assert conn.execute("SELECT in_digest FROM messages WHERE id=1").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM digest_messages").fetchone()[0] == 0
    assert bool(conn.execute("SELECT md_path FROM analysis WHERE message_id=1").fetchone()[0]) is ok
    conn.close()


@pytest.mark.asyncio
async def test_candidates_only_batch_when_article_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    path = str(tmp_path / "db.sqlite")
    init_db(path)
    conn = get_db(path)
    conn.execute("INSERT INTO sources (id, type, name) VALUES (1, 'rss', 'feed')")
    conn.execute("INSERT INTO messages (id, source_id, external_id, text) VALUES (1, 1, '1', 'Article')")
    conn.execute("INSERT INTO analysis (message_id, value_score, md_path) VALUES (1, 8, 'knowledge/old.md')")
    conn.commit()
    conn.close()
    commit = AsyncMock(return_value=True)
    monkeypatch.setattr(GitHubPublisher, "commit_files", commit)
    llm = AsyncMock()
    delivered: list[str] = []
    files = [("knowledge/candidates/list.md", "# List")]
    cfg = {"knowledge": {"enabled": True, "targets": ["github"], "batch_commit": True}}
    links = await publish_selected(llm, [row(1, 8, "tutorial", "2026-10-01")], cfg,
                                   None, path, extra_files=files, delivered=delivered)
    commit.assert_awaited_once_with(files, "docs(knowledge): add candidates list")
    llm.complete_json.assert_not_awaited()
    assert links["1"].endswith("knowledge/old.md") and delivered == [files[0][0]]


@pytest.mark.asyncio
async def test_direct_target_publishes_extra_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    path = str(tmp_path / "db.sqlite")
    init_db(path)
    target = Mock()
    target.publish = AsyncMock(return_value=True)
    files = [("knowledge/candidates/list.md", "# List")]
    delivered: list[str] = []
    cfg = {"knowledge": {"enabled": True}}
    links = await publish_selected(AsyncMock(), [], cfg, cast(KnowledgeTarget, target), path,
                                   extra_files=files, delivered=delivered)
    assert links == {} and delivered == [files[0][0]]
    target.publish.assert_awaited_once_with(files[0][0], files[0][1],
                                            "docs(knowledge): add candidates list")


@pytest.mark.asyncio
@pytest.mark.parametrize("delivered_ok", [True, False])
async def test_candidates_extra_is_read_only_and_links_only_after_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delivered_ok: bool,
) -> None:
    import analyzer.analyzer as analyzer_module

    path = str(tmp_path / "db.sqlite")
    init_db(path)
    conn = get_db(path)
    conn.execute("INSERT INTO sources (id, type, name) VALUES (1, 'rss', 'feed')")
    conn.execute("INSERT INTO messages (id, source_id, external_id, text) VALUES (1, 1, '1', 'Article')")
    conn.execute("INSERT INTO analysis (message_id, value_score) VALUES (1, 8)")
    conn.commit()
    conn.close()
    candidate = row(1, 8, "tutorial", "2026-10-01")
    analyzer = Mock(db_path=path, llm=AsyncMock())
    ctx = DigestContext(analyzer=cast(NewsAnalyzer, analyzer),
                        cfg={"knowledge": {"enabled": True, "repo": "owner/repo"}},
                        rules={}, template_name="ai_value", template_cfg={
                            "candidates_list": {"enabled": True, "min_score": 6}, "min_value_score": 7},
                        digest_max=7, min_temp=5, since=datetime.now(timezone.utc),
                        force=False, return_raw=False)
    ctx.artifacts.update({"pool": [candidate], "source_map": {"1": candidate["url"]},
                          "digest_name": "articles"})
    await candidates([candidate], ctx)
    files = ctx.artifacts["extra_files"]
    assert len(files) == 1 and files[0][0].startswith("knowledge/candidates/")
    conn = get_db(path)
    assert conn.execute("SELECT in_digest FROM messages WHERE id=1").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM digest_messages").fetchone()[0] == 0
    assert conn.execute("SELECT md_path FROM analysis WHERE message_id=1").fetchone()[0] is None
    conn.close()

    async def fake_publish(*args: Any, **kwargs: Any) -> dict[str, str]:
        if delivered_ok:
            kwargs["delivered"].append(files[0][0])
        return {}

    monkeypatch.setattr(analyzer_module, "publish_selected", fake_publish)
    await knowledge([candidate], ctx)
    assert ("candidates_link" in ctx.artifacts) is delivered_ok


def test_renderer_adds_only_optional_last_block() -> None:
    data = {"items": [{"title": "Article", "takeaway": "Why", "summary": "What", "source_id": "1"}]}
    plain, mode = render_digest(data, "ai_value", {})
    linked, linked_mode = render_digest(data, "ai_value", {}, candidates_link={
        "url": 'https://example.org/a?x=1&y="2"', "selected": 7, "total": 87})
    assert mode == linked_mode == "HTML"
    assert "Все кандидаты выпуска" not in plain
    assert linked.startswith(plain)
    assert linked.endswith('📋 <a href="https://example.org/a?x=1&amp;y=&quot;2&quot;">'
                          'Все кандидаты выпуска</a>: выбрано 7 из 87')


def test_config_has_flags_in_both_locations() -> None:
    import json

    settings = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
    templates = DEFAULT_CONFIG["digest_templates"]
    assert isinstance(templates, dict)
    defaults = templates["ai_value"]
    active = settings["digest_templates"]["ai_value"]
    assert defaults["tie_break"] == "temperature"
    assert defaults["candidates_list"] == {"enabled": False, "min_score": 6}
    assert active["tie_break"] == "oldest"
    assert active["candidates_list"] == {"enabled": True, "min_score": 6}
    assert active["quotas"]["hype"] == 0
    assert settings["categories"]["articles"]["extras"] == ["candidates", "knowledge"]
