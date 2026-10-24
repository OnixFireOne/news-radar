"""Additional media slots preserve the main quota selection and publishing paths."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from analyzer.pipeline.extras import build_candidates_markdown
from analyzer.site_digest import build_digest_post, build_telegram_announce
from analyzer.value_funnel import explain_selection, select_with_quotas


def config() -> dict[str, Any]:
    settings = json.loads(Path("config/settings.json").read_text())
    return dict(settings["digest_templates"]["ai_value"])


def row(index: int, text: str = "Agent workflow", score: int = 9,
        kind: str = "tutorial") -> dict[str, Any]:
    return {"id": index, "text": text, "value_score": score, "temperature": 8,
            "content_type": kind, "summary": "Useful result", "takeaway": "Try this",
            "url": f"https://example.org/{index}", "collected_at": "2026-10-24T00:00:00"}


def pool() -> list[dict[str, Any]]:
    return [row(i) for i in range(7)] + [row(i, "Video generation", 7) for i in range(7, 11)]


@pytest.mark.parametrize("bonus", [None, {}, {"enabled": False, "patterns": ["video"]},
                                    "invalid", {"enabled": True, "patterns": "video"},
                                    {"enabled": True, "patterns": [1]},
                                    {"enabled": True, "patterns": ["["]}])
def test_disabled_or_invalid(bonus: object) -> None:
    cfg = config()
    cfg.pop("media_bonus")
    expected = select_with_quotas(pool(), cfg)
    assert select_with_quotas(pool(), {**cfg, "media_bonus": bonus}) == expected


def test_three_additional_articles_and_explanation() -> None:
    cfg = config()
    candidates = pool()
    selected = select_with_quotas(candidates, cfg)
    assert [item["id"] for item in selected] == list(range(10))
    statuses = explain_selection(candidates, cfg, [item["id"] for item in selected],
                                 datetime.now(timezone.utc))
    assert {item.row["id"] for item in statuses if item.status == "selected"} == set(range(10))
    assert next(item.status for item in statuses if item.row["id"] == 10) == "not_fitted"


def test_main_media_does_not_spend_bonus() -> None:
    candidates = pool()
    candidates[0]["text"] = "Sora video"
    assert len(select_with_quotas(candidates, config())) == 10


def test_score_and_content_type_gates() -> None:
    cfg = config()
    cfg["media_bonus"]["min_score"] = 8
    assert len(select_with_quotas(pool(), cfg)) == 7
    candidates = pool()[:7] + [row(7, "video", 7, "hype_news"), row(8, "video", 7, "crypto"),
                               row(9, "video", 6)]
    assert len(select_with_quotas(candidates, config())) == 7


@pytest.mark.parametrize("text", ["Новая видеокарта", "Видеопамять GPU", "Видеозвонок",
                                  "Видеоконференция", "x" * 300 + " video generation"])
def test_non_media_and_body_mentions(text: str) -> None:
    assert len(select_with_quotas(pool()[:7] + [row(7, text, 7)], config())) == 7


@pytest.mark.parametrize("field", ["text", "summary", "takeaway"])
def test_matching_fields(field: str) -> None:
    candidate = row(7, score=7)
    candidate[field] = "Генерация изображений"
    assert len(select_with_quotas(pool()[:7] + [candidate], config())) == 8


def test_ten_articles_reach_site_cards_and_candidates() -> None:
    cfg = config()
    selected = select_with_quotas(pool(), cfg)
    draft = {"items": [{"source_id": str(i), "title": f"Article {i}",
                         "takeaway": "Try this", "summary": "Useful result"}
                        for i in range(1, 11)],
             "picks": [{"source_id": str(i), "hook": "Read this"} for i in range(1, 4)]}
    now = datetime.now(timezone.utc)
    _, content, url = build_digest_post(draft, selected, pool(), {}, cfg, {}, now)
    assert content.count('<article class="radar-card">') == 10
    reviews = {str(i): f"https://example.org/reviews/{i}/" for i in range(1, 11)}
    announce = build_telegram_announce(draft, selected, url, cfg, now, reviews)
    assert len(announce) <= 3500
    for i in range(1, 11):
        assert f'Article {i}<' in announce
    markdown, _ = build_candidates_markdown(pool(), selected, cfg, "articles", now)
    assert "Выбрано 10" in markdown
