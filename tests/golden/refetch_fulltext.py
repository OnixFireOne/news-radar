"""Build a separate golden-set candidate file with full article text."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collectors.fulltext_fetcher import FullTextFetcher

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "value_candidates.jsonl"


async def refetch(out: Path) -> None:
    if out.resolve() == SOURCE.resolve() or (out.exists() and out.samefile(SOURCE)):
        raise ValueError("Output must not overwrite value_candidates.jsonl")
    fetcher = FullTextFetcher(max_fetches_per_cycle=1000, max_fetches_per_feed=1000)
    summaries: dict[str, list[int]] = {}
    rows: list[dict[str, object]] = []
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        row: dict[str, object] = json.loads(line)
        source, url, snippet = row["source"], row["url"], row["text"]
        if not isinstance(source, str) or not isinstance(url, str) or not isinstance(snippet, str):
            raise ValueError("Each candidate must have string source, url and text fields")
        fulltext = await fetcher.fetch(url, feed_key=source)
        use_fulltext = fulltext is not None and len(fulltext) > len(snippet)
        row["snippet_chars"] = len(snippet)
        row["text_source"] = "fulltext" if use_fulltext else "snippet"
        if use_fulltext:
            row["text"] = fulltext
        rows.append(row)
        counts = summaries.setdefault(source, [0, 0, 0])
        counts[0] += 1
        counts[1 if use_fulltext else 2] += 1
    out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    for source, (count, fetched, kept) in summaries.items():
        print(f"{source}: rows={count}, fetched={fetched}, kept snippet={kept}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "value_candidates_fulltext.jsonl")
    args = parser.parse_args()
    asyncio.run(refetch(args.out))


if __name__ == "__main__":
    main()
