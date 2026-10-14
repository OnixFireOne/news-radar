"""Private, advisory review of an already selected AI digest."""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from analyzer.json_schemas import EDITOR_REVIEW_SCHEMA
from analyzer.pipeline.context import Row
from analyzer.pipeline.extras import candidate_title
from analyzer.prompts import EDITOR_REVIEW_PROMPT, EDITOR_REVIEW_PROMPT_VERSION
from analyzer.value_funnel import rank_candidates
from analyzer.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EditorReview:
    verdict: str
    swaps: list[tuple[str, str, str]]
    duplicates: list[tuple[str, str, str]]
    additions: list[tuple[str, str]]
    summary: str


def _clean(value: object) -> str:
    return str(value or "").replace("<<<", "‹‹‹").replace(">>>", "›››")


def _age(value: object, now: datetime) -> int:
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, (now - date.astimezone(timezone.utc)).days)
    except ValueError:
        return 0


def build_editor_prompt(
    selected: Sequence[Row], pool: Sequence[Row], cfg: Mapping[str, Any], rng: random.Random,
    now: datetime | None = None,
) -> tuple[str, dict[str, Row]]:
    """Frame untrusted excerpts and randomize the ranked candidate sample."""
    template = cfg.get("template_cfg", cfg)
    options = cfg.get("editor_review", cfg)
    selected_ids = {row.get("id") for row in selected}
    minimum = float(template.get("min_value_score", 5))
    candidates = [row for row in rank_candidates(pool, template)
                  if row.get("id") not in selected_ids and row.get("value_score") is not None
                  and float(str(row["value_score"])) >= minimum][:max(0, int(options.get("candidates", 20)))]
    candidates = [dict(row) for row in candidates]
    rng.shuffle(candidates)
    rows: dict[str, Row] = {}
    for prefix, group in (("S", selected), ("C", candidates)):
        for index, row in enumerate(group, 1):
            rows[f"{prefix}{index}"] = dict(row)
    # Queue age is relative to the digest run; evals over past digests pass its creation time.
    now = now or datetime.now(timezone.utc)
    limit = max(0, int(options.get("text_chars", 600)))
    blocks = []
    for article_id, row in rows.items():
        blocks.append(
            f"<<<ARTICLE {article_id}>>>\n"
            f"id: {article_id}\ncontent_type: {_clean(row.get('content_type'))}\n"
            f"value_score: {_clean(row.get('value_score'))}\n"
            f"days_in_queue: {_age(row.get('collected_at'), now)}\n"
            f"takeaway: {_clean(row.get('takeaway'))}\n"
            f"title: {_clean(candidate_title(row))}\n"
            f"text: {_clean(str(row.get('text') or '')[:limit])}\n"
            f"<<<END ARTICLE {article_id}>>>"
        )
    return EDITOR_REVIEW_PROMPT + "\n\n" + "\n\n".join(blocks), rows


