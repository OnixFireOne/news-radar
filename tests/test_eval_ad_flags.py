"""Ad-label parsing and scoring for the is_ad eval."""
from __future__ import annotations

from tests.eval_ad_flags import parse_ad_labels, score


def test_parse_reads_only_filled_labels() -> None:
    text = ("## a01 · X · d\n\n- реклама: да\n- коммент: \n\n## a02 · Y · d\n\n- реклама: нет\n\n"
            "## a03 · Z · d\n\n- реклама: пропуск\n\n## a04 · W · d\n\n- реклама: \n")
    assert parse_ad_labels(text) == {"a01": True, "a02": False}


def test_score_counts_false_and_missed_ads() -> None:
    labels = {"a1": True, "a2": True, "a3": True, "a4": True, "b1": False, "b2": False, "b3": False}
    flags: dict[str, bool | None] = {"a1": True, "a2": True, "a3": True, "a4": False,
                                     "b1": True, "b2": False, "b3": False}
    result = score(labels, flags)
    assert result["false_ad"] == ["b1"] and result["missed_ad"] == ["a4"]
    assert result["pass"] is False  # 1 of 3 clean flagged = 33% > 15%
    flags["b1"] = False
    assert score(labels, flags)["pass"] is True  # 1 of 4 ads missed = 25%
    flags["b1"] = None
    assert score(labels, flags)["broken"] == ["b1"] and score(labels, flags)["pass"] is False
