"""
Analyzer — AI-powered batch processing of collected messages.

Runs on a schedule (ANALYZE_INTERVAL_MINUTES).
Picks up unprocessed messages from DB → sends to LLM → saves results.
Also generates periodic digests for the Telegram bot.

Phase 1 additions:
  - After LLM analysis: compute BGE-m3 embedding and store in ChromaDB
  - ChromaDB enables: semantic search, similar-post lookup, deduplication
  - If ChromaDB is unavailable: falls back gracefully (warning, no crash)
"""

import asyncio
import json
import logging
import os
import sys
import sqlite3
import httpx
from datetime import datetime, timedelta
from pathlib import Path
from collections.abc import Sequence
from typing import Optional, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzer.llm_client import LLMClient, is_llm_locked, LLMLock, set_local_mode
from analyzer.prompts import SINGLE_MESSAGE_PROMPT, DIGEST_PROMPT, SYSTEM_PROMPT
from analyzer.value_classifier import LLMValueClassifier as LLMValueClassifier  # re-export: bricks resolve it here so test patches apply

from analyzer.knowledge_publisher import publish_selected as publish_selected  # re-export: see above
from analyzer.pipeline.context import AnalyzeContext, DigestContext, CategorySpec
from analyzer.pipeline.categories import (DigestPart, load_categories, resolve_digest,
                                          warn_uncategorized_sources)
from analyzer.pipeline.registry import ANALYZERS, EXTRAS, SELECTORS, WRITERS
from analyzer.pipeline.legacy import resolve_legacy
from analyzer.pipeline.store import store_results
import analyzer.pipeline.analyzers  # Register analysis bricks.
import analyzer.pipeline.hooks  # Register hooks.
import analyzer.pipeline.selectors  # Register selectors.
import analyzer.pipeline.extras  # Register extras.
import analyzer.pipeline.writers  # Register writers.
from analyzer.embedder import get_embedder
from analyzer.chroma_client import ChromaClient
from analyzer.trend_tracker import TrendTracker   # Phase 2: trend detection
from database.schema import get_db, init_db
from config.config_watcher import ConfigWatcher

logger = logging.getLogger(__name__)


