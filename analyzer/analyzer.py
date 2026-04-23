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
import httpx
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzer.llm_client import LLMClient, is_llm_locked, LLMLock
from analyzer.prompts import (
    SINGLE_MESSAGE_PROMPT, DIGEST_PROMPT, DIGEST_PROMPT_SPOILER,
    DIGEST_SPOILER_MERGE_ON, DIGEST_SPOILER_MERGE_OFF,
    SYSTEM_PROMPT,
)
from analyzer.renderer import render_digest
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
        self._topics: dict = cfg.load_topics() if cfg else {}
        if self._topics:
            logger.info(f"Loaded {len(self._topics)} topic aliases from topics.json")

    async def analyze_pending(self) -> int:
        """
        Process all unanalyzed messages.
        Returns the number of messages successfully analyzed.
        """
        conn = get_db(self.db_path)
        count = 0

        # Don't start analyzing if the LLM is busy (e.g. generating a digest)
        if is_llm_locked():
            logger.info("analyze_pending: LLM is locked (digest generation in progress). Pausing...")
            return 0

        try:
            # Load active subscriptions for real-time alerting
            active_subs = conn.execute("SELECT user_id, query FROM subscriptions WHERE active=1").fetchall()
            subs_list = [{"user_id": s["user_id"], "query": s["query"], "q_lower": s["query"].lower()} for s in active_subs]
        except Exception:
            subs_list = []

        try:
            min_len = int(self.cfg.get("min_message_length", 30)) if self.cfg else 30
            # PRIORITY QUEUE: sort by views and length instead of just time
            rows = conn.execute("""
                SELECT m.id, m.text, s.name as source_name, m.collected_at, m.views, m.forwards
                FROM messages m
                JOIN sources s ON m.source_id = s.id
                WHERE m.analyzed = 0
                  AND length(m.text) >= ?
                ORDER BY COALESCE(m.views, 0) DESC, length(m.text) DESC, m.collected_at DESC
                LIMIT ?
            """, (min_len, self.batch_size,)).fetchall()

            if not rows:
                logger.debug("No pending messages to analyze")
                return 0

            # Convert to list of dicts for safe async access
            pending_batch = [dict(r) for r in rows]

            logger.info(f"Analyzing {len(pending_batch)} pending messages (Priority Queue)...")

            # Semaphore for concurrent batching (LLM parallelism)
            concurrency = int(self.cfg.get("llm_concurrency", 3)) if self.cfg else 3
            sem = asyncio.Semaphore(concurrency)

            async def process_row(row: dict):
                async with sem:
                    # 1. Pre-flight Semantic Deduplication
                    embedding = None
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
                                    cloned_result = {
                                        "topic": best.get("topic", "general"),
                                        "temperature": best.get("temperature", 5.0),
                                        "summary": best.get("document", row["text"][:200])[:500],
                                        "keywords": ["duplicate"],
                                        "sentiment": "neutral",
                                        "embedding": embedding
                                    }
                                    return row, cloned_result
                    except Exception as e:
                        logger.warning(f"Pre-flight deduplication failed for msg {row['id']}: {e}")

                    # 2. Actual LLM Analysis (if not duplicate)
                    try:
                        result = await self._analyze_message(
                            message_id=row["id"],
                            text=row["text"],
                            source_name=row["source_name"],
                        )
                        if result:
                            result["embedding"] = embedding
                        return row, result
                    except Exception as e:
                        logger.error(f"Analysis failed for msg {row['id']}: {e}")
                        return row, None

            # Execute all tasks concurrently and wait
            tasks = [process_row(r) for r in pending_batch]
            results = await asyncio.gather(*tasks)

            # 3. Transactional Database Write Layer (Sequential)
            for row, result in results:
                try:
                    if result and result.get("summary"):
                        # Normalize 
                        raw_topic = result.get("topic", "general")
                        normalized_topic = self._normalize_topic(raw_topic)

                        conn.execute("""
                            INSERT INTO analysis
                                (message_id, temperature, topic, summary, keywords, sentiment)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            row["id"],
                            result.get("temperature", 5.0),
                            normalized_topic,
                            result.get("summary", ""),
                            json.dumps(result.get("keywords", []), ensure_ascii=False),
                            result.get("sentiment", "neutral"),
                        ))

                        # Use pre-computed embedding if available, otherwise compute sync
                        emb = result.get("embedding")
                        if emb is None:
                            emb = self.embedder.encode(row["text"])

                        # Add to Chroma
                        self.chroma.add_message(
                            message_id=row["id"],
                            embedding=emb,
                            text=row["text"],
                            source_name=row["source_name"],
                            timestamp=row.get("collected_at", datetime.utcnow().isoformat()),
                            temperature=result.get("temperature", 5.0),
                            topic=normalized_topic,
                        )

                        # Commit message
                        conn.execute("UPDATE messages SET analyzed=1 WHERE id=?", (row["id"],))
                        conn.commit()
                        count += 1

                        # Alerts and Subs ...
                        temp = float(result.get("temperature", 5.0))
                        min_alert_temp = float(self.cfg.get("breaking_alert_min_temp", 10) if self.cfg else 10)
                        if temp >= min_alert_temp and self.cfg and self.cfg.get("instant_alerts_temperature", True):
                            asyncio.create_task(
                                self._send_instant_alert(row["id"], row["source_name"], temp, raw_topic, result.get("summary", ""), row["text"])
                            )

                        if subs_list:
                            text_lower = row["text"].lower()
                            summary_lower = result.get("summary", "").lower()
                            for sub in subs_list:
                                if sub["q_lower"] in text_lower or sub["q_lower"] in summary_lower:
                                    asyncio.create_task(
                                        self._route_event("subscription_match", {
                                            "user_id": sub["user_id"],
                                            "query": sub["query"],
                                            "summary": result.get("summary", ""),
                                            "source": row["source_name"],
                                            "text": row["text"][:300]
                                        })
                                    )
                    else:
                        logger.warning(f"Message {row['id']}: Empty result, will retry.")
                        conn.rollback()

                except Exception as e:
                    logger.error(f"Failed DB write for message {row['id']}: {e}")
                    try:
                        conn.rollback()
                    except Exception:
                        pass

        finally:
            conn.close()

        logger.info(f"Analyzed {count} messages")
        return count

    async def _send_instant_alert(self, msg_id: int, source: str, temp: float, topic: str, summary: str, text: str):
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

    async def _route_event(self, event_type: str, data: dict):
        """
        Route an event to OpenClaw via OpenAI-compatible completion endpoint.

        We format each event as readable text so the agent understands the context.

        Toggle: config/settings.json -> "route_via_openclaw": true  (hot-reload)
        """
        webhook_url = (os.getenv("OPENCLAW_WEBHOOK_URL") or os.getenv("OPENCLAW_API_URL", "")).strip()
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

            token = (os.getenv("OPENCLAW_WEBHOOK_TOKEN") or os.getenv("OPENCLAW_API_TOKEN", "")).strip()
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
                      payload_preview: str = "", http_status: int = None) -> None:
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

    async def _enrich_alert_for_telegram(self, event_type: str, data: dict) -> dict:
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

    async def _fallback_telegram(self, event_type: str, data: dict):
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
            # summary is already replaced with Russian by _enrich_alert_for_telegram
            summary_raw = _h(data.get("summary", "").strip())
            channels_str = " ".join(f"@{_h(c)}" for c in channels[:10])

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
                    await client.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={
                            "chat_id": uid,
                            "text": message,
                            "parse_mode": parse_mode,
                            "link_preview_options": {"is_disabled": True},
                        },
                    )
                    self._log_dispatch(event_type, "fallback_telegram", "ok", message[:300])
                except Exception as e:
                    logger.error(f"Telegram fallback failed for {uid}: {e}")
                    self._log_dispatch(event_type, "fallback_telegram", "error", message[:300])

    async def _store_embedding(
        self,
        conn,              # already-open SQLite connection from the caller
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
    ) -> dict | None:
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

    async def generate_digest(self, hours: int | None = None, force: bool = False, return_raw: bool = False) -> str | None:
        """
        Generate a digest using a 4-tier priority queue.

        Priority tiers:
          1. ALERTS  — hack/scam or temperature >= 9  (always first)
          2. TRENDS  — messages belonging to hot trends (unique_sources >= threshold)
          3. HIGH    — temperature >= digest_min_temperature + 2, diverse sources
          4. FILL    — best remaining message per topic for diversity

        Rules (from settings.json digest_rules, hot-reloaded):
          max_per_topic       — cap per unique topic
          always_include_alerts — inserts alerts regardless of cap
          dedup_threshold     — cosine similarity threshold for semantic dedup
          digest_max_items    — total cap

        force=True: bypass in_digest filter (for manual /digest new command).
        """
        # ── Load template config (hot-reload) ──
        template_name = "classic"
        template_cfg  = {}
        rules         = {}
        if self.cfg:
            rules         = self.cfg.get("digest_rules", {})
            template_name = self.cfg.get("digest_template", "classic")
            templates_all = self.cfg.get("digest_templates", {})
            template_cfg  = templates_all.get(template_name, {})

        # Per-template settings (fall back to global keys)
        digest_max = template_cfg.get(
            "max_items",
            self.cfg.get("digest_max_items", 7) if self.cfg else 7
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

        if hours is not None:
            since = datetime.utcnow() - timedelta(hours=hours)
        else:
            try:
                last_row = conn.execute("SELECT period_end FROM digests ORDER BY created_at DESC LIMIT 1").fetchone()
                if last_row:
                    since = datetime.fromisoformat(last_row["period_end"])
                else:
                    since = datetime.utcnow() - timedelta(hours=12)
            except Exception:
                since = datetime.utcnow() - timedelta(hours=12)

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
                    s.name  AS source_name,
                    a.temperature,
                    a.topic,
                    a.summary,
                    -- is this message part of a hot trend?
                    EXISTS (
                        SELECT 1 FROM trend_messages tm
                        JOIN trends t ON t.id = tm.trend_id
                        WHERE tm.message_id = m.id
                          AND t.unique_sources >= ?
                          AND t.status IN ('emerging', 'hot')
                    ) AS in_hot_trend
                FROM messages m
                JOIN sources s ON m.source_id = s.id
                LEFT JOIN analysis a ON a.message_id = m.id
                WHERE datetime(m.collected_at) >= datetime(?)
                  AND m.analyzed = 1
                  {in_digest_filter}
                  AND a.temperature IS NOT NULL
                ORDER BY a.temperature DESC
                LIMIT 100
            """, (trend_src_min, since.isoformat())).fetchall()
        finally:
            conn.close()

        if not rows:
            logger.warning("No analyzed messages available for digest")
            return None

        # ── Semantic dedup via ChromaDB FIRST ──
        rows_dicts = [dict(r) for r in rows]
        if dedup_threshold < 1.0:
            rows_dicts = self._dedup_by_similarity(rows_dicts, threshold=dedup_threshold)

        # ── Build priority tiers ──
        alerts, trends_tier, high_tier, fill_tier = [], [], [], []
        for row in rows_dicts:
            topic = row["topic"] or "general"
            temp  = float(row["temperature"] or 5.0)
            is_alert = self._is_alert_topic(topic) or temp >= 9.0

            if is_alert and include_alerts:
                alerts.append(row)
            elif row["in_hot_trend"]:
                trends_tier.append(row)
            elif temp >= min_temp + 2:
                high_tier.append(row)
            elif temp >= min_temp:
                fill_tier.append(row)

        # ── Apply per-topic cap + collect candidates in priority order ──
        topic_counts: dict[str, int] = {}
        selected: list[dict] = []

        def try_add(item: dict, force: bool = False) -> bool:
            topic = item["topic"] or "general"
            count = topic_counts.get(topic, 0)
            if force or count < max_per_topic:
                selected.append(item)
                topic_counts[topic] = count + 1
                return True
            return False

        oversample_multiplier = 1

        # Alerts bypass cap
        for item in alerts:
            if len(selected) >= digest_max * oversample_multiplier:
                break
            try_add(item, force=True)

        for item in trends_tier + high_tier:
            if len(selected) >= digest_max * oversample_multiplier:
                break
            try_add(item)

        # Diversity fill: one best per remaining topic
        seen_topics = set(topic_counts.keys())
        for item in fill_tier:
            if len(selected) >= digest_max * oversample_multiplier:
                break
            topic = item["topic"] or "general"
            if topic not in seen_topics:
                try_add(item)
                seen_topics.add(topic)

        if not selected:
            logger.warning("Digest priority queue produced 0 candidates")
            return None

        # ── Cross-digest dedup: driven by template flags ──
        use_cross_dedup    = template_cfg.get("cross_dedup", True)
        use_ongoing_trends = template_cfg.get("ongoing_trends", True)
        lookback_digests   = template_cfg.get("lookback_digests", 2)

        ongoing_trends = []
        if use_cross_dedup and use_ongoing_trends and lookback_digests > 0:
            cross_dedup_threshold = rules.get("cross_dedup_threshold", 0.75)
            selected, ongoing_trends = self._dedup_against_previous_digests(
                selected, lookback=lookback_digests, threshold=cross_dedup_threshold
            )
        elif use_cross_dedup and lookback_digests > 0:
            # Dedup but don't pass ongoing trends to the LLM
            cross_dedup_threshold = rules.get("cross_dedup_threshold", 0.75)
            selected, _ = self._dedup_against_previous_digests(
                selected, lookback=lookback_digests, threshold=cross_dedup_threshold
            )

        logger.info(
            f"Digest: {len(alerts)} alerts, {len(trends_tier)} trend msgs, "
            f"{len(high_tier)} high-temp → {len(selected)} selected after dedup"
        )

        # For Agent mode, strictly enforce the max limit after dedup.
        # For Legacy Mode, we KEEP the oversized `selected` list and let the LLM prune it.
        if return_raw:
            selected = selected[:digest_max]

        # ── Build LLM prompt ──
        # Build post URLs for source linking in the digest
        def post_url(row: dict) -> str:
            ext_id = row.get("external_id", "")
            src = row.get("source_name", "")
            if ext_id and src and not src.startswith("-"):  # skip private/numeric channel IDs
                return f"https://t.me/{src}/{ext_id}"
            return f"https://t.me/{src}" if src else ""

        messages_text = "\n\n".join([
            f"[{i+1}] Channel: @{row['source_name']} | PostURL: {post_url(dict(row))} | Temperature: {row['temperature']}/10\n"
            f"Topic: {row['topic']}\n"
            f"Text: {row['text'][:300]}"
            for i, row in enumerate(selected)
        ])

        period = "последнее время"

        # Build ongoing trends section for the LLM if any carried-over topics detected
        if ongoing_trends:
            lines = ["\n--- ONGOING TRENDS (topics continuing from previous digest — synthesize as update) ---"]
            # Group by topic to avoid repeating the same trend multiple times
            seen_topics: set[str] = set()
            for t in ongoing_trends:
                topic = t["topic"]
                if topic not in seen_topics:
                    seen_topics.add(topic)
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
                        WHERE id IN (
                            SELECT tm2.message_id
                            FROM trend_messages tm1
                            JOIN trend_messages tm2 ON tm1.trend_id = tm2.trend_id
                            WHERE tm1.message_id IN ({placeholders})
                        )
                    """, selected_ids)
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"Failed to finalise raw digest DB state: {e}")
            return messages_text

        # ── 1. Agent Mode: Push raw payload to OpenClaw ──
        agent_succeeded = False
        route_enabled = self.cfg.get("route_via_openclaw", False) if self.cfg else False

        if route_enabled:
            webhook_url = (os.getenv("OPENCLAW_WEBHOOK_URL") or os.getenv("OPENCLAW_API_URL", "")).strip()

            if webhook_url.endswith("/hooks/wake"):
                webhook_url = webhook_url.replace("/hooks/wake", "/v1/chat/completions")
            elif not webhook_url.endswith("/v1/chat/completions"):
                webhook_url = "http://openclaw:18789/v1/chat/completions"

            token = (os.getenv("OPENCLAW_WEBHOOK_TOKEN") or os.getenv("OPENCLAW_API_TOKEN", "")).strip()
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
                    if template_name == "spoiler":
                        # Spoiler template: LLM returns JSON → renderer builds MarkdownV2
                        title_max_words       = template_cfg.get("title_max_words", 8)
                        summary_max_sentences = template_cfg.get("summary_max_sentences", 3)
                        llm_merge             = template_cfg.get("llm_merge", True)

                        if llm_merge:
                            merge_step = DIGEST_SPOILER_MERGE_ON
                        else:
                            merge_step = DIGEST_SPOILER_MERGE_OFF.format(digest_max=digest_max)

                        spoiler_prompt = DIGEST_PROMPT_SPOILER.format(
                            period=period,
                            count=len(selected),
                            messages=messages_text,
                            digest_max=digest_max,
                            title_max_words=title_max_words,
                            summary_max_sentences=summary_max_sentences,
                            merge_step=merge_step,
                        )
                        llm_json = await self.llm.complete_json(
                            user_prompt=spoiler_prompt,
                            system_prompt=SYSTEM_PROMPT,
                            temperature=0.3,
                            # max_tokens не ограничен: с включённым thinking модель тратит
                            # ~5000 токенов на reasoning — жёсткий лимит 4096 обрывал JSON
                            disable_thinking=False,  # digest: always full thinking for quality
                        )
                        digest_content, parse_mode = render_digest(llm_json, "spoiler", template_cfg)
                    else:
                        # Classic template: LLM returns ready-made Markdown text
                        raw_text = await self.llm.complete(
                            user_prompt=prompt,
                            system_prompt=SYSTEM_PROMPT,
                            temperature=0.4,
                            disable_thinking=False,  # digest: always full thinking for quality
                        )
                        digest_content, parse_mode = render_digest(raw_text, "classic", template_cfg)
                except Exception as e:
                    logger.error(f"Local LLM digest generation failed: {e}")
                    return None

        # ── 3. Mark in DB ──
        try:
            conn = get_db(self.db_path)
            try:
                if digest_content:
                    conn.execute(
                        "INSERT INTO digests (content_md, parse_mode, period_start, period_end) VALUES (?, ?, ?, ?)",
                        (digest_content, parse_mode, since.isoformat(), datetime.utcnow().isoformat()),
                    )

                selected_ids = [row["id"] for row in selected]
                conn.executemany(
                    "UPDATE messages SET in_digest=1 WHERE id=?",
                    [(mid,) for mid in selected_ids]
                )

                if selected_ids:
                    placeholders = ",".join("?" * len(selected_ids))
                    conn.execute(f"""
                        UPDATE messages SET in_digest=1
                        WHERE id IN (
                            SELECT tm2.message_id
                            FROM trend_messages tm1
                            JOIN trend_messages tm2 ON tm1.trend_id = tm2.trend_id
                            WHERE tm1.message_id IN ({placeholders})
                        )
                    """, selected_ids)

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

    def _dedup_by_similarity(self, candidates: list[dict], threshold: float) -> list[dict]:
        """
        Remove semantically near-duplicate messages using ChromaDB cosine similarity.

        For each candidate we query ChromaDB for similar messages already in
        the 'selected so far' set. If similarity > threshold → skip.
        Falls back to returning candidates as-is if ChromaDB is unavailable.
        """
        try:
            self.chroma._connect()
            unique: list[dict] = []
            kept_ids: set[str] = set()

            for msg in candidates:
                msg_id = str(msg["id"])
                if msg_id in kept_ids:
                    continue

                # Find documents in ChromaDB that are similar to this one
                vector = self.embedder.encode(msg["text"][:512])
                result = self.chroma._collection.query(
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
        self, candidates: list[dict], lookback: int = 2, threshold: float = 0.75
    ) -> tuple[list[dict], list[dict]]:
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
                "SELECT content_md FROM digests ORDER BY id DESC LIMIT ?", (lookback,)
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

            def cosine(a, b) -> float:
                a, b = np.array(a), np.array(b)
                denom = np.linalg.norm(a) * np.linalg.norm(b)
                return float(np.dot(a, b) / denom) if denom > 0 else 0.0

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
                        conn = get_db(self.db_path)
                        min_len = int(self.cfg.get("min_message_length", 30)) if self.cfg else 30
                        count = conn.execute("SELECT COUNT(*) FROM messages WHERE analyzed = 0 AND length(text) >= ?", (min_len,)).fetchone()[0]
                        conn.close()
                        if count >= max_pending:
                            logger.info(f"Threshold reached ({count} pending >= {max_pending}), waking up early")
                            break
                    except Exception as e:
                        logger.debug(f"Error checking pending count: {e}")


async def main():
    """Docker entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    db_path = os.environ.get("DATABASE_PATH", "/app/data/news.db")
    interval = int(os.environ.get("ANALYZE_INTERVAL_MINUTES", "30"))

    init_db(db_path)

    llm = LLMClient(timeout=300)  # 5 min — covers digest with full thinking

    logger.info(f"Checking LLM at {llm.base_url}...")
    if await llm.health_check():
        logger.info("LLM is available")
    else:
        logger.warning("LLM not available yet — will retry on each cycle")

    cfg = ConfigWatcher("/app/config/settings.json")

    analyzer = NewsAnalyzer(
        db_path=db_path,
        llm_client=llm,
        interval_minutes=interval,
        cfg=cfg,
    )

    await analyzer.run_loop()


if __name__ == "__main__":
    asyncio.run(main())
