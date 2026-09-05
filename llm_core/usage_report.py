"""Aggregate UsageRecord entries into a per-model spend report."""

from __future__ import annotations

from dataclasses import dataclass

from llm_core.usage import UsageRecord


@dataclass(frozen=True)
class ModelUsageSummary:
    model: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


def summarize_by_model(records: list[UsageRecord]) -> list[ModelUsageSummary]:
    """Aggregate usage records into per-model totals, sorted by total_tokens desc."""
    totals: dict[str, ModelUsageSummary] = {}
    for r in records:
        prev = totals.get(r.model)
        if prev is None:
            totals[r.model] = ModelUsageSummary(
                model=r.model,
                calls=1,
                prompt_tokens=r.prompt_tokens,
                completion_tokens=r.completion_tokens,
                total_tokens=r.total_tokens,
            )
        else:
            totals[r.model] = ModelUsageSummary(
                model=r.model,
                calls=prev.calls + 1,
                prompt_tokens=prev.prompt_tokens + r.prompt_tokens,
                completion_tokens=prev.completion_tokens + r.completion_tokens,
                total_tokens=prev.total_tokens + r.total_tokens,
            )
    return sorted(totals.values(), key=lambda s: s.total_tokens, reverse=True)


def format_report_text(summaries: list[ModelUsageSummary]) -> str:
    """Render a per-model usage summary as a human-readable report string."""
    if not summaries:
        return "No LLM usage recorded."
    lines = ["LLM usage report:"]
    for s in summaries:
        lines.append(
            f"  {s.model}: {s.calls} calls, "
            f"{s.prompt_tokens} prompt + {s.completion_tokens} completion "
            f"= {s.total_tokens} tokens"
        )
    return "\n".join(lines)
