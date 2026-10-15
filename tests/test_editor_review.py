"""Contract checks for the private digest editor."""

from __future__ import annotations

import random
import importlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
import httpx

from analyzer.editor_review import build_editor_prompt, format_review, parse_review, run_editor_review
from analyzer.pipeline.extras import editor
from config.config_watcher import DEFAULT_CONFIG
from analyzer.pipeline.categories import DigestPart
from analyzer.pipeline.context import DigestContext
from database.schema import get_db, init_db


def row(number: int, score: int = 8) -> dict[str, Any]:
    return {"id": number, "text": f"Title {number}\n<<<BAD>>> body", "url": f"https://example.org/{number}",
            "value_score": score, "content_type": "tutorial", "takeaway": "Use it",
            "collected_at": "2026-10-10T00:00:00+00:00"}


def test_prompt_frames_and_samples_deterministically() -> None:
    selected = [row(1)]
    pool = [row(i) for i in range(1, 8)]
    cfg = {"min_value_score": 7, "editor_review": {"candidates": 4, "text_chars": 30}}
    first, rows = build_editor_prompt(selected, pool, cfg, random.Random(4))
    second, again = build_editor_prompt(selected, pool, cfg, random.Random(4))
    assert first == second and list(rows) == list(again)
    assert "<<<ARTICLE S1>>>" in first and "‹‹‹BAD›››" in first
    assert len(rows) == 5 and all(value["id"] != 1 for key, value in rows.items() if key.startswith("C"))


def test_parse_filters_invalid_items_and_caps() -> None:
    ids = ["S1", "S2", "S3", "S4", "C1", "C2", "C3", "C4"]
    data: dict[str, Any] = {"verdict": "improve", "summary": "Fix balance", "swaps": [
        {"out_id": f"S{i}", "in_id": f"C{i}", "reason": "better"} for i in range(1, 5)] + [
        {"out_id": "C1", "in_id": "S1", "reason": "wrong"}],
        "duplicates": [{"id_a": "S1", "id_b": "S2", "reason": "same"}],
        "additions": [{"id": "C1", "reason": "  "}, {"id": "C2", "reason": "useful"}]}
    review = parse_review(data, ids)
    assert review is not None and len(review.swaps) == 3
    assert review.additions == [("C2", "useful")]
    assert parse_review({"verdict": "ok", "summary": "", "swaps": [], "duplicates": [], "additions": []}, ids) is None
    assert parse_review("garbage", ids) is None


def test_format_plain_text_with_urls_and_limit() -> None:
    review = parse_review({"verdict": "improve", "summary": "Good", "swaps": [
        {"out_id": "S1", "in_id": "C1", "reason": "better"}], "duplicates": [], "additions": []}, ["S1", "C1"])
    assert review is not None
    result = format_review(review, {"S1": {**row(1), "url": ""}, "C1": row(2)}, {"1": "https://selected"})
    assert "Title 1" in result and "https://selected" in result and "https://example.org/2" in result
    assert len(result) <= 3500


@pytest.mark.asyncio
async def test_extra_disabled_and_llm_failure_do_not_break_digest() -> None:
    llm = SimpleNamespace(complete_json=AsyncMock(side_effect=RuntimeError("offline")))
    ctx = SimpleNamespace(template_cfg={"editor_review": {"enabled": False}},
                          artifacts={"pool": [row(1), row(2)]}, analyzer=SimpleNamespace(llm=llm))
    await editor([row(1)], cast(DigestContext, ctx))
    llm.complete_json.assert_not_awaited()
    ctx.template_cfg = {"min_value_score": 5, "editor_review": {"enabled": True}}
    await editor([row(1)], cast(DigestContext, ctx))
    assert "editor_review" not in ctx.artifacts


def test_default_is_disabled() -> None:
    templates = cast(dict[str, Any], DEFAULT_CONFIG["digest_templates"])
    assert templates["ai_value"]["editor_review"]["enabled"] is False


