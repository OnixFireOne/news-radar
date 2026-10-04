"""Re-score a sample of already analysed articles with another value prompt, without writing to the DB.

ТЗ #4 И4.2. Prints the score distribution before/after per source and the largest changes,
and saves every pair to data/rescore/<stamp>-<prompt>.json for the report.

    docker compose run --rm --no-deps analyzer python scripts/rescore_sample.py --since 2026-10-04
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyzer.llm_client import build_llm_client  # noqa: E402
from analyzer.prompts import AI_VALUE_PROMPTS  # noqa: E402
from analyzer.value_classifier import LLMValueClassifier, ValueItem  # noqa: E402

QUERY = """SELECT m.id, s.name AS source, m.text, m.url, m.in_digest, m.is_ad,
                  CAST(a.value_score AS INTEGER) AS score, a.content_type
           FROM messages m JOIN sources s ON s.id = m.source_id
           JOIN analysis a ON a.message_id = m.id
           WHERE a.value_score IS NOT NULL AND date(m.collected_at) >= ?
           ORDER BY m.id"""


def histogram(scores: list[int]) -> str:
    counts = Counter(scores)
    return " ".join(f"{score}:{counts[score]}" for score in range(1, 11) if counts[score])


def share(scores: list[int], minimum: int) -> str:
    return f"{sum(s >= minimum for s in scores)}/{len(scores)}"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=datetime.now(timezone.utc).date().isoformat(),
                        help="collected_at date (UTC), inclusive")
    parser.add_argument("--prompt", choices=sorted(AI_VALUE_PROMPTS), default="ai_value-v4")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--db", default="/app/data/news.db")
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(QUERY, (args.since,))]
    conn.close()
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print(f"no analysed articles since {args.since}")
        return

    llm = build_llm_client(task="classify")
    if llm.router is None:
        raise SystemExit("LLM_PROVIDERS is empty: the catalog mode is required")
    settings = json.loads(Path("/app/config/settings.json").read_text(encoding="utf-8"))
    strict = "classify" in settings.get("llm_strict_json_tasks", [])
    classifier = LLMValueClassifier(llm.router, task="classify", concurrency=args.concurrency,
                                    strict=strict, prompt_version=args.prompt)
    print(f"re-scoring {len(rows)} articles since {args.since} with {args.prompt} "
          f"({llm.router.model_for('classify')}, strict={strict})")
    outcomes = await classifier.classify([ValueItem(str(r["id"]), r["text"], r["source"]) for r in rows])

    by_id = {o.item_id: o for o in outcomes}
    pairs: list[dict[str, object]] = []
    failed = 0
    for row in rows:
        outcome = by_id[str(row["id"])]
        if outcome.verdict is None:
            failed += 1
            continue
        v = outcome.verdict
        title = row["text"].strip().splitlines()[0][:90] if row["text"].strip() else ""
        pairs.append({"id": row["id"], "source": row["source"], "url": row["url"], "title": title,
                      "old": row["score"], "new": v.value_score, "old_type": row["content_type"],
                      "new_type": v.content_type, "is_ad_old": row["is_ad"], "is_ad_new": v.is_ad,
                      "in_digest": row["in_digest"], "takeaway": v.takeaway})

    per_source: dict[str, tuple[list[int], list[int]]] = defaultdict(lambda: ([], []))
    for pair in pairs:
        old, new = per_source[str(pair["source"])]
        old.append(int(str(pair["old"])))
        new.append(int(str(pair["new"])))
    every_old = [int(str(p["old"])) for p in pairs]
    every_new = [int(str(p["new"])) for p in pairs]

    print(f"\nscored {len(pairs)}, failed {failed}")
    print(f"{'source':34} {'n':>4}  {'>=7 old':>8} {'>=7 new':>8}  {'>=8 old':>8} {'>=8 new':>8}")
    for source, (old, new) in sorted(per_source.items(), key=lambda kv: -len(kv[1][0])):
        print(f"{source[:34]:34} {len(old):>4}  {share(old, 7):>8} {share(new, 7):>8}  "
              f"{share(old, 8):>8} {share(new, 8):>8}")
    print(f"{'ALL':34} {len(every_old):>4}  {share(every_old, 7):>8} {share(every_new, 7):>8}  "
          f"{share(every_old, 8):>8} {share(every_new, 8):>8}")
    print(f"\nold: {histogram(every_old)}\nnew: {histogram(every_new)}")

    print("\nnew >= 8:")
    for pair in sorted(pairs, key=lambda p: -int(str(p["new"]))):
        if int(str(pair["new"])) >= 8:
            print(f"  {pair['id']:>5} {pair['old']}->{pair['new']} {str(pair['source'])[:12]:12} {pair['title']}")
    print("\nlargest drops among old >= 7:")
    drops = sorted((p for p in pairs if int(str(p["old"])) >= 7),
                   key=lambda p: int(str(p["new"])) - int(str(p["old"])))[:15]
    for pair in drops:
        print(f"  {pair['id']:>5} {pair['old']}->{pair['new']} {str(pair['source'])[:12]:12} {pair['title']}")
    rises = [p for p in pairs if int(str(p["new"])) > int(str(p["old"]))]
    print(f"\nrose: {len(rises)}")
    for pair in rises[:10]:
        print(f"  {pair['id']:>5} {pair['old']}->{pair['new']} {str(pair['source'])[:12]:12} {pair['title']}")

    costs = [c.cost_usd for c in classifier.calls]
    known = [c for c in costs if c is not None]
    print(f"\ncalls {len(costs)}, cost ${sum(known):.4f}" + (f" ({len(costs) - len(known)} without cost)"
                                                         if len(known) < len(costs) else ""))
    out_dir = Path("/app/data/rescore")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"{stamp}-{args.prompt}.json"
    out.write_text(json.dumps({"since": args.since, "prompt": args.prompt, "failed": failed,
                               "cost_usd": sum(known), "pairs": pairs}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"saved {out}")


if __name__ == "__main__":
    asyncio.run(main())
