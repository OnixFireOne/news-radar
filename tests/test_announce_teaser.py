"""Teaser announcement: lead + model picks + the rest of the issue, behind announce_style."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from analyzer.analyzer import NewsAnalyzer
from analyzer.json_schemas import DIGEST_AI_VALUE_SCHEMA_V3
from analyzer.pipeline.context import DigestContext
from analyzer.pipeline.writers import _ai_value_compose
from analyzer.site_digest import build_telegram_announce
from config.config_watcher import DEFAULT_CONFIG

NOW = datetime(2026, 10, 12)
URL = "https://neuronavt.blog/posts/2026-10-12-ai-radar/"
TYPES = {"practical_case": {"emoji": "💡"}, "research": {"emoji": "🔬"}, "_default": {"emoji": "🔹"}}
CFG = {"announce_style": "teaser", "types": TYPES}


def data(count: int = 5) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [{"id": i, "text": "Article", "value_score": 8,
             "content_type": "research" if i == 2 else "practical_case"} for i in range(1, count + 1)]
    draft: dict[str, Any] = {
        "items": [{"source_id": str(i), "title": f"Title {i}", "takeaway": "t", "summary": "s"}
                  for i in range(1, count + 1)],
        "lead": "Main lesson of the day.", "highlights": ["Point"],
        "picks": [{"source_id": "2", "hook": "Hook <two> & more"}, {"source_id": "1", "hook": "Hook one"}],
    }
    return draft, rows


def test_teaser_layout() -> None:
    draft, rows = data()
    text = build_telegram_announce(draft, rows, URL, CFG, NOW)
    assert text.startswith("🤖 <b>AI-радар — 12 октября</b>\nMain lesson of the day.\n\n<b>Стоит открыть:</b>\n")
    assert "🔬 Hook &lt;two&gt; &amp; more\n💡 Hook one" in text
    assert "Ещё 3 статьи: Title 3 · Title 4 · Title 5" in text
    assert text.endswith(f'<a href="{URL}">Читать выпуск на сайте →</a>')
    assert "Интересное" not in text


def test_teaser_fits_limit_and_cuts_on_words() -> None:
    draft, rows = data(8)
    draft["lead"] = "Длинное слово " * 200
    draft["picks"] = [{"source_id": str(i), "hook": "<&> " * 300} for i in (1, 2, 3)]
    for item in draft["items"]:
        item["title"] = "Очень длинный заголовок статьи " * 10
    text = build_telegram_announce(draft, rows, URL, CFG, NOW)
    assert len(text) < 1000
    assert "&lt;&amp;&gt;…" in text and "&lt;&amp" not in text.replace("&lt;&amp;&gt;", "")
    assert text.endswith("</a>")


@pytest.mark.parametrize("cfg,picks", [({"types": TYPES}, True), (CFG, False)])
def test_points_style_when_disabled_or_no_picks(cfg: dict[str, Any], picks: bool) -> None:
    draft, rows = data()
    if not picks:
        draft["picks"] = [{"source_id": "99", "hook": "unknown"}, {"source_id": "1", "hook": " "}]
    text = build_telegram_announce(draft, rows, URL, cfg, NOW)
    assert "Интересное:\n• Point" in text and "Стоит открыть" not in text


@pytest.mark.asyncio
async def test_compose_v3_cleans_picks() -> None:
    draft, rows = data()
    raw = dict(draft, picks=[{"source_id": "9", "hook": "x"}, {"source_id": "1", "hook": " a "},
                             {"source_id": "1", "hook": "dup"}, {"source_id": "2", "hook": ""}, "bad",
                             {"source_id": "3", "hook": "c"}, {"source_id": "4", "hook": "d"},
                             {"source_id": "5", "hook": "e"}])
    analyzer = Mock()
    analyzer.llm = AsyncMock()
    analyzer.llm.complete_json.return_value = raw
    ctx = DigestContext(cast(NewsAnalyzer, analyzer), {}, {}, "ai_value",
                        {"digest_prompt_version": "ai_value-digest-v3"}, 8, 5, NOW, False, False)
    result = await _ai_value_compose(rows, ctx)
    kwargs = analyzer.llm.complete_json.await_args.kwargs
    assert '"picks"' in kwargs["user_prompt"] and kwargs["schema"] is DIGEST_AI_VALUE_SCHEMA_V3
    assert result["picks"] == [{"source_id": "1", "hook": "a"}, {"source_id": "3", "hook": "c"},
                               {"source_id": "4", "hook": "d"}]


def test_config_defaults_keep_old_announce() -> None:
    template = cast(dict[str, Any], cast(dict[str, Any], DEFAULT_CONFIG["digest_templates"])["ai_value"])
    assert template["announce_style"] == "points"
    assert template["digest_prompt_version"] == "ai_value-digest-v1"
    settings = json.loads(Path("config/settings.json").read_text())["digest_templates"]["ai_value"]
    assert settings["announce_style"] in ("teaser", "cards")
    assert settings["digest_prompt_version"] in ("ai_value-digest-v3", "ai_value-digest-v4")
