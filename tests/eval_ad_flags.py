"""Evaluate is_ad against the owner's dated ad labels (tests/golden/ADS.md)."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyzer.prompts import AI_VALUE_PROMPT_VERSION
from analyzer.value_classifier import ValueItem
from tests.eval_value_scoring import Args, build_classifier

ROOT = Path(__file__).resolve().parent / "golden"
MAX_FALSE_AD = 0.15   # share of "нет" labels the classifier may flag as ads
MAX_MISSED_AD = 0.25  # share of "да" labels the classifier may miss


def parse_ad_labels(text: str) -> dict[str, bool]:
    """Read `реклама: да|нет` per headed section; `пропуск` and blanks are skipped."""
    labels: dict[str, bool] = {}
    for match in re.finditer(r"(?ms)^## (a\d+) · .*?(?=^## a\d+ · |\Z)", text):
        label = re.search(r"(?m)^- реклама: (да|нет)\s*$", match.group())
        if label:
            labels[match.group(1)] = label.group(1) == "да"
    return labels


def load_ad_items(candidates: Path) -> tuple[list[ValueItem], dict[str, bool]]:
    labels = parse_ad_labels((ROOT / "ADS.md").read_text(encoding="utf-8"))
    items: list[ValueItem] = []
    for line in candidates.read_text(encoding="utf-8").splitlines():
        row: dict[str, object] = json.loads(line)
        gid, body, source = row.get("gid"), row.get("text"), row.get("source")
        if isinstance(gid, str) and gid in labels and isinstance(body, str):
            items.append(ValueItem(gid, body, source if isinstance(source, str) else ""))
    return items, {item.id: labels[item.id] for item in items}


def score(labels: dict[str, bool], flags: dict[str, bool | None]) -> dict[str, object]:
    ads = [gid for gid, is_ad in labels.items() if is_ad]
    clean = [gid for gid, is_ad in labels.items() if not is_ad]
    false_ad = [gid for gid in clean if flags.get(gid) is True]
    missed = [gid for gid in ads if flags.get(gid) is False]
    broken = [gid for gid in labels if flags.get(gid) is None]
    false_rate = len(false_ad) / len(clean) if clean else 0.0
    missed_rate = len(missed) / len(ads) if ads else 0.0
    return {"ads": len(ads), "clean": len(clean), "false_ad": false_ad, "missed_ad": missed, "broken": broken,
            "false_ad_rate": round(false_rate, 3), "missed_ad_rate": round(missed_rate, 3),
            "pass": not broken and false_rate <= MAX_FALSE_AD and missed_rate <= MAX_MISSED_AD}


async def evaluate(args: Args) -> dict[str, object]:
    items, labels = load_ad_items(args.candidates)
    if not items:
        raise ValueError("no labeled items in ADS.md")
    classifier = build_classifier(args)
    outcomes = await classifier.classify(items)
    flags: dict[str, bool | None] = {
        o.item_id: (o.verdict.is_ad if o.verdict is not None else None) for o in outcomes}
    costs = [call.cost_usd for call in classifier.calls]
    result = score(labels, flags)
    result.update(provider=args.provider, model=args.model, prompt=AI_VALUE_PROMPT_VERSION,
                  cost_usd=sum(c for c in costs if c is not None) if None not in costs else None)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--impl", choices=("llm",), default="llm")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--structured", choices=("tool", "text"), default="tool")
    parser.add_argument("--candidates", type=Path, default=ROOT / "ads_candidates.jsonl")
    parser.add_argument("--catalog", type=Path, default=Path("/app/config/providers.json"))
    parser.add_argument("--out-dir", type=Path, default=ROOT / "runs")
    args = cast(Args, parser.parse_args())
    result = asyncio.run(evaluate(args))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (args.out_dir / f"ads-{stamp}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("PASS" if result["pass"] else "FAIL",
          f"false ads <= {MAX_FALSE_AD:.0%} of clean, missed ads <= {MAX_MISSED_AD:.0%} of ads")


if __name__ == "__main__":
    main()
