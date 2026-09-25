"""Evaluate an interchangeable classifier against dated owner labels."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyzer.prompts import AI_VALUE_PROMPT_VERSION
from analyzer.value_classifier import LLMValueClassifier, ValueClassifier, ValueItem
from llm_core.catalog import load_catalog, resolve_active
from llm_core.router import ProviderRouter

ROOT = Path(__file__).resolve().parent / "golden"
# Owner labeled these from the full page, but the pipeline only sees the RSS teaser:
# openai.com answers bots with a Cloudflare challenge (403). Reported separately.
UNFETCHABLE = frozenset({"g22", "g32"})


class Args(Protocol):
    provider: str
    model: str
    batch_size: int
    structured: str
    threshold: int
    limit: int | None
    catalog: Path
    out_dir: Path


def parse_labels(text: str) -> dict[str, str]:
    """Read only labels from headed golden-set sections."""
    labels: dict[str, str] = {}
    for match in re.finditer(r"(?ms)^## (g\d+) · .*?(?=^## g\d+ · |\Z)", text):
        label = re.search(r"(?m)^- метка: (ценно|хайп|шум|пропуск)\s*$", match.group())
        if label and label.group(1) != "пропуск":
            labels[match.group(1)] = label.group(1)
    return labels


def load_items(limit: int | None = None) -> tuple[list[ValueItem], dict[str, str]]:
    labels = parse_labels((ROOT / "LABELING.md").read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for name in ("value_candidates.jsonl", "synthetic.jsonl"):
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            row: dict[str, object] = json.loads(line)
            rows.append(row)
    items: list[ValueItem] = []
    selected: dict[str, str] = {}
    for row in rows:
        gid = row.get("gid")
        if not isinstance(gid, str):
            continue
        label = row.get("label") if gid.startswith("s") else labels.get(gid)
        if label not in ("ценно", "хайп", "шум"):
            continue
        body, source = row.get("text"), row.get("source")
        if not isinstance(body, str):
            continue
        items.append(ValueItem(gid, body, source if isinstance(source, str) else ""))
        selected[gid] = label
        if limit is not None and len(items) >= limit:
            break
    return items, selected


def build_classifier(args: Args) -> ValueClassifier:
    catalog = load_catalog(args.catalog)
    active = resolve_active(catalog, [args.provider], os.environ)
    router = ProviderRouter(active, timeout=300)
    if args.structured not in ("tool", "text"):
        raise ValueError("structured must be tool or text")
    return LLMValueClassifier(router, model=args.model, batch_size=args.batch_size,
                              structured=cast(Literal["tool", "text"], args.structured))


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction + 0.5)))]


async def evaluate(args: Args) -> dict[str, object]:
    items, labels = load_items(args.limit)
    classifier = build_classifier(args)
    outcomes = await classifier.classify(items)
    calls = classifier.calls if isinstance(classifier, LLMValueClassifier) else []
    confusion: Counter[str] = Counter()
    paths: Counter[str] = Counter()
    hype_violations: list[str] = []
    mismatches: list[str] = []
    adversarial: list[dict[str, object]] = []
    details: list[dict[str, object]] = []
    broken = 0
    for outcome in outcomes:
        label = labels[outcome.item_id]
        verdict = outcome.verdict
        paths[outcome.path] += 1
        score = verdict.value_score if verdict else None
        predicted = score is not None and score >= args.threshold
        actual = label == "ценно"
        if verdict is None:
            broken += 1
            confusion[("valuable" if actual else "not_valuable") + "/broken"] += 1
        else:
            confusion[("valuable" if actual else "not_valuable") + "/" +
                      ("valuable" if predicted else "not_valuable")] += 1
            if predicted != actual:
                mismatches.append(outcome.item_id)
            if label == "хайп" and score is not None and score >= 8:
                hype_violations.append(outcome.item_id)
        record: dict[str, object] = {"gid": outcome.item_id, "label": label,
                                     "path": outcome.path, "error": outcome.error,
                                     "verdict": asdict(verdict) if verdict else None}
        details.append(record)
        if outcome.item_id in {"g20", "g21", "g24", "g28", "g30", "g31", "s01", "s02"}:
            adversarial.append({"gid": outcome.item_id, "label": label, "score": score,
                                "type": verdict.content_type if verdict else None,
                                "takeaway": verdict.takeaway if verdict else None})
    evaluated = sum(confusion.values())
    accuracy = sum(count for key, count in confusion.items() if key in
                   ("valuable/valuable", "not_valuable/not_valuable")) / evaluated if evaluated else None
    reachable = [record for record in details if record["gid"] not in UNFETCHABLE]
    reachable_correct = sum(1 for record in reachable if record["verdict"] is not None and
                            (cast(dict[str, int], record["verdict"])["value_score"] >= args.threshold)
                            == (record["label"] == "ценно"))
    accuracy_reachable = reachable_correct / len(reachable) if reachable else None
    costs_missing = sum(call.cost_usd is None for call in calls)
    costs = [call.cost_usd for call in calls if call.cost_usd is not None]
    cost_total = sum(costs) if calls and costs_missing == 0 else None
    latency = [call.latency_seconds for call in calls]
    result: dict[str, object] = {
        "date_utc": datetime.now(timezone.utc).isoformat(), "provider": args.provider,
        "model": args.model, "prompt_version": AI_VALUE_PROMPT_VERSION,
        "batch_size": args.batch_size, "structured": args.structured,
        "threshold": args.threshold, "accuracy": accuracy,
        "accuracy_without_unfetchable": accuracy_reachable, "unfetchable": sorted(UNFETCHABLE),
        "evaluated": evaluated,
        "confusion": dict(confusion), "hype_ge_8": hype_violations,
        "mismatches": mismatches, "broken": broken, "paths": dict(paths),
        "tokens_in": sum(call.prompt_tokens or 0 for call in calls),
        "tokens_out": sum(call.completion_tokens or 0 for call in calls),
        "cost_usd": cost_total, "calls_without_cost": costs_missing,
        "cost_sources": dict(Counter(call.cost_source for call in calls)),
        "latency_median": statistics.median(latency) if latency else None,
        "latency_p95": percentile(latency, 0.95), "adversarial": adversarial,
        "items": details, "calls": [asdict(call) for call in calls],
        "pass": accuracy is not None and accuracy >= 0.8 and not hype_violations,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--structured", choices=("tool", "text"), default="tool")
    parser.add_argument("--threshold", type=int, default=5)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--catalog", type=Path, default=Path("/app/config/providers.json"))
    parser.add_argument("--out-dir", type=Path, default=ROOT / "runs")
    args = parser.parse_args()
    result = asyncio.run(evaluate(args))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Model ids may contain "/" (e.g. OpenRouter vendor prefixes).
    model_slug = args.model.replace("/", "_")
    name = f"{stamp}-{args.provider}-{model_slug}-b{args.batch_size}-{args.structured}.json"
    (args.out_dir / name).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    results_file = ROOT / "RESULTS.md"
    if not results_file.exists():
        results_file.write_text("| Date | Provider | Model | Prompt | Batch | Structured | Accuracy | Hype ≥8 | Broken | Cost USD | Median / p95 latency | Notes |\n"
                                "|---|---|---|---|---:|---|---:|---:|---:|---:|---|---|\n", encoding="utf-8")
    accuracy = result["accuracy"]
    latency_median, latency_p95 = result["latency_median"], result["latency_p95"]
    hype_violations = cast(list[str], result["hype_ge_8"])
    row = (f"| {stamp} | {args.provider} | {args.model} | {AI_VALUE_PROMPT_VERSION} | {args.batch_size} | "
           f"{args.structured} | {accuracy} | {len(hype_violations)} | {result['broken']} | "
           f"{result['cost_usd']} | {latency_median} / {latency_p95} | missing cost: "
           f"{result['calls_without_cost']} calls |\n")
    with results_file.open("a", encoding="utf-8") as output:
        output.write(row)
    for key in ("accuracy", "accuracy_without_unfetchable", "unfetchable", "confusion", "hype_ge_8", "broken", "paths", "tokens_in", "tokens_out",
                "cost_usd", "calls_without_cost", "cost_sources", "latency_median", "latency_p95", "mismatches"):
        print(f"{key}: {result[key]}")
    print("Adversarial: gid | label | score | type | takeaway")
    for item in cast(list[dict[str, object]], result["adversarial"]):
        print(f"{item['gid']} | {item['label']} | {item['score']} | {item['type']} | {item['takeaway']}")
    print("Mismatches: gid | label | score | type")
    mismatches = cast(list[str], result["mismatches"])
    for item in cast(list[dict[str, object]], result["items"]):
        if item["gid"] in mismatches:
            verdict = cast(dict[str, object], item["verdict"])
            print(f"{item['gid']} | {item['label']} | {verdict['value_score']} | {verdict['content_type']}")
    print("PASS" if result["pass"] else "FAIL", "accuracy >= 0.80 and zero hype >= 8")


if __name__ == "__main__":
    main()
