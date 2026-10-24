"""Cards announcement: linked picks with hooks and a collapsed linked list of the rest."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from analyzer.site_digest import CARDS_LIMIT, build_telegram_announce
from config.config_watcher import DEFAULT_CONFIG

NOW = datetime(2026, 10, 22)
URL = "https://neuronavt.blog/posts/2026-10-22-ai-radar/"
TYPES = {"practical_case": {"emoji": "💡"}, "research": {"emoji": "🔬"}, "_default": {"emoji": "🔹"}}
CFG = {"announce_style": "cards", "types": TYPES}
REVIEWS = {str(i): f"https://neuronavt.blog/reviews/r{i}/" for i in range(1, 9)}


def data(count: int = 5) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [{"id": i, "text": "Article", "value_score": 8,
             "content_type": "research" if i == 2 else "practical_case"} for i in range(1, count + 1)]
    draft: dict[str, Any] = {
        "items": [{"source_id": str(i), "title": f"Title {i}", "takeaway": "t", "summary": "s"}
                  for i in range(1, count + 1)],
        "lead": "Main <news> of the day.", "highlights": ["Point"],
        "picks": [{"source_id": "2", "hook": "Hook <two> & more"}, {"source_id": "1", "hook": "Hook one"}],
    }
    return draft, rows


def test_cards_layout_links_every_article() -> None:
    draft, rows = data()
    text = build_telegram_announce(draft, rows, URL, CFG, NOW, REVIEWS)
    assert text.startswith("🤖 <b>AI-радар — 22 октября</b>\n<i>Main &lt;news&gt; of the day.</i>\n\n"
                           "<b>Стоит открыть</b>\n\n")
    assert ('🔬 <b><a href="https://neuronavt.blog/reviews/r2/">Title 2</a></b>\nHook &lt;two&gt; &amp; more\n\n'
            '💡 <b><a href="https://neuronavt.blog/reviews/r1/">Title 1</a></b>\nHook one') in text
    assert ('<blockquote expandable><b>Ещё 3 статьи в выпуске</b>\n'
            '💡 <a href="https://neuronavt.blog/reviews/r3/">Title 3</a>\n') in text
    assert "Title 5</a></blockquote>" in text
    assert text.endswith(f'<a href="{URL}">Читать выпуск на сайте →</a>')
    assert "…" not in text


def test_cards_without_review_url_or_unsafe_url_show_plain_title() -> None:
    draft, rows = data()
    text = build_telegram_announce(draft, rows, URL, CFG, NOW, {"1": "javascript:alert(1)"})
    assert "🔬 <b>Title 2</b>" in text and "💡 <b>Title 1</b>" in text and "javascript" not in text
    assert "💡 Title 3" in text


def test_cards_stay_below_limit_with_long_texts() -> None:
    draft, rows = data(30)
    draft["lead"] = "Длинное слово " * 200
    draft["picks"] = [{"source_id": str(i), "hook": "<&> " * 300} for i in (1, 2, 3)]
    for item in draft["items"]:
        item["title"] = "Очень длинный заголовок статьи " * 10
    text = build_telegram_announce(draft, rows, URL, CFG, NOW, REVIEWS)
    assert len(text) < CARDS_LIMIT
    assert "Ещё 27 статей в выпуске" in text and text.count("<blockquote") == 1
    assert text.endswith("</a>")


def test_cards_fall_back_to_points_without_picks() -> None:
    draft, rows = data()
    draft["picks"] = []
    text = build_telegram_announce(draft, rows, URL, CFG, NOW, REVIEWS)
    assert "Интересное:\n• Point" in text and "blockquote" not in text


def test_cards_not_the_default() -> None:
    template = cast(dict[str, Any], cast(dict[str, Any], DEFAULT_CONFIG["digest_templates"])["ai_value"])
    assert template["announce_style"] == "points"
    settings = json.loads(Path("config/settings.json").read_text())["digest_templates"]["ai_value"]
    assert settings["announce_style"] in ("teaser", "cards")