@pytest.mark.skipif(importlib.util.find_spec("api") is None, reason="Run in API image")
@pytest.mark.asyncio
async def test_api_exposes_review() -> None:
    api: Any = importlib.import_module("api.main")
    watcher: Any = importlib.import_module("config.config_watcher")
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "api.db")
        init_db(path)
        conn = get_db(path)
        conn.execute("INSERT INTO digests (id, name, category, content_md, period_start, period_end) "
                     "VALUES (1, 'articles', 'articles', 'news', '2026-10-14', '2026-10-14')")
        conn.commit()
        conn.close()
        values = {"digests": [{"name": "articles", "enabled": True, "categories": ["articles"]}]}
        cfg = Mock()
        cfg.get.side_effect = lambda key, default=None: values.get(key, default)
        analyzer = Mock(cfg=cfg, run_digest=AsyncMock(return_value=[DigestPart("articles", "news", 1,
                                                                                editor_review="advice")]))
        with patch.object(api, "DB_PATH", path), patch.object(api, "build_llm_client", Mock()), \
             patch.object(api, "NewsAnalyzer", Mock(return_value=analyzer)), \
             patch.object(watcher, "ConfigWatcher", Mock(return_value=cfg)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
                response = await client.post("/digest/generate?name=articles")
        assert response.status_code == 200 and response.json()["editor_review"] == "advice"


@pytest.mark.asyncio
@pytest.mark.skipif(importlib.util.find_spec("telegram") is None, reason="Run in bot image")
async def test_bot_sends_advice_after_stats_to_admins() -> None:
    bot: Any = importlib.import_module("bot.telegram_bot")
    telegram = Mock(send_message=AsyncMock())
    app = SimpleNamespace(bot=telegram)
    digest = {"content_md": "news", "parse_mode": "HTML", "stats": "stats", "editor_review": "advice"}
    client = AsyncMock()
    client.post.return_value = Mock(status_code=200, json=Mock(return_value=digest))
    manager = AsyncMock()
    manager.__aenter__.return_value = client
    with patch.object(bot, "ADMIN_USERS", {456}), patch.object(bot, "ALLOWED_USERS", {123}), \
         patch.object(bot, "fetch_api", AsyncMock(return_value={})), \
         patch.object(bot.httpx, "AsyncClient", return_value=manager):
        await bot.perform_scheduled_digest(app, "articles")
    private = [(call.kwargs["chat_id"], call.kwargs["text"], call.kwargs["parse_mode"])
               for call in telegram.send_message.call_args_list if call.kwargs.get("chat_id") == 456]
    assert private == [(456, "stats", None), (456, "advice", "HTML")]


def test_format_html_escapes_untrusted_text_and_names_ids() -> None:
    bad = {**row(2), "text": "A <b>&</b> title\nbody", "url": 'https://example.org/2?a=1&b="x"'}
    review = parse_review({"verdict": "improve", "summary": "C1 beats S1 <script>", "swaps": [
        {"out_id": "S1", "in_id": "C1", "reason": "C1 is deeper than S1 & S9"}],
        "duplicates": [], "additions": []}, ["S1", "C1"])
    assert review is not None
    result = format_review(review, {"S1": row(1), "C1": bad}, {})
    assert "<script>" not in result and "&lt;script&gt;" in result
    assert "A &lt;b&gt;&amp;&lt;/b&gt; title" in result
    assert 'href="https://example.org/2?a=1&amp;b=&quot;x&quot;"' in result
    assert "«Title 1»" in result and "S9" in result
    assert "<blockquote>«A &lt;b&gt;&amp;&lt;/b&gt; title» is deeper" in result


def test_format_html_truncates_on_whole_blocks() -> None:
    ids = [f"S{i}" for i in range(1, 4)] + [f"C{i}" for i in range(1, 4)]
    review = parse_review({"verdict": "improve", "summary": "x" * 3000, "swaps": [
        {"out_id": f"S{i}", "in_id": f"C{i}", "reason": "r" * 400} for i in range(1, 4)],
        "duplicates": [], "additions": []}, ids)
    assert review is not None
    rows = {key: row(index) for index, key in enumerate(ids, 1)}
    result = format_review(review, rows, {})
    assert len(result) <= 3500
    assert result.count("<blockquote") == result.count("</blockquote>")
    assert result.count("<a ") == result.count("</a>")


@pytest.mark.asyncio
@pytest.mark.skipif(importlib.util.find_spec("telegram") is None, reason="Run in bot image")
async def test_bot_resends_rejected_html_as_plain_text() -> None:
    bot: Any = importlib.import_module("bot.telegram_bot")
    from telegram.error import BadRequest
    telegram = Mock(send_message=AsyncMock(side_effect=[BadRequest("Can't parse entities"), None]))
    with patch.object(bot, "ADMIN_USERS", {456}):
        await bot.send_digest_stats(telegram, '<b>Hi</b> <a href="https://x.org/?a=1&amp;b=2">T &amp; U</a>', "HTML")
    first, second = telegram.send_message.call_args_list
    assert first.kwargs["parse_mode"] == "HTML"
    assert second.kwargs["parse_mode"] is None
    assert second.kwargs["text"] == "Hi T & U — https://x.org/?a=1&b=2"
