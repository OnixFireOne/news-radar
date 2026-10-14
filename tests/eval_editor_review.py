"""Read-only evaluation of an already published articles digest."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyzer.editor_review import build_editor_prompt, format_review, parse_review
from analyzer.json_schemas import EDITOR_REVIEW_SCHEMA
from analyzer.prompts import EDITOR_REVIEW_PROMPT_VERSION
from llm_core.catalog import load_catalog, resolve_active
from llm_core.client import ChatMessage
from llm_core.router import ProviderRouter


def load_rows(db: str, digest_id: int, carryover_days: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        digest = conn.execute("SELECT created_at FROM digests WHERE id=? AND category='articles'", (digest_id,)).fetchone()
        if digest is None:
            raise ValueError(f"Unknown articles digest {digest_id}")
        end = datetime.fromisoformat(str(digest["created_at"]).replace("Z", "+00:00"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        start = end - timedelta(days=carryover_days)
        fields = "m.id, m.text, m.url, m.collected_at, a.takeaway, a.content_type, a.value_score, a.temperature"
        selected = [dict(row) for row in conn.execute(
            f"SELECT {fields} FROM digest_messages dm JOIN messages m ON m.id=dm.message_id "
            "LEFT JOIN analysis a ON a.message_id=m.id WHERE dm.digest_id=? ORDER BY dm.rowid", (digest_id,))]
        pool = [dict(row) for row in conn.execute(
            f"SELECT {fields} FROM messages m JOIN analysis a ON a.message_id=m.id "
            "WHERE m.analyzed=1 AND (m.is_ad=0 OR m.is_ad IS NULL) "
            "AND datetime(m.collected_at)>=datetime(?) AND datetime(m.collected_at)<=datetime(?) "
            "AND a.value_score IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM digest_messages dm WHERE dm.message_id=m.id AND dm.digest_id<?)",
            (start.isoformat(), end.isoformat(), digest_id))]
        return selected, pool
    finally:
        conn.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digest-id", type=int, required=True)
    parser.add_argument("--provider", default="openai.chat_completions")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "data/news.db"))
    parser.add_argument("--settings", default="config/settings.json")
    parser.add_argument("--catalog", default="config/providers.json")
    args = parser.parse_args()
    cfg = json.loads(Path(args.settings).read_text())["digest_templates"]["ai_value"]
    selected, pool = load_rows(args.db, args.digest_id, float(cfg.get("carryover_days", 0)))
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        created = str(conn.execute("SELECT created_at FROM digests WHERE id=?", (args.digest_id,)).fetchone()[0])
    finally:
        conn.close()
    digest_time = datetime.fromisoformat(created).replace(tzinfo=timezone.utc)
    prompt, rows = build_editor_prompt(selected, pool, cfg, random.Random(0), now=digest_time)
    router = ProviderRouter(resolve_active(load_catalog(args.catalog), [args.provider], os.environ), timeout=600)
    messages: list[ChatMessage] = [{"role": "user", "content": prompt}]
    started = time.monotonic()
    response = await router.complete("editor", messages, model=args.model, temperature=0.2,
                                     tool=EDITOR_REVIEW_SCHEMA if router.primary().profile.supports_tools(args.model) else None)
    raw: object = response.structured
    if raw is None:
        try:
            raw = json.loads(response.content)
        except json.JSONDecodeError:
            raw = response.content
    review = parse_review(raw, rows)
    source_map = {str(i): str(row.get("url") or "") for i, row in enumerate(selected, 1)}
    formatted = format_review(review, rows, source_map) if review else ""
    output = Path("tests/golden/editor_runs")
    output.mkdir(parents=True, exist_ok=True)
    base = output / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-d{args.digest_id}"
    base.with_suffix(".json").write_text(json.dumps({
        "digest_id": args.digest_id, "provider": args.provider, "model": args.model,
        "prompt_version": EDITOR_REVIEW_PROMPT_VERSION, "raw_response": raw,
        "article_ids": {key: row.get("id") for key, row in rows.items()},
        "tokens_in": response.usage.prompt_tokens if response.usage else None,
        "tokens_out": response.usage.completion_tokens if response.usage else None,
        "cost_usd": response.cost_usd, "latency_s": round(time.monotonic() - started, 1),
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    base.with_suffix(".txt").write_text(formatted, encoding="utf-8")
    print(formatted)
    print(f"tokens in/out: {response.usage.prompt_tokens if response.usage else None}/"
          f"{response.usage.completion_tokens if response.usage else None}; cost: ${response.cost_usd}")


if __name__ == "__main__":
    asyncio.run(main())