def parse_review(data: object, ids: Mapping[str, Row] | Sequence[str]) -> EditorReview | None:
    if not isinstance(data, dict) or data.get("verdict") not in ("ok", "improve"):
        return None
    allowed = set(ids)
    selected = {key for key in allowed if key.startswith("S")}
    candidates = {key for key in allowed if key.startswith("C")}
    swaps: list[tuple[str, str, str]] = []
    duplicates: list[tuple[str, str, str]] = []
    additions: list[tuple[str, str]] = []
    used_out: set[str] = set()
    used_in: set[str] = set()
    for item in data.get("swaps", []) if isinstance(data.get("swaps"), list) else []:
        if not isinstance(item, dict):
            continue
        out_id, in_id, reason = item.get("out_id"), item.get("in_id"), item.get("reason")
        if (isinstance(out_id, str) and isinstance(in_id, str) and out_id in selected and in_id in candidates and out_id not in used_out
                and in_id not in used_in and isinstance(reason, str) and reason.strip()):
            swaps.append((out_id, in_id, reason.strip()))
            used_out.add(out_id)
            used_in.add(in_id)
            if len(swaps) == 3:
                break
    seen_pairs: set[tuple[str, str]] = set()
    for item in data.get("duplicates", []) if isinstance(data.get("duplicates"), list) else []:
        if not isinstance(item, dict):
            continue
        first, second, reason = item.get("id_a"), item.get("id_b"), item.get("reason")
        if (isinstance(first, str) and isinstance(second, str) and first in selected and second in selected and first != second
                and isinstance(reason, str) and reason.strip()):
            pair = (min(first, second), max(first, second))
            if pair not in seen_pairs:
                duplicates.append((first, second, reason.strip()))
                seen_pairs.add(pair)
    used_add: set[str] = set()
    for item in data.get("additions", []) if isinstance(data.get("additions"), list) else []:
        if not isinstance(item, dict):
            continue
        article_id, reason = item.get("id"), item.get("reason")
        if (isinstance(article_id, str) and article_id in candidates and article_id not in used_add
                and isinstance(reason, str) and reason.strip()):
            additions.append((article_id, reason.strip()))
            used_add.add(article_id)
            if len(additions) == 3:
                break
    summary = data.get("summary")
    summary = summary.strip() if isinstance(summary, str) else ""
    if not (swaps or duplicates or additions or summary):
        return None
    return EditorReview(data["verdict"], swaps, duplicates, additions, summary)


def format_review(review: EditorReview, rows: Mapping[str, Row], source_map: Mapping[str, str]) -> str:
    lines = [f"🧐 Редактор выпуска: {'всё хорошо' if review.verdict == 'ok' else 'есть предложения'}"]
    if review.summary:
        lines.append(review.summary)

    def article(article_id: str) -> str:
        row = rows[article_id]
        url = str(row.get("url") or "")
        if not url and article_id.startswith("S"):
            url = source_map.get(article_id[1:], "")
        score = row.get("value_score")
        return f"{candidate_title(row)} (оценка {score if score is not None else '—'})" + (f" — {url}" if url else "")

    if review.swaps:
        lines.append("Заменить:")
        lines.extend(f"• {article(out_id)} → {article(in_id)}: {reason}" for out_id, in_id, reason in review.swaps)
    if review.duplicates:
        lines.append("Дубли:")
        lines.extend(f"• {article(first)} / {article(second)}: {reason}" for first, second, reason in review.duplicates)
    if review.additions:
        lines.append("Добавить:")
        lines.extend(f"• {article(article_id)}: {reason}" for article_id, reason in review.additions)
    result: list[str] = []
    for line in lines:
        if sum(len(part) + 1 for part in result) + len(line) > 3500:
            break
        result.append(line)
    return "\n".join(result)


def save_review(path_dir: str | Path, now: datetime, payload: Mapping[str, object]) -> None:
    try:
        directory = Path(path_dir)
        directory.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(payload.get("name", "articles"))).strip("-") or "articles"
        path = directory / f"{now:%Y-%m-%d}-{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception:
        logger.warning("Editor review save failed", exc_info=True)


async def run_editor_review(
    llm: LLMClient, selected: Sequence[Row], pool: Sequence[Row], template_cfg: Mapping[str, Any],
    source_map: Mapping[str, str], name: str,
) -> str | None:
    try:
        prompt, rows = build_editor_prompt(selected, pool, template_cfg, random.Random())
        raw = await llm.complete_json(prompt, task="editor", schema=EDITOR_REVIEW_SCHEMA, temperature=0.2)
        review = parse_review(raw, rows)
        router = llm.router
        model = router.model_for("editor") if router is not None else "legacy"
        save_review("data/editor_reviews", datetime.now(timezone.utc), {
            "name": name, "prompt_version": EDITOR_REVIEW_PROMPT_VERSION, "model": model,
            "raw_response": raw, "review": asdict(review) if review else None,
            "article_ids": {key: row.get("id") for key, row in rows.items()},
        })
        return format_review(review, rows, source_map) if review else None
    except Exception:
        logger.warning("Editor review unavailable", exc_info=True)
        return None
