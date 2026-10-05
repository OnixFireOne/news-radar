"""Optional digest enrichment."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from collections.abc import Mapping, Sequence

from analyzer.pipeline.context import DigestContext, Row
from analyzer.pipeline.registry import EXTRAS
from analyzer.value_funnel import explain_selection
from analyzer.knowledge_publisher import blob_url

logger = logging.getLogger("analyzer.analyzer")


def _table_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().replace("\\", "\\\\").replace("|", "\\|")


def build_candidates_markdown(
    pool: Sequence[Row], selected: Sequence[Row], template: Mapping[str, object],
    name: str, now: datetime,
) -> tuple[str, int]:
    """Build a read-only selection audit; the total counts threshold passing pool rows."""
    options = template.get("candidates_list", {})
    if not isinstance(options, Mapping):
        options = {}
    min_score = float(str(options.get("min_score", 6)))
    threshold = float(str(template.get("min_value_score", 5)))
    selected_ids = [row["id"] for row in selected]
    statuses = explain_selection(pool, template, selected_ids, now)
    total = sum(row.get("value_score") is not None and float(str(row["value_score"])) >= threshold
                for row in pool)
    quotas = template.get("quotas", {})
    if not isinstance(quotas, Mapping):
        quotas = {}
    days = template.get("carryover_days", 0)
    order = template.get("tie_break", "temperature")
    if order != "oldest":
        order = "temperature"
    lines = [
        f"# Кандидаты выпуска {_table_text(name)} — {now:%d.%m.%Y}", "",
        f"Выбрано {len(selected)} из {total} · порог {threshold:g} · очередь {days} дн. · "
        f"квоты: practical {quotas.get('practical', 5)}, tools_research {quotas.get('tools_research', 2)}, "
        f"hype {quotas.get('hype', 1)} · порядок: {order}", "",
        "| Оценка | Тип | Статья | Собрана | Статус |", "|---|---|---|---|---|",
    ]
    types = template.get("types", {})
    if not isinstance(types, Mapping):
        types = {}
    labels = {"selected": "✅ в выпуске", "below_threshold": "⬇ ниже порога",
              "type_off": "🚫 тип отключён", "not_fitted": "⏳ не вместилось"}
    for item in statuses:
        row = item.row
        score_value = row.get("value_score")
        if row.get("id") not in selected_ids and (score_value is None or float(str(score_value)) < min_score):
            continue
        kind = str(row.get("content_type") or "")
        style = types.get(kind, {})
        label = style.get("label", kind) if isinstance(style, Mapping) else kind
        title = next((line.strip() for line in str(row.get("text") or "").splitlines() if line.strip()), "")
        title = re.sub(r"\s+", " ", title)
        title = title[:119] + "…" if len(title) > 120 else title
        title = _table_text(title).replace("[", "\\[").replace("]", "\\]")
        url = str(row.get("url") or "").strip().replace(")", "%29")
        url = re.sub(r"\s", "%20", url).replace("|", "%7C")
        article = f"[{title}]({url})" if url else title
        collected = str(row.get("collected_at") or "")
        try:
            date = datetime.fromisoformat(collected.replace("Z", "+00:00")).strftime("%d.%m")
        except ValueError:
            date = "—"
        status = labels[item.status]
        if item.last_day and item.status == "not_fitted":
            status += " · ⌛ последний день"
        score_text = f"{float(str(score_value)):g}" if score_value is not None else "—"
        lines.append(f"| {score_text} | {_table_text(label)} | "
                     f"{article} | {date} | {status} |")
    return "\n".join(lines) + "\n", total


@EXTRAS.register("candidates")
async def candidates(selected: list[Row], ctx: DigestContext) -> None:
    try:
        options = ctx.template_cfg.get("candidates_list", {})
        if not isinstance(options, Mapping) or not options.get("enabled", False):
            return
        knowledge_cfg = ctx.cfg.get("knowledge", {})
        if not isinstance(knowledge_cfg, Mapping) or not knowledge_cfg.get("enabled", False):
            return
        now = datetime.now(timezone.utc)
        name = str(ctx.artifacts.get("digest_name") or ctx.artifacts.get("category_name") or "articles")
        source_map = ctx.artifacts.get("source_map", {})
        selected_urls = {row["id"]: source_map.get(str(index), "") for index, row in enumerate(selected, 1)}
        pool = [dict(row, url=row.get("url") or selected_urls.get(row["id"], ""))
                for row in ctx.artifacts["pool"]]
        content, total = build_candidates_markdown(pool, selected, ctx.template_cfg, name, now)
        directory = str(knowledge_cfg.get("dir", "knowledge")).strip("/")
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-") or "articles"
        path = f"{directory}/candidates/{now:%Y-%m-%d}-{safe_name}.md"
        ctx.artifacts["extra_files"] = [(path, content)]
        ctx.artifacts["candidates_meta"] = {"path": path, "selected": len(selected), "total": total}
    except Exception:
        logger.warning("Candidates list unavailable")


@EXTRAS.register("knowledge")
async def knowledge(selected: list[Row], ctx: DigestContext) -> None:
    from analyzer import analyzer as analyzer_module

    source_map = ctx.artifacts["source_map"]
    md_map: dict[str, str] = {}
    try:
        knowledge_rows = [dict(row, url=source_map[str(i)]) for i, row in enumerate(selected, 1)]
        delivered: list[str] = []
        md_map = await analyzer_module.publish_selected(
            ctx.analyzer.llm, knowledge_rows,
            {"knowledge": ctx.cfg.get("knowledge", {}),
             "llm_concurrency": ctx.cfg.get("llm_concurrency", 3)},
            None, ctx.analyzer.db_path,
            extra_files=ctx.artifacts.get("extra_files", ()), delivered=delivered,
        )
        meta = ctx.artifacts.get("candidates_meta")
        if meta and meta["path"] in delivered:
            knowledge_cfg = ctx.cfg.get("knowledge", {})
            ctx.artifacts["candidates_link"] = {
                "url": blob_url(str(knowledge_cfg.get("repo", "OnixFireOne/news-radar")),
                                str(knowledge_cfg.get("branch", "main")), meta["path"]),
                "selected": meta["selected"], "total": meta["total"],
            }
    except Exception:
        logger.warning("Knowledge unavailable: published=0 reused=0 failed=%s", len(selected))
    ctx.artifacts["md_map"] = md_map