class NewsAnalyzer:
    """
    Batch processor that sends messages through the LLM pipeline.

    Flow:
    1. Fetch messages where analyzed=0
    2. For each: request temperature, topic, summary, keywords from LLM
    3. Save results to the analysis table
    4. Mark message.analyzed=1
    """

    def __init__(
        self,
        db_path: str,
        llm_client: LLMClient,
        batch_size: int = 10,
        interval_minutes: int = 30,
        cfg: ConfigWatcher | None = None,
    ):
        self.db_path = db_path
        self.llm = llm_client
        self.batch_size = batch_size
        self.interval = interval_minutes * 60
        self.cfg = cfg  # optional — used for digest_rules + topics hot-reload

        # Vector store for semantic search + dedup (Phase 1)
        self.chroma = ChromaClient()
        self.embedder = get_embedder()  # BGE-m3, loaded on first encode() call

        # Phase 3: topic normalization table (reloaded hot from topics.json)
        self._topics: dict[str, Any] = cfg.load_topics() if cfg else {}
        if self._topics:
            logger.info(f"Loaded {len(self._topics)} topic aliases from topics.json")

    async def analyze_pending(self) -> int:
        """
        Process all unanalyzed messages.
        Returns the number of messages successfully analyzed.
        """
        count = 0

        # Don't start analyzing if the LLM is busy (e.g. generating a digest)
        if is_llm_locked():
            logger.info("analyze_pending: LLM is locked (digest generation in progress). Pausing...")
            return 0

        conn = get_db(self.db_path)
        try:
            # Load active subscriptions for real-time alerting
            active_subs = conn.execute("SELECT user_id, query FROM subscriptions WHERE active=1").fetchall()
            subs_list = [{"user_id": s["user_id"], "query": s["query"], "q_lower": s["query"].lower()} for s in active_subs]
        except Exception:
            subs_list = []

        try:
            cfg = self._pipeline_config()
            specs = (load_categories(cfg, self.llm.router is not None) if cfg["categories"]
                     else [resolve_legacy(cfg, self.llm.router is not None)])
            min_len = int(self.cfg.get("min_message_length", 30)) if self.cfg else 30
            concurrency = int(self.cfg.get("llm_concurrency", 3)) if self.cfg else 3
            for spec in specs:
                source_filter, source_params = self._source_filter(spec.sources)
                rows = conn.execute(f"""
                    SELECT m.id, m.text, s.name as source_name, m.collected_at, m.views, m.forwards
                    FROM messages m JOIN sources s ON m.source_id = s.id
                    WHERE m.analyzed = 0 AND length(m.text) >= ? {source_filter}
                    ORDER BY COALESCE(m.views, 0) DESC, length(m.text) DESC, m.collected_at DESC
                    LIMIT ?
                """, (min_len, *source_params, self.batch_size)).fetchall()
                if not rows:
                    continue
                pending_batch = [dict(r) for r in rows]
                ctx = AnalyzeContext(self, conn, cfg, subs_list, concurrency, spec.params)
                results = await ANALYZERS.get(spec.analyzer)(pending_batch, ctx)
                count += store_results(results, ctx, spec.hooks, spec.analyzer == "ai_value")

        finally:
            conn.close()

        logger.info(f"Analyzed {count} messages")
        return count

    async def _preflight(
        self, row: dict[str, Any], conn: sqlite3.Connection, ai_value: bool,
    ) -> tuple[dict[str, Any] | None, list[float] | None]:
        # 0. Heuristic ad pre-filter (no LLM call needed)
        if self._is_heuristic_ad(row["text"]):
            logger.info(f"Message {row['id']} flagged as ad by heuristic filter — skipping LLM")
            return {"__heuristic_ad": True}, None

        # 1. Pre-flight Semantic Deduplication
        embedding: list[float] | None = None
        try:
            # Encode using thread pool (BGE-m3 is CPU bound)
            loop = asyncio.get_running_loop()
            embedding = await loop.run_in_executor(None, self.embedder.encode, row["text"])

            if self.chroma.health_check():
                # Chroma search
                matches = self.chroma.search(query_embedding=embedding, limit=1)
                if matches:
                    best = matches[0]
                    if best.get("similarity", 0) > 0.90:
                        logger.info(f"Message {row['id']} is duplicate of {best['message_id']} (sim={best['similarity']}). Cloning AI response.")
                        if ai_value:
                            original = conn.execute(
                                "SELECT a.*, m.is_ad FROM analysis a "
                                "JOIN messages m ON m.id=a.message_id WHERE a.message_id=?",
                                (best["message_id"],),
                            ).fetchone()
                            if original is not None and all(original[key] is not None for key in
                                    ("content_type", "value_score", "has_outcome", "takeaway")):
                                cloned_value: dict[str, Any] = dict(original)
                                cloned_value["embedding"] = embedding
                                return cloned_value, embedding
                            return None, embedding
                        cloned_result: dict[str, Any] = {
                            "topic": best.get("topic", "general"),
                            "temperature": best.get("temperature", 5.0),
                            "summary": best.get("document", row["text"][:200])[:500],
                            "keywords": ["duplicate"],
                            "sentiment": "neutral",
                            "embedding": embedding
                        }
                        return cloned_result, embedding
        except Exception as e:
            logger.warning(f"Pre-flight deduplication failed for msg {row['id']}: {e}")

        return None, embedding

    async def _send_instant_alert(self, msg_id: int, source: str, temp: float, topic: str, summary: str, text: str) -> None:
        """Dispatch a breaking news alert — via OpenClaw if configured, else direct Telegram."""
        # Try to build a direct post link from the DB
        source_url = f"https://t.me/{source}"
        conn = get_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT m.external_id FROM messages m JOIN sources s ON m.source_id=s.id "
                "WHERE m.id=? AND s.name=?", (msg_id, source)
            ).fetchone()
            if row and row["external_id"]:
                source_url = f"https://t.me/{source}/{row['external_id']}"
            conn.execute("UPDATE messages SET alerted_at = CURRENT_TIMESTAMP WHERE id = ?", (msg_id,))
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

        await self._route_event("breaking_alert", {
            "message_id": msg_id,
            "source": source,
            "source_url": source_url,
            "topic": topic,
            "temperature": temp,
            "summary": summary,
        })

    async def _route_event(self, event_type: str, data: dict[str, Any]) -> None:
        """
        Route an event to OpenClaw via OpenAI-compatible completion endpoint.

        We format each event as readable text so the agent understands the context.

        Toggle: config/settings.json -> "route_via_openclaw": true  (hot-reload)
        """
        webhook_url = (os.getenv("OPENCLAW_WEBHOOK_URL") or os.getenv("OPENCLAW_API_URL") or "").strip()
        route_enabled = self.cfg.get("route_via_openclaw", False) if self.cfg else False

        # Convert old /hooks/wake URL to OpenAI /v1/chat/completions
        if webhook_url.endswith("/hooks/wake"):
            webhook_url = webhook_url.replace("/hooks/wake", "/v1/chat/completions")
        elif not webhook_url.endswith("/v1/chat/completions"):
            webhook_url = "http://openclaw:18789/v1/chat/completions"

        if webhook_url and route_enabled:
            # Build human-readable text for OpenClaw /hooks/wake
            if event_type == "breaking_alert":
                text = (
                    f"[NEWS-RADAR EVENT: breaking_alert]\n"
                    f"Topic: {data.get('topic')}\n"
                    f"Temperature: {data.get('temperature')}/10\n"
                    f"Source: {data.get('source')} ({data.get('source_url')})\n"
                    f"Summary: {data.get('summary')}"
                )
            elif event_type == "trend_alert":
                text = (
                    f"[NEWS-RADAR EVENT: trend_alert]\n"
                    f"Topic: {data.get('topic')}\n"
                    f"Trend Score: {data.get('score', 0):.1f} \u2014 {data.get('sources')} unique channels\n"
                    f"Summary: {data.get('summary')}"
                )
            elif event_type == "digest":
                text = (
                    f"[NEWS-RADAR EVENT: digest]\n"
                    f"Period: {data.get('period')}\n"
                    f"Messages: {data.get('message_count')}\n\n"
                    f"{data.get('text', '')}"
                )
            elif event_type == "subscription_match":
                text = (
                    f"[NEWS-RADAR EVENT: subscription_match]\n"
                    f"User: {data.get('user_id')}\n"
                    f"Query: {data.get('query')}\n"
                    f"Source: {data.get('source')}\n"
                    f"Summary: {data.get('summary')}\n\n"
                    f"Action: Review this real-time match and immediately notify the user if relevant. Provide the source link (https://t.me/{data.get('source')})."
                )
            else:
                text = f"[NEWS-RADAR EVENT: {event_type}]\n{json.dumps(data, ensure_ascii=False)}"

            token = (os.getenv("OPENCLAW_WEBHOOK_TOKEN") or os.getenv("OPENCLAW_API_TOKEN") or "").strip()
            headers = {"Authorization": f"Bearer {token}"} if token else {}

            payload = {
                "model": "main",
                "messages": [
                    {"role": "system", "content": "You are the RoutingAgent. Process this event according to AGENTS.md instructions."},
                    {"role": "user", "content": text}
                ]
            }

            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        webhook_url,
                        json=payload,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    logger.info(f"Event '{event_type}' \u2192 OpenClaw (HTTP {resp.status_code})")
                    self._log_dispatch(event_type, "agent", "ok", text, resp.status_code)
            except Exception as e:
                logger.error(f"OpenClaw webhook failed ({event_type}): {e}")
                self._log_dispatch(event_type, "agent", "error", text)
        else:
            # Direct Telegram (OpenClaw disabled or not configured)
            await self._fallback_telegram(event_type, data)

    def _log_dispatch(self, event_type: str, sent_to: str, status: str,
                      payload_preview: str = "", http_status: Optional[int] = None) -> None:
        """Persist a dispatch record to dispatch_log for audit and debugging."""
        try:
            conn = get_db(self.db_path)
            conn.execute(
                "INSERT INTO dispatch_log (event_type, sent_to, status, payload_preview, http_status) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_type, sent_to, status, payload_preview[:300], http_status)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"dispatch_log write failed: {e}")

    async def _enrich_alert_for_telegram(self, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        LLM enrichment step before rendering the alert template.

        breaking_alert:
            summary is already in Russian, but topic is an English category label
            and there is no headline yet. LLM generates a punchy Russian headline.
            Returns: data + {"headline": str}

        hot_trend:
            topic and summary both come from _name_cluster() which prompts English output.
            LLM translates both to Russian.
            Returns: data + {"headline": str, "summary_ru": str}

        Falls back gracefully — returns original data unchanged if LLM fails.
        """
        from analyzer.prompts import ALERT_ENRICH_PROMPT, HOT_TREND_ENRICH_PROMPT

        try:
            if event_type == "breaking_alert":
                prompt = ALERT_ENRICH_PROMPT.format(
                    topic=data.get("topic", ""),
                    summary=data.get("summary", ""),
                )
                result = await self.llm.complete_json(
                    user_prompt=prompt,
                    system_prompt=(
                        "Ты редактор русскоязычного крипто-канала. "
                        "Отвечай строго валидным JSON, без пояснений."
                    ),
                    temperature=0.3,
                    disable_thinking=False,  # alert enrichment: quality > speed
                )
                if isinstance(result, dict) and result.get("headline"):
                    enriched = {**data, "headline": result["headline"]}
                    if result.get("summary_ru"):
                        enriched["summary"] = result["summary_ru"]
                    return enriched

            elif event_type == "hot_trend":
                prompt = HOT_TREND_ENRICH_PROMPT.format(
                    topic=data.get("topic", ""),
                    summary=data.get("summary", ""),
                )
                result = await self.llm.complete_json(
                    user_prompt=prompt,
                    system_prompt=(
                        "Ты редактор русскоязычного крипто-канала. "
                        "Отвечай строго валидным JSON, без пояснений."
                    ),
                    temperature=0.3,
                    disable_thinking=False,  # alert enrichment: quality > speed
                )
                if isinstance(result, dict):
                    enriched = dict(data)
                    if result.get("headline"):
                        enriched["headline"] = result["headline"]
                    if result.get("summary_ru"):
                        enriched["summary"] = result["summary_ru"]
                    return enriched

        except Exception as e:
            logger.warning(f"Alert enrichment LLM call failed ({event_type}): {e} — using raw data")

        return data  # graceful fallback: raw data, no headline

    async def _fallback_telegram(self, event_type: str, data: dict[str, Any]) -> None:
        """Send event directly to Telegram using structured HTML templates.

        For breaking_alert and hot_trend: calls _enrich_alert_for_telegram first
        to generate a Russian headline (and translate the summary for hot_trend).
        The LLM enrichment is best-effort — if it fails, raw data is used as fallback.
        """
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        allowed_users = os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",")
        if not bot_token or not allowed_users or not allowed_users[0]:
            return

        # LLM enrichment: generate Russian headline / translate for relevant alert types
        if event_type in ("breaking_alert", "hot_trend"):
            data = await self._enrich_alert_for_telegram(event_type, data)

        parse_mode = "HTML"
        message = ""

        def _h(text: str) -> str:
            """Escape HTML special chars for Telegram HTML mode."""
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        def _trim_to_sentences(text: str, max_sentences: int) -> str:
            """Trim text to at most max_sentences (split by '. ')."""
            import re
            sentences = re.split(r"(?<=[.!?])\s+", text.strip())
            return " ".join(sentences[:max_sentences])

        if event_type == "breaking_alert":
            # ── Температурный алерт ───────────────────────────────────────────
            # 🚨 <b>ЗАГОЛОВОК (LLM)</b> (10/10)
            # <blockquote expandable>саммари макс 10 предложений</blockquote>
            # <a href="...">источник</a>
            source_url = data.get("source_url", f"https://t.me/{data['source']}")
            # Prefer LLM-generated headline; fall back to raw topic category
            headline = _h(data.get("headline") or data.get("topic", ""))
            temp = data.get("temperature", 0)
            summary_raw = _h(_trim_to_sentences(data.get("summary", ""), 10))

            message = (
                f"🚨 <b>{headline}</b> ({temp:.0f}/10)\n\n"
                f"<blockquote expandable>{summary_raw}</blockquote>\n\n"
                f'<a href="{source_url}">источник</a>'
            )

        elif event_type == "hot_trend":
            # ── Тренд-алерт ──────────────────────────────────────────────────
            # 🔥 HOT TREND
            #
            # <b>ЗАГОЛОВОК (LLM-переведённый)</b>
            # Score: X | Channels: Y
            # <blockquote expandable>саммари (LLM-переведённое)</blockquote>
            #
            # Источники: @ch1, @ch2, ...
            # Prefer LLM-generated Russian headline; fall back to raw English topic
            headline = _h(data.get("headline") or data.get("topic", ""))
            score = data.get("score", 0)
            sources = data.get("sources", 0)
            channels = data.get("channels", [])
            source_urls = data.get("source_urls", {})
            # summary is already replaced with Russian by _enrich_alert_for_telegram
            summary_raw = _h(data.get("summary", "").strip())
            
            # Map channel to A tag if URL is present
            channel_links = []
            for c in channels[:10]:
                c_html = _h(c)
                if c in source_urls:
                    channel_links.append(f'<a href="{source_urls[c]}">{c_html}</a>')
                else:
                    channel_links.append(f"{c_html}")
            channels_str = " ".join(channel_links)

            message = (
                f"🔥 HOT TREND\n\n"
                f"<b>{headline}</b>\n"
                f"Score: {score:.1f} | Channels: {sources}\n\n"
                f"<blockquote expandable>{summary_raw}</blockquote>"
            )
            if channels_str:
                message += f"\n\nИсточники: {channels_str}"

        elif event_type == "trend_alert":
            # Старый trend_alert (из route_event) — тоже переводим на HTML
            topic = _h(data.get("topic", ""))
            score = data.get("score", 0)
            sources_count = data.get("sources", 0)
            summary_raw = _h(data.get("summary", "").strip())

            message = (
                f"📈 <b>{topic}</b>\n"
                f"Score: {score:.1f} | Каналов: {sources_count}\n\n"
                f"<blockquote expandable>{summary_raw}</blockquote>"
            )

        elif event_type == "digest":
            # Дайджест отправляется отдельно со своим parse_mode
            message = data.get("text", "")
            parse_mode = data.get("parse_mode", "Markdown")

        elif event_type == "subscription_match":
            query = _h(data.get("query", ""))
            summary_raw = _h(data.get("summary", "").strip())
            source = data.get("source", "")

            message = (
                f"🔔 <b>Совпадение: {query}</b>\n\n"
                f"<blockquote expandable>{summary_raw}</blockquote>\n\n"
                f'<a href="https://t.me/{source}">источник</a>'
            )

        else:
            return

        async with httpx.AsyncClient() as client:
            for uid in allowed_users:
                uid = uid.strip()
                if not uid:
                    continue
                try:
                    resp = await client.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={
                            "chat_id": uid,
                            "text": message,
                            "parse_mode": parse_mode,
                            "link_preview_options": {"is_disabled": True},
                        },
                    )
                    self._log_dispatch(event_type, "fallback_telegram", "ok", message[:2000])
                except Exception as e:
                    logger.error(f"Telegram fallback failed for {uid}: {e}")
                    self._log_dispatch(event_type, "fallback_telegram", "error", message[:300])


    async def _store_embedding(
        self,
        conn: sqlite3.Connection,  # already-open SQLite connection from the caller
        message_id: int,
        text: str,
        source_name: str,
        timestamp: str,
        temperature: float,
        topic: str,
    ) -> None:
        """
        Compute BGE-m3 embedding and store in ChromaDB.
        Uses the caller's open connection to mark chroma_synced=1
        (avoids opening a second connection which causes SQLite write lock).

        On success: sets messages.chroma_synced=1 so TrendTracker can find
        this message in its clustering cycle.
        Best-effort: if ChromaDB is down, logs a warning and continues.
        """
        try:
            loop = asyncio.get_running_loop()
            # sentence-transformers encode() is CPU-bound → run in thread pool
            embedding = await loop.run_in_executor(
                None,
                self.embedder.encode,
                text,
            )
            self.chroma.add_message(
                message_id=message_id,
                embedding=embedding,
                text=text,
                source_name=source_name,
                timestamp=timestamp,
                temperature=temperature,
                topic=topic,
            )

            # Mark synced using the caller's connection (no second conn needed)
            conn.execute(
                "UPDATE messages SET chroma_synced=1 WHERE id=?",
                (message_id,)
            )
            logger.debug(f"Stored embedding for message {message_id} in ChromaDB")

        except Exception as e:
            logger.warning(f"Failed to store embedding for message {message_id}: {e}")

    async def _analyze_message(
        self,
        message_id: int,
        text: str,
        source_name: str,
    ) -> dict[str, Any] | None:
        """Send a single message to the LLM for analysis.

        Thinking mode is read hot from settings.json (llm_thinking_mode):
          'full' — full reasoning, best quality (~15-20s/msg)
          'off'  — no reasoning, ~8-16x faster but lower quality
        Digest LLM calls always use full thinking regardless of this setting.
        """
        prompt = SINGLE_MESSAGE_PROMPT.format(
            source_name=source_name,
            text=text[:2000],  # truncate very long messages
        )

        # Hot-reload: re-read thinking mode on every analysis cycle
        thinking_mode = self.cfg.get("llm_thinking_mode", "full") if self.cfg else "full"
        disable_thinking = (thinking_mode == "off")
        if disable_thinking:
            logger.debug(f"Message {message_id}: thinking disabled (llm_thinking_mode=off)")

        try:
            result = await self.llm.complete_json(
                user_prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                temperature=0.1,
                disable_thinking=disable_thinking,
                # max_tokens не задан → используется дефолт 4096 (достаточно для reasoning + JSON)
            )

            # Clamp temperature to valid range
            if "temperature" in result:
                result["temperature"] = max(1.0, min(10.0, float(result["temperature"])))

            return result

        except Exception as e:
            logger.error(f"LLM analysis failed for message {message_id}: {e}")
            return None

    def _normalize_topic(self, raw_topic: str) -> str:
        """
        Map the LLM-returned topic label to a canonical name using topics.json aliases.

        Example: "BTC", "биткоин", "btc price" → "bitcoin"

        Reloads topics on every call when cfg is available so topics.json
        hot-reload takes effect within the next analysis batch.
        """
        if self.cfg:
            self._topics = self.cfg.load_topics()

        if not self._topics or not raw_topic:
            return (raw_topic or "general").lower().strip()

        lower = raw_topic.lower().strip()

        for canonical, meta in self._topics.items():
            if lower == canonical:
                return canonical
            aliases = [a.lower() for a in meta.get("aliases", [])]
            if lower in aliases:
                return canonical

        return lower  # unknown topic — keep as-is

    def _is_alert_topic(self, topic: str) -> bool:
        """Return True if topic is marked alert:true in topics.json."""
        meta = self._topics.get(topic, {})
        return bool(meta.get("alert", False))

    def _is_heuristic_ad(self, text: str) -> bool:
        """
        Fast keyword-based ad detector — runs BEFORE the LLM to save tokens.

        Returns True if the message text contains any known ad/promo signal.
        Reads config hot from settings.json (ad_filter block):
          - ad_filter.enabled          — master switch (default True)
          - ad_filter.use_heuristic    — enable this method (default True)
          - ad_filter.heuristic_keywords — list of keywords/substrings to match

        All checks are case-insensitive.
        """
        if not self.cfg:
            return False

        ad_cfg = self.cfg.get("ad_filter", {})
        if not ad_cfg.get("enabled", True):
            return False
        if not ad_cfg.get("use_heuristic", True):
            return False

        keywords: list[str] = ad_cfg.get("heuristic_keywords", [])
        if not keywords:
            return False

        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in keywords)


    def _pipeline_config(self) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "categories": {}, "digests": [], "analysis_profile": "crypto",
            "digest_template": "classic", "llm_concurrency": 3,
        }
        return {key: self.cfg.get(key, value) if self.cfg else value
                for key, value in defaults.items()}

    @staticmethod
    def _source_filter(sources: Sequence[str]) -> tuple[str, tuple[str, ...]]:
        if not sources:
            return "", ()
        return "AND s.type IN (" + ",".join("?" for _ in sources) + ")", tuple(sources)

    def _digest_since(self, hours: int | None, name: str | None) -> datetime:
        if hours is not None:
            return datetime.utcnow() - timedelta(hours=hours)
        conn = get_db(self.db_path)
        try:
            where = " WHERE name = ?" if name is not None else ""
            row = conn.execute(
                "SELECT period_end FROM digests" + where + (" ORDER BY created_at DESC, id DESC LIMIT 1" if name is not None
                 else " ORDER BY created_at DESC LIMIT 1"),
                (name,) if name is not None else (),
            ).fetchone()
            return datetime.fromisoformat(row["period_end"]) if row else datetime.utcnow() - timedelta(hours=12)
        except (sqlite3.Error, ValueError, TypeError):
            return datetime.utcnow() - timedelta(hours=12)
        finally:
            conn.close()

    async def generate_digest(self, hours: int | None = None, force: bool = False, return_raw: bool = False) -> str | None:
        cfg = self._pipeline_config()
        if cfg["digests"]:
            parts = await self.run_digest(None, hours, force, return_raw)
            return parts[0].result if parts else None
        return await self.run_category(
            resolve_legacy(cfg, self.llm.router is not None, warn_on_fallback=False),
            None, hours, force, return_raw,
        )

    async def run_digest(self, name: str | None, hours: int | None = None,
                         force: bool = False, return_raw: bool = False) -> list[DigestPart]:
        cfg = self._pipeline_config()
        digest = resolve_digest(cfg, name)
        if not cfg["digests"]:
            result = await self.generate_digest(hours, force, return_raw)
            return [DigestPart("crypto", result, self._last_digest_id)] if result else []
        if digest is None or not digest.enabled:
            return []
        categories = {cat.name: cat for cat in load_categories(cfg, self.llm.router is not None)}
        # Freeze the window before the first category writes its snapshot.
        since = self._digest_since(hours, digest.name)
        parts: list[DigestPart] = []
        for category_name in dict.fromkeys(digest.categories):
            cat = categories.get(category_name)
            if cat is None:
                logger.warning("Digest %s: category %s disabled, missing or unavailable", digest.name, category_name)
                continue
            result = await self.run_category(cat, digest.name, hours, force, return_raw, _since=since)
            if result:
                parts.append(DigestPart(cat.name, result, self._last_digest_id))
        return parts

    async def run_category(self, cat: CategorySpec, digest_name: str | None,
                           hours: int | None = None, force: bool = False,
                           return_raw: bool = False, *, _since: datetime | None = None) -> str | None:
        """Select and render one category, preserving the legacy pipeline order."""
        self._last_digest_id: int | None = None
        spec = cat
        template_name = cat.template
        rules = self.cfg.get("digest_rules", {}) if self.cfg else {}
        templates_all = self.cfg.get("digest_templates", {}) if self.cfg else {}
        template_cfg: dict[str, Any] = {**templates_all.get(template_name, {}), **cat.params}
        source_filter, source_params = self._source_filter(cat.sources)

        # Per-template settings (fall back to global keys)
        digest_max = template_cfg.get(
            "max_items",
            8 if template_name == "ai_value" else (self.cfg.get("digest_max_items", 7) if self.cfg else 7)
        )
        min_temp = template_cfg.get(
            "min_temperature",
            self.cfg.get("digest_min_temperature", 5.0) if self.cfg else 5.0
        )

        max_per_topic     = rules.get("max_per_topic", 2)
        include_alerts    = rules.get("always_include_alerts", True)
        trend_src_min     = rules.get("min_unique_sources_for_trend", 3)
        dedup_threshold   = rules.get("dedup_threshold", 0.85)

        conn = get_db(self.db_path)
        try:
            conn.execute("UPDATE messages SET in_digest=0 WHERE in_digest=2")
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to reset pending digests: {e}")

        since = _since if _since is not None else self._digest_since(hours, digest_name)

        # Safety cap: never look back more than 24h to avoid LLM context overflow
        max_lookback = datetime.utcnow() - timedelta(hours=24)
        if since < max_lookback:
            logger.warning(f"period_end is too old ({since.isoformat()}), capping since to 24h ago")
            since = max_lookback

        in_digest_filter = "" if force else "AND m.in_digest = 0"

        try:
            rows = conn.execute(f"""
                SELECT
                    m.id,
                    m.external_id,
                    m.text,
                    m.url,
                    m.collected_at,
                    s.type AS source_type,
                    a.takeaway,
                    a.md_path,
                    s.name  AS source_name,
                    a.temperature,
                    a.topic,
                    a.summary,
                    a.content_type,
                    a.value_score,
                    -- is this message part of a hot trend?
                    EXISTS (
                        SELECT 1 FROM trend_messages tm
                        JOIN trends t ON t.id = tm.trend_id
                        WHERE tm.message_id = m.id
                          AND t.unique_sources >= ?
                          AND t.status IN ('emerging', 'hot')
                    ) AS in_hot_trend,
                    (m.alerted_at IS NOT NULL) AS was_alerted
                FROM messages m
                JOIN sources s ON m.source_id = s.id
                LEFT JOIN analysis a ON a.message_id = m.id
                WHERE datetime(m.collected_at) >= datetime(?)
                  AND m.analyzed = 1
                  AND (m.is_ad = 0 OR m.is_ad IS NULL)
                  {in_digest_filter}
                  {source_filter}
                  AND a.temperature IS NOT NULL
                ORDER BY a.temperature DESC
                LIMIT 100
            """, (trend_src_min, since.isoformat(), *source_params)).fetchall()
        finally:
            conn.close()

        if not rows:
            logger.warning("No analyzed messages available for digest")
            return None

        # ── Semantic dedup via ChromaDB FIRST ──
        rows_dicts = [dict(r) for r in rows]
        if dedup_threshold < 1.0:
            rows_dicts = self._dedup_by_similarity(rows_dicts, threshold=dedup_threshold)

        digest_ctx = DigestContext(
            analyzer=self, cfg={"digest_template": template_name,
                                "knowledge": self.cfg.get("knowledge", {}) if self.cfg else {},
                                "llm_concurrency": self.cfg.get("llm_concurrency", 3) if self.cfg else 3},
            rules=rules, template_name=template_name,
            template_cfg=template_cfg, digest_max=digest_max, min_temp=min_temp,
            since=since, force=force, return_raw=return_raw, params=cat.params,
        )
        selected = SELECTORS.get(spec.select)(rows_dicts, digest_ctx)
        alerts_count, trends_count, high_count = digest_ctx.artifacts["tier_counts"]

        if not selected:
            logger.warning("Digest priority queue produced 0 candidates")
            return None

        # ── Cross-digest dedup: driven by template flags ──
        use_cross_dedup    = template_cfg.get("cross_dedup", True)
        use_ongoing_trends = template_cfg.get("ongoing_trends", True)
        lookback_digests   = template_cfg.get("lookback_digests", 2)

        ongoing_trends: list[dict[str, Any]] = []
        if use_cross_dedup and use_ongoing_trends and lookback_digests > 0:
            cross_dedup_threshold = rules.get("cross_dedup_threshold", 0.75)
            selected, ongoing_trends = self._dedup_against_previous_digests(
                selected, lookback=lookback_digests, threshold=cross_dedup_threshold,
                digest_name=digest_name,
            )
        elif use_cross_dedup and lookback_digests > 0:
            # Dedup but don't pass ongoing trends to the LLM
            cross_dedup_threshold = rules.get("cross_dedup_threshold", 0.75)
            selected, _ = self._dedup_against_previous_digests(
                selected, lookback=lookback_digests, threshold=cross_dedup_threshold,
                digest_name=digest_name,
            )

        logger.info(
            f"Digest: {alerts_count} alerts, {trends_count} trend msgs, "
            f"{high_count} high-temp → {len(selected)} selected after dedup"
        )

        # ── Emotional balance: if previous digest started with negative alerts,
        # push negative items past position 2 so digest opens with neutral/positive news ──
        negative_keywords = [kw.lower() for kw in (self.cfg.get("keywords_alert", []) if self.cfg else [])]
        # Named digests opt out per template (articles have no alert keywords worth balancing).
        if negative_keywords and (digest_name is None or template_cfg.get("emotional_balance", True)):
            def _is_negative(item: dict[str, Any]) -> bool:
                text = ((item.get("topic") or "") + " " + (item.get("text") or "")).lower()
                return any(kw in text for kw in negative_keywords)

            # Check if last digest's top-2 were negative (via in_digest messages)
            prev_was_negative = False
            try:
                conn2 = get_db(self.db_path)
                prev_top = conn2.execute(f"""
                    SELECT a.topic, m.text FROM messages m
                    JOIN sources s ON s.id = m.source_id
                    LEFT JOIN analysis a ON a.message_id = m.id
                    WHERE m.in_digest = 1 {source_filter}
                    ORDER BY m.id DESC LIMIT 2
                """, source_params).fetchall()
                conn2.close()
                prev_was_negative = any(_is_negative(dict(r)) for r in prev_top)
            except Exception:
                pass

            if prev_was_negative:
                negative_items = [i for i in selected if _is_negative(i)]
                non_negative   = [i for i in selected if not _is_negative(i)]
                # Keep first 2 slots for non-negative, then interleave rest
                selected = non_negative[:2] + negative_items + non_negative[2:]

        # For Agent mode, strictly enforce the max limit after dedup.
        # For Legacy Mode, we KEEP the oversized `selected` list and let the LLM prune it.
        if return_raw:
            selected = selected[:digest_max]

        # ── Build LLM prompt ──
        # Build post URLs for source linking in the digest
        def post_url(row: dict[str, Any]) -> str:
            ext_id = str(row.get("external_id", "") or "")
            src = str(row.get("source_name", "") or "")
            if not src:
                return ""
            if ext_id and src.replace("-", "").isdigit():
                clean_id = src.replace("-100", "", 1).replace("-", "")
                return f"https://t.me/c/{clean_id}/{ext_id}"
            elif ext_id:
                return f"https://t.me/{src}/{ext_id}"
            return f"https://t.me/{src}"

        messages_text = "\n\n".join([
            f"[{i+1}] Channel: @{row['source_name']} | Temperature: {row['temperature']}/10\n"
            f"Topic: {row['topic']}\n"
            f"Text: {row['text'][:300]}"
            for i, row in enumerate(selected)
        ])
        
        source_map = {str(i+1): post_url(dict(row)) for i, row in enumerate(selected)}

        if template_name == "ai_value":
            source_map = {
                str(i): str(row.get("url") or (post_url(row) if row.get("source_type") == "telegram" else ""))
                for i, row in enumerate(selected, 1)
            }

        period = "последнее время"

        # Build ongoing trends section for the LLM if any carried-over topics detected
        if ongoing_trends:
            lines = ["\n--- ONGOING TRENDS (topics continuing from previous digest — synthesize as update) ---"]
            # Group by topic to avoid repeating the same trend multiple times
            seen_trend_topics: set[str] = set()
            for t in ongoing_trends:
                topic = t["topic"]
                if topic not in seen_trend_topics:
                    seen_trend_topics.add(topic)
                    # Collect all summaries for this topic
                    topic_summaries = [
                        t2["summary"] for t2 in ongoing_trends
                        if t2["topic"] == topic and t2["summary"]
                    ]
                    lines.append(f"\nTOPIC: {topic}")
                    for i, s in enumerate(topic_summaries[:3], 1):
                        lines.append(f"  Update {i}: {s}")
            lines.append("--- END ONGOING TRENDS ---\n")
            ongoing_trends_section = "\n".join(lines)
        else:
            ongoing_trends_section = "\n"

        prompt = DIGEST_PROMPT.format(
            period=period,
            count=len(selected),
            messages=messages_text,
            ongoing_trends_section=ongoing_trends_section,
            digest_max=digest_max,
        )

        if return_raw:
            # ── Pull Architecture: Mark in DB as pending and return raw text immediately ──
            try:
                conn = get_db(self.db_path)
                selected_ids = [row["id"] for row in selected]
                conn.executemany("UPDATE messages SET in_digest=2 WHERE id=?", [(mid,) for mid in selected_ids])
                
                if selected_ids:
                    placeholders = ",".join("?" * len(selected_ids))
                    conn.execute(f"""
                        UPDATE messages SET in_digest=2
                        WHERE id IN (SELECT m.id FROM messages m JOIN sources s ON s.id=m.source_id
                                     WHERE 1=1 {source_filter}) AND id IN (
                            SELECT tm2.message_id
                            FROM trend_messages tm1
                            JOIN trend_messages tm2 ON tm1.trend_id = tm2.trend_id
                            WHERE tm1.message_id IN ({placeholders})
                        )
                    """, [*source_params, *selected_ids])
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"Failed to finalise raw digest DB state: {e}")
            return messages_text

        # ── 1. Agent Mode: Push raw payload to OpenClaw ──
        agent_succeeded = False
        route_enabled = self.cfg.get("route_via_openclaw", False) if self.cfg else False

        if route_enabled:
            webhook_url = (os.getenv("OPENCLAW_WEBHOOK_URL") or os.getenv("OPENCLAW_API_URL") or "").strip()

            if webhook_url.endswith("/hooks/wake"):
                webhook_url = webhook_url.replace("/hooks/wake", "/v1/chat/completions")
            elif not webhook_url.endswith("/v1/chat/completions"):
                webhook_url = "http://openclaw:18789/v1/chat/completions"

            token = (os.getenv("OPENCLAW_WEBHOOK_TOKEN") or os.getenv("OPENCLAW_API_TOKEN") or "").strip()
            headers = {"Authorization": f"Bearer {token}"} if token else {}

            payload_text = (
                f"[NEWS-RADAR EVENT: digest_raw]\n"
                f"Period: {period}\n"
                f"Messages: {len(selected)}\n\n"
                f"{messages_text}\n\n"
                f"Action: Generate a markdown digest based on these messages and send it to the user. Do not return the raw messages."
            )
            payload = {
                "model": "main",
                "messages": [
                    {"role": "system", "content": "You are the RoutingAgent. Process this event according to AGENTS.md instructions."},
                    {"role": "user", "content": payload_text}
                ]
            }
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(webhook_url, json=payload, headers=headers)
                    resp.raise_for_status()
                    agent_succeeded = True
                    logger.info("Successfully dispatched raw digest data to OpenClaw Agent")
            except Exception as e:
                logger.error(f"Failed to dispatch raw digest to Agent: {e}")

        # ── 2. Legacy Mode: Local LLM generates digest directly ──
        digest_content = ""
        parse_mode = "Markdown"
        if not agent_succeeded:
            if route_enabled:
                logger.error("Agent digest push failed and legacy fallback is disabled when route_via_openclaw=true.")
                return None
            # route_via_openclaw=false → use local LLM
            with LLMLock():
                try:
                    digest_ctx.artifacts.update({
                        "source_map": source_map, "prompt": prompt, "period": period,
                        "messages_text": messages_text,
                    })
                    writer = WRITERS.get(spec.template if spec.template in WRITERS.names() else "classic")
                    draft = await writer.compose(selected, digest_ctx)
                    # Extras (e.g. knowledge md) run after the draft, so a failed draft costs nothing extra,
                    # and before the render, so the render can link their results.
                    for extra_name in spec.extras:
                        await EXTRAS.get(extra_name)(selected, digest_ctx)
                    digest_content, parse_mode = writer.render(draft, selected, digest_ctx)
                except Exception as e:
                    logger.error(f"Local LLM digest generation failed: {e}")
                    return None

        # ── 3. Mark in DB ──
        try:
            conn = get_db(self.db_path)
            try:
                if digest_content:
                    cursor = conn.execute(
                        "INSERT INTO digests (content_md, parse_mode, period_start, period_end, name, category) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (digest_content, parse_mode, since.isoformat(), datetime.utcnow().isoformat(),
                         digest_name, cat.name if digest_name is not None else None),
                    )
                    self._last_digest_id = cursor.lastrowid

                selected_ids = [row["id"] for row in selected]
                conn.executemany(
                    "UPDATE messages SET in_digest=1 WHERE id=?",
                    [(mid,) for mid in selected_ids]
                )

                if selected_ids:
                    placeholders = ",".join("?" * len(selected_ids))
                    conn.execute(f"""
                        UPDATE messages SET in_digest=1
                        WHERE id IN (SELECT m.id FROM messages m JOIN sources s ON s.id=m.source_id
                                     WHERE 1=1 {source_filter}) AND id IN (
                            SELECT tm2.message_id
                            FROM trend_messages tm1
                            JOIN trend_messages tm2 ON tm1.trend_id = tm2.trend_id
                            WHERE tm1.message_id IN ({placeholders})
                        )
                    """, [*source_params, *selected_ids])

                conn.commit()
            finally:
                conn.close()

            # ── 4. Return Output ──
            if agent_succeeded:
                return "dispatched"
            else:
                return digest_content

        except Exception as e:
            logger.error(f"Failed to finalise digest DB state: {e}")
            return None

    def _dedup_by_similarity(self, candidates: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
        """
        Remove semantically near-duplicate messages using ChromaDB cosine similarity.

        For each candidate we query ChromaDB for similar messages already in
        the 'selected so far' set. If similarity > threshold → skip.
        Falls back to returning candidates as-is if ChromaDB is unavailable.
        """
        try:
            self.chroma._connect()
            unique: list[dict[str, Any]] = []
            kept_ids: set[str] = set()

            for msg in candidates:
                msg_id = str(msg["id"])
                if msg_id in kept_ids:
                    continue

                # Find documents in ChromaDB that are similar to this one
                vector = self.embedder.encode(msg["text"][:512])
                collection = self.chroma._collection
                if collection is None:
                    return candidates
                result = collection.query(
                    query_embeddings=[vector],
                    n_results=min(10, max(1, len(candidates))),
                    include=["distances"],
                )
                # ChromaDB returns L2 distances; convert to cosine similarity
                # For normalized embeddings: cosine_sim ≈ 1 - (L2² / 2)
                distances = (result.get("distances") or [[]])[0]
                ids       = (result.get("ids") or [[]])[0]

                is_dup = False
                for sim_id, dist in zip(ids, distances):
                    if sim_id == msg_id:
                        continue
                    cosine_sim = max(0.0, 1.0 - dist / 2.0)
                    if cosine_sim >= threshold and sim_id in kept_ids:
                        is_dup = True
                        break

                if not is_dup:
                    unique.append(msg)
                    kept_ids.add(msg_id)

            return unique

        except Exception as e:
            logger.warning(f"Semantic dedup skipped (ChromaDB unavailable): {e}")
            return candidates


    def _dedup_against_previous_digests(
        self, candidates: list[dict[str, Any]], lookback: int = 2, threshold: float = 0.75,
        digest_name: str | None = None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Semantic dedup against previous digests.

        Returns:
          - filtered: candidates that are NOT similar to previous digest stories
          - ongoing: list of dicts {topic, summary, similarity} for stories that
            ARE similar to previous digests (used by caller to inform the LLM)

        Falls back gracefully: if embedder or DB is unavailable, returns (candidates, []).
        """
        try:
            import re

            # ── 1. Load summaries from last N digests ──
            conn = get_db(self.db_path)
            rows = conn.execute(
                "SELECT content_md FROM digests" + (" WHERE name = ?" if digest_name is not None else "")
                + " ORDER BY id DESC LIMIT ?",
                (digest_name, lookback) if digest_name is not None else (lookback,)
            ).fetchall()
            conn.close()

            if not rows:
                return candidates, []

            # Extract per-story summaries from digest markdown
            past_summaries: list[str] = []
            for row in rows:
                content = row["content_md"] or ""
                blocks = re.split(r"🔹", content)
                for block in blocks[1:]:  # skip header block
                    lines = [
                        ln.strip()
                        for ln in block.splitlines()
                        if ln.strip() and not ln.strip().startswith("*") and not ln.strip().startswith("[")
                    ]
                    if lines:
                        past_summaries.append(" ".join(lines[:2]))

            if not past_summaries:
                return candidates, []

            # ── 2. Encode past summaries ──
            past_embeddings = [
                self.embedder.encode(s) for s in past_summaries
            ]

            # ── 3. Filter candidates by semantic similarity ──
            import numpy as np

            def cosine(a: Sequence[float], b: Sequence[float]) -> float:
                va, vb = np.array(a), np.array(b)
                denom = np.linalg.norm(va) * np.linalg.norm(vb)
                return float(np.dot(va, vb) / denom) if denom > 0 else 0.0

            filtered = []
            ongoing = []  # stories that match previous digest = ongoing trends
            for msg in candidates:
                candidate_text = (msg.get("summary") or msg.get("text") or "")[:300]
                cand_emb = self.embedder.encode(candidate_text)

                max_sim = max(cosine(cand_emb, p_emb) for p_emb in past_embeddings)
                if max_sim >= threshold:
                    ongoing.append({
                        "topic": msg.get("topic", "unknown"),
                        "summary": (msg.get("summary") or "")[:150],
                        "similarity": max_sim,
                    })
                    logger.debug(
                        f"Cross-digest dedup: ongoing trend '{msg.get('topic')}' "
                        f"(similarity {max_sim:.2f})"
                    )
                else:
                    filtered.append(msg)

            if ongoing:
                logger.info(
                    f"Cross-digest dedup: {len(ongoing)} ongoing trends detected, "
                    f"{len(filtered)} fresh stories kept"
                )

            return (filtered if filtered else candidates), ongoing

        except Exception as e:
            logger.warning(f"Cross-digest semantic dedup skipped: {e}")
            return candidates, []


    def _pending_count(self) -> int:
        cfg = self._pipeline_config()
        sources = tuple(source for cat in load_categories(cfg, self.llm.router is not None)
                        for source in cat.sources) if cfg["categories"] else ()
        if cfg["categories"] and not sources:
            return 0
        source_filter, source_params = self._source_filter(sources)
        min_len = int(self.cfg.get("min_message_length", 30)) if self.cfg else 30
        conn = get_db(self.db_path)
        try:
            return int(conn.execute(
                "SELECT COUNT(*) FROM messages m JOIN sources s ON s.id=m.source_id "
                "WHERE m.analyzed = 0 AND length(m.text) >= ? " + source_filter,
                (min_len, *source_params),
            ).fetchone()[0])
        finally:
            conn.close()

    def _trend_sources(self) -> tuple[str, ...] | None:
        cfg = self._pipeline_config()
        if not cfg["categories"]:
            return None
        return tuple(dict.fromkeys(source for cat in load_categories(cfg, self.llm.router is not None)
                                   if "trends" in cat.hooks for source in cat.sources))

    async def run_loop(self) -> None:
        """
        Main loop — runs things on different schedules:
          1. LLM analysis of new messages (every ANALYZE_INTERVAL_MINUTES)
          2. TrendTracker clustering cycle (every TREND_INTERVAL_MINUTES, default 15 min)

        Both tasks share the same ChromaDB client and embedder instance.
        TrendTracker is non-blocking: if it fails, analysis continues.
        """
        trend_interval = int(os.environ.get("TREND_INTERVAL_MINUTES", "15")) * 60
        last_trend_run = datetime.utcnow() - timedelta(seconds=trend_interval)  # run immediately on start
        messages_analyzed_since_trend = 0

        # Shared TrendTracker instance (reuses self.chroma, self.llm)
        trend_tracker = TrendTracker(
            db_path=self.db_path,
            llm_client=self.llm,
            chroma_client=self.chroma,
            analyzer=self,
        )

        cfg = self._pipeline_config()
        if cfg["categories"]:
            conn = get_db(self.db_path)
            try:
                warn_uncategorized_sources(conn, load_categories(cfg))
            finally:
                conn.close()

        logger.info(
            f"Analyzer loop started — "
            f"analysis every {self.interval // 60} min, "
            f"trends every {trend_interval // 60} min"
        )

        while True:
            # ─── LLM analysis cycle ───
            try:
                analyzed_count = await self.analyze_pending()
                if analyzed_count > 0:
                    messages_analyzed_since_trend += analyzed_count
            except Exception as e:
                logger.error(f"Analyzer loop error: {e}")

            # ─── TrendTracker cycle (Time-based OR Event-based) ───
            now = datetime.utcnow()
            time_elapsed = (now - last_trend_run).total_seconds() >= trend_interval
            
            threshold = int((self.cfg.get("trend_messages_threshold", 20)) if self.cfg else 20)
            threshold_met = (messages_analyzed_since_trend >= threshold)

            if time_elapsed or threshold_met:
                trigger_reason = "threshold met" if threshold_met else "timer elapsed"
                logger.info(f"Triggering TrendTracker run_cycle ({trigger_reason}). Analyzed since last run: {messages_analyzed_since_trend}")
                try:
                    source_types = self._trend_sources()
                    trend_tracker.source_types = source_types
                    if source_types == ():
                        logger.debug("Trend cycle skipped: no enabled category has trends hook")
                    else:
                        await trend_tracker.run_cycle()
                    last_trend_run = now
                    messages_analyzed_since_trend = 0
                except Exception as e:
                    logger.error(f"TrendTracker cycle error: {e}")
                    last_trend_run = now  # don't retry immediately on error
                    messages_analyzed_since_trend = 0
                    

            slept = 0
            while slept < self.interval:
                await asyncio.sleep(10)
                slept += 10
                
                # Wake up early if too many messages accumulated
                max_pending = int((self.cfg.get("analyze_max_pending", 10)) if self.cfg else 10)
                if max_pending > 0:
                    try:
                        count = self._pending_count()
                        if count >= max_pending:
                            logger.info(f"Threshold reached ({count} pending >= {max_pending}), waking up early")
                            break
                    except Exception as e:
                        logger.debug(f"Error checking pending count: {e}")


async def main() -> None:
    """Docker entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    db_path = os.environ.get("DATABASE_PATH", "/app/data/news.db")
    interval = int(os.environ.get("ANALYZE_INTERVAL_MINUTES", "30"))

    init_db(db_path)

    from analyzer.llm_client import build_llm_client

    llm = build_llm_client(timeout=300)  # 5 min — covers digest with full thinking

    logger.info(f"Checking LLM at {llm.base_url}...")
    if await llm.health_check():
        logger.info("LLM is available")
    else:
        logger.warning("LLM not available yet — will retry on each cycle")

    cfg = ConfigWatcher("/app/config/settings.json")

    # ТЗ #4 И1: llm_local_mode toggles LLMLock/chat_template_kwargs (local-GPU-only
    # behaviors). Default True preserves current prod behavior. Hot-reload follows
    # the same cfg.on_change pattern used elsewhere (e.g. collectors/telegram.py) —
    # note this only takes effect if something in this process also runs cfg.watch().
    if llm.is_legacy:
        set_local_mode(cfg.get("llm_local_mode", True))
        cfg.on_change("llm_local_mode", set_local_mode)

    analyzer = NewsAnalyzer(
        db_path=db_path,
        llm_client=llm,
        interval_minutes=interval,
        cfg=cfg,
    )

    await asyncio.gather(cfg.watch(), analyzer.run_loop())


if __name__ == "__main__":
    asyncio.run(main())
