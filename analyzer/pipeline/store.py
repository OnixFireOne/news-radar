"""Sequential transactional writes for analyzed messages."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from analyzer.pipeline.context import AnalysisResult, AnalyzeContext
from analyzer.pipeline.registry import HOOKS

logger = logging.getLogger("analyzer.analyzer")


def store_results(results: list[AnalysisResult], ctx: AnalyzeContext, hooks: tuple[str, ...], ai_value: bool) -> int:
    conn = ctx.conn
    analyzer = ctx.analyzer
    count = 0
    for row, result in results:
        try:
            if result and result.get("__heuristic_ad"):
                conn.execute("UPDATE messages SET analyzed=1, is_ad=1 WHERE id=?", (row["id"],))
                conn.commit()
                count += 1
                continue

            if result and (result.get("summary") or (ai_value and result.get("content_type"))):
                raw_topic = result.get("topic", "general")
                normalized_topic = analyzer._normalize_topic(raw_topic)
                is_ad_llm = 1 if result.get("is_ad") else 0
                if is_ad_llm:
                    logger.info(f"Message {row['id']} flagged as ad by LLM")

                columns = ["message_id", "temperature", "topic", "summary", "keywords", "sentiment"]
                values: list[Any] = [
                    row["id"], result.get("temperature", 5.0), normalized_topic,
                    result.get("summary", ""),
                    result.get("keywords", "[]") if ai_value else json.dumps(result.get("keywords", []), ensure_ascii=False),
                    result.get("sentiment", "neutral"),
                ]
                if ai_value:
                    value_columns = ["content_type", "value_score", "has_outcome", "takeaway"]
                    columns.extend(value_columns)
                    values.extend(result.get(key) for key in value_columns)
                conn.execute(
                    f"INSERT INTO analysis ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})", values,
                )

                emb = result.get("embedding")
                if emb is None:
                    emb = analyzer.embedder.encode(row["text"])
                analyzer.chroma.add_message(
                    message_id=row["id"], embedding=emb, text=row["text"],
                    source_name=row["source_name"],
                    timestamp=row.get("collected_at", datetime.utcnow().isoformat()),
                    temperature=result.get("temperature", 5.0), topic=normalized_topic,
                )
                conn.execute(
                    "UPDATE messages SET analyzed=1, chroma_synced=1, is_ad=? WHERE id=?",
                    (is_ad_llm, row["id"]),
                )
                conn.commit()
                count += 1
                if not is_ad_llm:
                    for hook_name in hooks:
                        HOOKS.get(hook_name)(row, result, ctx)
            else:
                logger.warning(f"Message {row['id']}: Empty result, will retry.")
                conn.rollback()
        except Exception as exc:
            logger.error(f"Failed DB write for message {row['id']}: {exc}")
            try:
                conn.rollback()
            except Exception:
                pass
    return count
