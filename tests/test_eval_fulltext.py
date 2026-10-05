"""Candidate file selection for the golden-set evaluator."""

import json
from pathlib import Path

from tests.eval_value_scoring import load_items


def test_load_items_uses_custom_candidates_and_appends_synthetic(tmp_path: Path) -> None:
    candidates = tmp_path / "custom.jsonl"
    candidates.write_text(json.dumps({"gid": "g01", "source": "custom", "text": "Full article"}) + "\n",
                          encoding="utf-8")
    items, labels = load_items(candidates=candidates)
    assert items[0].id == "g01"
    assert items[0].text == "Full article"
    assert items[0].source == "custom"
    assert [item.id for item in items] == ["g01", "s01", "s02"]
    assert set(labels) == {"g01", "s01", "s02"}
    limited, _ = load_items(limit=1, candidates=candidates)
    assert limited == items[:1]
