"""Pure conversion and quota selection for the AI value profile."""

import json
import re
from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone

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


def _date(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.max.replace(tzinfo=timezone.utc)
    else:
        return datetime.max.replace(tzinfo=timezone.utc)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def rank_candidates(candidates: Sequence[Mapping[str, object]], cfg: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Rank all pool rows, preserving input order on complete ties."""
    oldest = cfg.get("tie_break") == "oldest"
    if oldest:
        return sorted(candidates, key=lambda row: (-_rank(row)[0], _date(row.get("collected_at")),
                                                    -_rank(row)[1]))
    return sorted(candidates, key=lambda row: (-_rank(row)[0], -_rank(row)[1]))


@dataclass(frozen=True)
class CandidateStatus:
    row: Mapping[str, object]
    status: str
    last_day: bool


def explain_selection(
    candidates: Sequence[Mapping[str, object]], cfg: Mapping[str, object],
    selected_ids: Sequence[object], now: datetime,
) -> list[CandidateStatus]:
    """Explain the final selection without changing rows or database state."""
    selected = set(selected_ids)
    minimum = _number(cfg.get("min_value_score"), 5)
    crypto_min = _number(cfg.get("crypto_min_temperature"), 8)
    quotas = cfg.get("quotas", {})
    if not isinstance(quotas, Mapping):
        quotas = {}
    days = _number(cfg.get("carryover_days"), 0)
    cutoff = _date(now) - timedelta(days=days) + timedelta(days=1)
    fill_types = QUOTA_GROUPS["practical"] | QUOTA_GROUPS["tools_research"] | {"opinion"}
    results: list[CandidateStatus] = []
    for row in rank_candidates(candidates, cfg):
        score, temperature = _rank(row)
        kind = row.get("content_type")
        if row.get("id") in selected:
            status = "selected"
        elif row.get("value_score") is None or score < minimum:
            status = "below_threshold"
        elif kind == "crypto" and temperature < crypto_min and not row.get("in_hot_trend"):
            status = "type_off"
        elif kind not in fill_types and any(
            kind in types and _number(quotas.get(group), _DEFAULT_QUOTAS[group]) <= 0
            for group, types in QUOTA_GROUPS.items()
        ):
            status = "type_off"
        else:
            status = "not_fitted"
        results.append(CandidateStatus(row, status, days > 0 and _date(row.get("collected_at")) < cutoff))
    return results


def select_with_quotas(
    candidates: Sequence[Mapping[str, object]], cfg: Mapping[str, object],
) -> list[dict[str, object]]:
    quotas = cfg.get("quotas", {})
    if not isinstance(quotas, Mapping):
        quotas = {}
    minimum = _number(cfg.get("min_value_score"), 5)
    crypto_min = _number(cfg.get("crypto_min_temperature"), 8)
    maximum = max(0, int(_number(cfg.get("max_items"), 8)))
    order = sorted(range(len(candidates)), key=lambda i: (
        -_rank(candidates[i])[0],
        _date(candidates[i].get("collected_at")) if cfg.get("tie_break") == "oldest" else datetime.min.replace(tzinfo=timezone.utc),
        -_rank(candidates[i])[1],
    ))
    ranked = [i for i in order
              if candidates[i].get("value_score") is not None and _rank(candidates[i])[0] >= minimum
              and (candidates[i].get("content_type") != "crypto"
                   or _rank(candidates[i])[1] >= crypto_min or candidates[i].get("in_hot_trend"))]
    fill_types = QUOTA_GROUPS["practical"] | QUOTA_GROUPS["tools_research"] | {"opinion"}
    # Soft quotas: a fill group reserves slots only for strong rows; weaker ones compete by rank.
    # Hype and crypto pass only through their reservation, so they keep the plain minimum.
    reserve_min = _number(cfg.get("quota_min_score"), minimum)
    selected: set[int] = set()
    for group, types in QUOTA_GROUPS.items():
        limit = max(0, int(_number(quotas.get(group), _DEFAULT_QUOTAS[group])))
        floor = reserve_min if types <= fill_types else minimum
        members = [i for i in ranked if candidates[i].get("content_type") in types
                   and _rank(candidates[i])[0] >= floor]
        selected.update(members[:limit])
    for i in ranked:
        if len(selected) >= maximum:
            break
        if candidates[i].get("content_type") in fill_types:
            selected.add(i)
    # Preserve the original main selection, including truncation of oversized reservations.
    main = [i for i in ranked if i in selected][:maximum]
    selected = set(main)
    bonus = cfg.get("media_bonus")
    if isinstance(bonus, dict) and bonus.get("enabled") is True:
        patterns = bonus.get("patterns")
        if isinstance(patterns, list) and all(isinstance(pattern, str) for pattern in patterns):
            try:
                compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
            except re.error:
                compiled = []
            limit = max(0, int(_number(bonus.get("max_items"), 3)))
            floor = _number(bonus.get("min_score"), minimum)
            added = 0
            for i in ranked:
                if added >= limit:
                    break
                row = candidates[i]
                if i in selected or row.get("content_type") not in fill_types or _rank(row)[0] < floor:
                    continue
                text = "\n".join((str(row.get("text") or "")[:300],
                                   str(row.get("takeaway") or ""), str(row.get("summary") or "")))
                if any(pattern.search(text) for pattern in compiled):
                    selected.add(i)
                    added += 1
    # Filtering the globally ranked indices preserves input order for tied scores.
    return [dict(candidates[i]) for i in ranked if i in selected]
