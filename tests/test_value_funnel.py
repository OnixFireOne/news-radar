"""Quota selection preserves reserved groups and fills unused capacity."""

import json

from analyzer.value_classifier import ValueVerdict
from analyzer.value_funnel import select_with_quotas, verdict_to_row


def candidate(kind, score=7, temperature=5, **extra):
    return dict(content_type=kind, value_score=score, temperature=temperature, **extra)


def test_default_group_allocations():
    rows = ([candidate("tutorial", id=f"p{i}") for i in range(7)]
            + [candidate("research", id=f"r{i}") for i in range(4)]
            + [candidate("hype_news", id=f"h{i}") for i in range(3)])
    selected = select_with_quotas(rows, {})
    assert [r["id"] for r in selected] == ["p0", "p1", "p2", "p3", "p4", "r0", "r1", "h0"]


def test_hype_never_fills_extra_slots_and_crypto_zero_never_passes():
    rows = [candidate("hype_news", 10), candidate("hype_news", 9),
            candidate("crypto", 10, 10, in_hot_trend=True)]
    assert select_with_quotas(rows, {}) == rows[:1]


def test_crypto_gate_and_quota():
    rows = [candidate("crypto", 10, 7), candidate("crypto", 9, 7, in_hot_trend=True),
            candidate("crypto", 8, 8), candidate("crypto", 7, 10)]
    assert select_with_quotas(rows, {"quotas": {"crypto": 1}}) == [rows[1]]
    assert select_with_quotas(rows[2:], {"quotas": {"crypto": 1}}) == [rows[2]]
    assert select_with_quotas(rows[:1], {"quotas": {"crypto": 1}}) == []


def test_minimum_and_missing_scores():
    rows = [candidate("tutorial", None), candidate("tutorial", 4), candidate("tutorial", 5)]
    assert select_with_quotas(rows, {}) == [rows[2]]
    assert select_with_quotas(rows, {"min_value_score": 6}) == []


def test_opinion_only_uses_free_capacity():
    rows = [candidate("tutorial", 5, id=i) for i in range(5)]
    opinion = candidate("opinion", 10)
    assert select_with_quotas([opinion, *rows], {"max_items": 5}) == rows
    assert select_with_quotas([opinion, *rows], {}) == [opinion, *rows]


def test_practical_and_tools_can_fill_beyond_reservations():
    for kind in ("tutorial", "practical_case", "tool_release", "research", "opinion"):
        rows = [candidate(kind, id=i) for i in range(10)]
        assert select_with_quotas(rows, {}) == rows[:8]


def test_maximum_order_and_stable_ties():
    rows = [candidate("tutorial", 7, 9, id="a"), candidate("research", 9, 3, id="b"),
            candidate("tutorial", 7, 10, id="c"), candidate("research", 7, 10, id="d")]
    assert [r["id"] for r in select_with_quotas(rows, {"max_items": 3})] == ["b", "c", "d"]
    assert select_with_quotas(rows, {"max_items": 0}) == []


def test_partial_config_uses_defaults_and_does_not_mutate():
    rows = [candidate("hype_news", 10), candidate("hype_news", 9), candidate("crypto", 10, 10)]
    cfg = {"quotas": {"practical": 1}}
    selected = select_with_quotas(rows, cfg)
    assert selected == rows[:1]
    selected[0]["value_score"] = 1
    assert rows[0]["value_score"] == 10
    assert cfg == {"quotas": {"practical": 1}}


def test_verdict_row_mapping():
    v = ValueVerdict(8, "tutorial", 9, True, "Вывод", "agents", "Кратко", ["агенты"], True)
    assert verdict_to_row(v) == dict(temperature=8, content_type="tutorial", value_score=9,
                                    has_outcome=1, takeaway="Вывод", topic="agents", summary="Кратко",
                                    keywords=json.dumps(["агенты"], ensure_ascii=False))
    assert verdict_to_row(ValueVerdict(1, "opinion", 1, False, "", "other", "", [], False))["has_outcome"] == 0
