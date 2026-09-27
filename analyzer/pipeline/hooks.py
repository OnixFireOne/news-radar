"""Post-store alert hooks."""

from __future__ import annotations

import asyncio
from analyzer.pipeline.context import AnalyzeContext, Row
from analyzer.pipeline.registry import HOOKS


@HOOKS.register("alerts")
def alerts(row: Row, result: Row, ctx: AnalyzeContext) -> None:
    analyzer = ctx.analyzer
    temp = float(result.get("temperature", 5.0))
    min_alert_temp = float(analyzer.cfg.get("breaking_alert_min_temp", 10) if analyzer.cfg else 10)
    if temp >= min_alert_temp and analyzer.cfg and analyzer.cfg.get("instant_alerts_temperature", True):
        asyncio.create_task(analyzer._send_instant_alert(
            row["id"], row["source_name"], temp, result.get("topic", "general"),
            result.get("summary", ""), row["text"],
        ))


@HOOKS.register("subscriptions")
def subscriptions(row: Row, result: Row, ctx: AnalyzeContext) -> None:
    if ctx.subs_list:
        text_lower = row["text"].lower()
        summary_lower = result.get("summary", "").lower()
        for sub in ctx.subs_list:
            if sub["q_lower"] in text_lower or sub["q_lower"] in summary_lower:
                asyncio.create_task(ctx.analyzer._route_event("subscription_match", {
                    "user_id": sub["user_id"], "query": sub["query"],
                    "summary": result.get("summary", ""), "source": row["source_name"],
                    "text": row["text"][:300],
                }))


@HOOKS.register("trends")
def trends(row: Row, result: Row, ctx: AnalyzeContext) -> None:
    """Opt into the separately scheduled trend cycle; no per-message action."""
