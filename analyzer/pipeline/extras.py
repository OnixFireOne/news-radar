"""Optional digest enrichment."""

from __future__ import annotations

import logging

from analyzer.pipeline.context import DigestContext, Row
from analyzer.pipeline.registry import EXTRAS

logger = logging.getLogger("analyzer.analyzer")


@EXTRAS.register("knowledge")
async def knowledge(selected: list[Row], ctx: DigestContext) -> None:
    from analyzer import analyzer as analyzer_module

    source_map = ctx.artifacts["source_map"]
    md_map: dict[str, str] = {}
    try:
        knowledge_rows = [dict(row, url=source_map[str(i)]) for i, row in enumerate(selected, 1)]
        md_map = await analyzer_module.publish_selected(
            ctx.analyzer.llm, knowledge_rows,
            {"knowledge": ctx.cfg.get("knowledge", {}),
             "llm_concurrency": ctx.cfg.get("llm_concurrency", 3)},
            None, ctx.analyzer.db_path,
        )
    except Exception:
        logger.warning("Knowledge unavailable: published=0 reused=0 failed=%s", len(selected))
    ctx.artifacts["md_map"] = md_map
