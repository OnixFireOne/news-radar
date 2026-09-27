"""Message analysis bricks."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from analyzer.pipeline.context import AnalysisResult, AnalyzeContext, Row
from analyzer.pipeline.registry import ANALYZERS
from analyzer.value_classifier import ValueItem
from analyzer.value_funnel import verdict_to_row

logger = logging.getLogger("analyzer.analyzer")


async def _preflight_batch(rows: list[Row], ctx: AnalyzeContext, ai_value: bool) -> list[AnalysisResult]:
    sem = asyncio.Semaphore(ctx.concurrency)

    async def process_row(row: Row) -> AnalysisResult:
        async with sem:
            prepared, embedding = await ctx.analyzer._preflight(row, ctx.conn, ai_value)
            if prepared is not None:
                return row, prepared
            if ai_value:
                return row, {"__needs_value": True, "embedding": embedding}
            try:
                result = await ctx.analyzer._analyze_message(
                    message_id=row["id"], text=row["text"], source_name=row["source_name"],
                )
                if result:
                    result["embedding"] = embedding
                return row, result
            except Exception as exc:
                logger.error(f"Analysis failed for msg {row['id']}: {exc}")
                return row, None

    return list(await asyncio.gather(*(process_row(row) for row in rows)))


@ANALYZERS.register("crypto")
async def crypto(rows: list[Row], ctx: AnalyzeContext) -> list[AnalysisResult]:
    return await _preflight_batch(rows, ctx, False)


@ANALYZERS.register("ai_value")
async def ai_value(rows: list[Row], ctx: AnalyzeContext) -> list[AnalysisResult]:
    results = await _preflight_batch(rows, ctx, True)
    router = ctx.analyzer.llm.router
    if router is None:
        return results
    # Resolve the factory through analyzer.py so existing patches keep working.
    from analyzer import analyzer as analyzer_module

    classifier = analyzer_module.LLMValueClassifier(router, task="classify", concurrency=ctx.concurrency)
    items = [ValueItem(id=str(row["id"]), text=row["text"], source=row["source_name"])
             for row, result in results if result and result.get("__needs_value")]
    outcomes = await classifier.classify(items)
    by_id = {outcome.item_id: outcome for outcome in outcomes}
    for index, (row, result) in enumerate(results):
        if result is None or not result.get("__needs_value"):
            continue
        outcome = by_id.get(str(row["id"]))
        if outcome is None or outcome.verdict is None:
            logger.warning("Value classification failed for msg %s: %s", row["id"],
                           outcome.error if outcome else "missing outcome")
            results[index] = (row, None)
            continue
        value_result: dict[str, Any] = verdict_to_row(outcome.verdict)
        value_result["is_ad"] = outcome.verdict.is_ad
        value_result["embedding"] = result.get("embedding")
        results[index] = (row, value_result)
    calls = classifier.calls
    prompt_tokens = [call.prompt_tokens for call in calls]
    completion_tokens = [call.completion_tokens for call in calls]
    costs = [call.cost_usd for call in calls]
    logger.info(
        "Value classification: calls=%d prompt_tokens=%s completion_tokens=%s "
        "cost_usd=%s latency_seconds=%.3f",
        len(calls),
        sum(t for t in prompt_tokens if t is not None) if None not in prompt_tokens else None,
        sum(t for t in completion_tokens if t is not None) if None not in completion_tokens else None,
        sum(c for c in costs if c is not None) if None not in costs else None,
        sum(call.latency_seconds for call in calls),
    )
    return results
