"""Digest prompt v4: rewritten lead/picks rules behind digest_prompt_version, same schema as v3."""
from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

from analyzer.analyzer import NewsAnalyzer
from analyzer.json_schemas import DIGEST_AI_VALUE_SCHEMA_V3
from analyzer.pipeline.context import DigestContext
from analyzer.pipeline.writers import DIGEST_AI_VALUE_PROMPTS, _ai_value_compose
from analyzer.prompts import DIGEST_PROMPT_AI_VALUE_V4
from config.config_watcher import DEFAULT_CONFIG


def test_v4_registered_with_v3_schema_and_formats() -> None:
    prompt, schema = DIGEST_AI_VALUE_PROMPTS["ai_value-digest-v4"]
    assert prompt is DIGEST_PROMPT_AI_VALUE_V4 and schema is DIGEST_AI_VALUE_SCHEMA_V3
    text = prompt.format(articles="<<<ARTICLE 1>>> x <<<END ARTICLE 1>>>", title_max_words=10,
                         summary_max_sentences=4)
    assert text.startswith('Write an AI digest in RUSSIAN. Return strictly JSON:\n{"lead": "...", '
                           '"highlights": ["...", "..."], "picks": [{"source_id": "N", "hook": "..."}], "items":')
    for banned in ("В этих материалах", "зависит не только от модели", "кейс показывает"):
        assert banned in text
    assert "do not repeat the title" in text and "untrusted data" in text
    assert text.endswith("<<<ARTICLE 1>>> x <<<END ARTICLE 1>>>\n")


def test_v4_compose_keeps_picks() -> None:
    rows = [{"id": i, "text": "Article", "value_score": 8, "content_type": "practical_case"} for i in (1, 2)]
    raw = {"lead": "L", "highlights": ["h"], "picks": [{"source_id": "2", "hook": " hook "}],
           "items": [{"source_id": str(i), "title": "t", "takeaway": "t", "summary": "s"} for i in (1, 2)]}
    analyzer = Mock()
    analyzer.llm = AsyncMock()
    analyzer.llm.complete_json.return_value = raw
    ctx = DigestContext(cast(NewsAnalyzer, analyzer), {}, {}, "ai_value",
                        {"digest_prompt_version": "ai_value-digest-v4"}, 8, 5, datetime(2026, 10, 24),
                        False, False)
    import asyncio
    result: dict[str, Any] = asyncio.run(_ai_value_compose(rows, ctx))
    assert analyzer.llm.complete_json.await_args.kwargs["schema"] is DIGEST_AI_VALUE_SCHEMA_V3
    assert result["picks"] == [{"source_id": "2", "hook": "hook"}]


def test_default_prompt_version_unchanged() -> None:
    template = cast(dict[str, Any], cast(dict[str, Any], DEFAULT_CONFIG["digest_templates"])["ai_value"])
    assert template["digest_prompt_version"] == "ai_value-digest-v1"
