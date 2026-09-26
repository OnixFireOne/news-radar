"""Pure conversion and quota selection for the AI value profile."""

import json
from collections.abc import Mapping, Sequence

from analyzer.value_classifier import ValueVerdict

QUOTA_GROUPS = {
    "practical": {"practical_case", "tutorial"},
    "tools_research": {"tool_release", "research"},
    "hype": {"hype_news"},
    "crypto": {"crypto"},
}
_DEFAULT_QUOTAS = {"practical": 5, "tools_research": 2, "hype": 1, "crypto": 0}


def verdict_to_row(v: ValueVerdict) -> dict[str, object]:
    return {
        "temperature": v.temperature, "topic": v.topic, "summary": v.summary,
        "keywords": json.dumps(v.keywords, ensure_ascii=False),
        "content_type": v.content_type, "value_score": v.value_score,
        "has_outcome": int(v.has_outcome), "takeaway": v.takeaway,
    }


def _number(value: object, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _rank(row: Mapping[str, object]) -> tuple[float, float]:
    return (_number(row.get("value_score"), 0), _number(row.get("temperature"), 0))


def select_with_quotas(
    candidates: Sequence[Mapping[str, object]], cfg: Mapping[str, object],
) -> list[dict[str, object]]:
    quotas = cfg.get("quotas", {})
    if not isinstance(quotas, Mapping):
        quotas = {}
    minimum = _number(cfg.get("min_value_score"), 5)
    crypto_min = _number(cfg.get("crypto_min_temperature"), 8)
    maximum = max(0, int(_number(cfg.get("max_items"), 8)))
    ranked = sorted(
        (i for i, row in enumerate(candidates)
         if row.get("value_score") is not None and _rank(row)[0] >= minimum
         and (row.get("content_type") != "crypto"
              or _rank(row)[1] >= crypto_min or row.get("in_hot_trend"))),
        key=lambda i: _rank(candidates[i]), reverse=True,
    )
    selected: set[int] = set()
    for group, types in QUOTA_GROUPS.items():
        limit = max(0, int(_number(quotas.get(group), _DEFAULT_QUOTAS[group])))
        members = [i for i in ranked if candidates[i].get("content_type") in types]
        selected.update(members[:limit])
    fill_types = QUOTA_GROUPS["practical"] | QUOTA_GROUPS["tools_research"] | {"opinion"}
    for i in ranked:
        if len(selected) >= maximum:
            break
        if candidates[i].get("content_type") in fill_types:
            selected.add(i)
    # Filtering the globally ranked indices preserves input order for tied scores.
    return [dict(candidates[i]) for i in ranked if i in selected][:maximum]
