"""Compare knowledge prompts on a read-only database; never publish or install usage sinks."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyzer.knowledge_publisher import generate_doc
from analyzer.llm_client import LLMClient, LLMJSONError
from llm_core.catalog import load_catalog, resolve_active
from llm_core.client import ChatMessage
from llm_core.router import ProviderRouter
from llm_core.transport import JsonSchemaTool

ROOT = Path(__file__).resolve().parent / "golden" / "knowledge_runs"
VARIANTS = ["knowledge-v2:gpt-6-luna", "knowledge-v3:gpt-6-luna", "knowledge-v3:gpt-6.1-sol"]


def load_articles(db: str, ids: list[int]) -> list[dict[str, Any]]:
    conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = []
        for message_id in ids:
            row = conn.execute(
                "SELECT m.*, a.takeaway, a.content_type, a.value_score, a.topic "
                "FROM messages m LEFT JOIN analysis a ON a.message_id=m.id WHERE m.id=?",
                (message_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Message {message_id} not found")
            rows.append(dict(row))
        return rows
    finally:
        conn.close()


class EvalClient:
    """Use the production generation pipeline with an isolated, in-memory LLM adapter."""

    def __init__(self, router: ProviderRouter, model: str) -> None:
        self.router = router
        self.model = model
        self.calls: list[dict[str, Any]] = []
        self.draft: dict[str, Any] = {}

    async def complete_json(self, *, user_prompt: str, system_prompt: str,
                            schema: JsonSchemaTool, **kwargs: Any) -> dict[str, Any]:
        messages: list[ChatMessage] = [
            {"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt},
        ]
        tool = self.router.primary().profile.supports_tools(self.model)
        started = time.monotonic()
        response = await self.router.complete(
            "knowledge", messages, model=self.model,
            tool=replace(schema, strict=True) if tool else None,
        )
        self.calls.append({
            "latency_s": round(time.monotonic() - started, 3), "cost_usd": response.cost_usd,
            "prompt_tokens": response.usage.prompt_tokens if response.usage else None,
            "completion_tokens": response.usage.completion_tokens if response.usage else None,
            "mode": "tool" if tool else "text",
        })
        draft: Any = response.structured
        if draft is None:
            text = response.content
            try:
                draft = json.loads(text[text.index("{"):text.rindex("}") + 1])
            except (ValueError, json.JSONDecodeError) as exc:
                raise LLMJSONError("Invalid eval JSON") from exc
        if not isinstance(draft, dict):
            raise LLMJSONError("Expected an object")
        self.draft = draft
        return self.draft


def numbers(text: str) -> set[str]:
    # Normalize decimal punctuation; this is a lexical heuristic, not a factual verdict.
    return {value.replace(",", ".") for value in re.findall(r"\d+(?:[.,]\d+)*", text)}


def metrics(body: str, source: str, draft: dict[str, Any]) -> dict[str, Any]:
    glossary = draft.get("glossary")
    return {"length_chars": len(body), "numbers_absent_from_source": sorted(numbers(body) - numbers(source)),
            "glossary_terms": len(glossary) if isinstance(glossary, list) else 0,
            "has_table": "## Таблица\n" in body}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "data/news.db"))
    parser.add_argument("--message-id", type=int, action="append", required=True)
    parser.add_argument("--variants", nargs="+", default=VARIANTS)
    parser.add_argument("--provider", default="openai.chat_completions")
    parser.add_argument("--catalog", default="config/providers.json")
    parser.add_argument("--settings", default="config/settings.json")
    args = parser.parse_args()
    variants = []
    for variant in args.variants:
        version, sep, model = variant.partition(":")
        if version not in ("knowledge-v2", "knowledge-v3") or not sep or not model:
            parser.error(f"Invalid variant: {variant}")
        variants.append((version, model))
    rows = load_articles(args.db, args.message_id)
    cfg = json.loads(Path(args.settings).read_text(encoding="utf-8"))["knowledge"]
    router = ProviderRouter(resolve_active(load_catalog(args.catalog), [args.provider], os.environ), timeout=600)
    ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for row in rows:
        runs: list[dict[str, Any]] = []
        markdown = [f"# Message {row['id']}\n", f"Источник: {row.get('url', '')}\n"]
        baseline: int | None = None
        for version, model in variants:
            client = EvalClient(router, model)
            doc = await generate_doc(cast(LLMClient, client), row,
                                     {**cfg, "format": "full", "prompt_version": version})
            body = doc.body if doc else ""
            stats = metrics(body, str(row.get("text") or ""), client.draft)
            if version == "knowledge-v2" and doc is not None and baseline is None:
                baseline = len(body)
            run = {"version": version, "model": model, "broken": doc is None, "draft": client.draft,
                   "body": body, "metrics": stats, "calls": client.calls}
            runs.append(run)
            markdown.extend([f"## {version}:{model}\n", body if doc else "BROKEN", ""])
        for run in runs:
            run["metrics"]["length_ratio_to_v2"] = (
                round(len(run["body"]) / baseline, 3) if baseline else None)
        base = ROOT / f"{stamp}-m{row['id']}"
        base.with_suffix(".json").write_text(json.dumps(
            {"message_id": row["id"], "provider": args.provider, "runs": runs},
            ensure_ascii=False, indent=2), encoding="utf-8")
        markdown.append("Числа — лексическая эвристика: округления и преобразования требуют ручной проверки.\n")
        markdown.append("```json\n" + json.dumps([r["metrics"] for r in runs], ensure_ascii=False, indent=2) + "\n```")
        base.with_suffix(".md").write_text("\n".join(markdown), encoding="utf-8")
        print(f"-> {base}.md")


if __name__ == "__main__":
    asyncio.run(main())
