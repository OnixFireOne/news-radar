"""Digest candidate selectors."""

from __future__ import annotations

from analyzer.pipeline.context import DigestContext, Row
from analyzer.pipeline.registry import SELECTORS
from analyzer.value_funnel import select_with_quotas


@SELECTORS.register("tiers")
def tiers(rows: list[Row], ctx: DigestContext) -> list[Row]:
    alerts: list[Row] = []
    trends_tier: list[Row] = []
    high_tier: list[Row] = []
    fill_tier: list[Row] = []
    selected: list[Row] = []
    include_alerts = ctx.rules.get("always_include_alerts", True)
    max_per_topic = ctx.rules.get("max_per_topic", 2)
    for row in rows:
        topic = row["topic"] or "general"
        temp = float(row["temperature"] or 5.0)
        is_alert = ctx.analyzer._is_alert_topic(topic) or temp >= 9.0 or bool(row.get("was_alerted"))
        if is_alert and include_alerts:
            alerts.append(row)
        elif row["in_hot_trend"]:
            trends_tier.append(row)
        elif temp >= ctx.min_temp + 2:
            high_tier.append(row)
        elif temp >= ctx.min_temp:
            fill_tier.append(row)

    topic_counts: dict[str, int] = {}

    def try_add(item: Row, force: bool = False) -> bool:
        topic = item["topic"] or "general"
        count = topic_counts.get(topic, 0)
        if force or count < max_per_topic:
            selected.append(item)
            topic_counts[topic] = count + 1
            return True
        return False

    oversample_multiplier = 1
    for item in alerts:
        if len(selected) >= ctx.digest_max * oversample_multiplier:
            break
        try_add(item, force=True)
    for item in trends_tier + high_tier:
        if len(selected) >= ctx.digest_max * oversample_multiplier:
            break
        try_add(item)
    seen_topics = set(topic_counts.keys())
    for item in fill_tier:
        if len(selected) >= ctx.digest_max * oversample_multiplier:
            break
        topic = item["topic"] or "general"
        if topic not in seen_topics:
            try_add(item)
            seen_topics.add(topic)
    ctx.artifacts["tier_counts"] = (len(alerts), len(trends_tier), len(high_tier))
    return selected


@SELECTORS.register("quotas")
def quotas(rows: list[Row], ctx: DigestContext) -> list[Row]:
    ctx.artifacts["tier_counts"] = (0, 0, 0)
    return select_with_quotas(rows, ctx.template_cfg)
