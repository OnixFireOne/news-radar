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

from analyzer.llm_client import LLMClient
from analyzer.prompts import SINGLE_MESSAGE_PROMPT, DIGEST_PROMPT, SYSTEM_PROMPT
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

        try:
            # Load active subscriptions for real-time alerting
            active_subs = conn.execute("SELECT user_id, query FROM subscriptions WHERE active=1").fetchall()
            subs_list = [{"user_id": s["user_id"], "query": s["query"], "q_lower": s["query"].lower()} for s in active_subs]
        except Exception:
            subs_list = []

        try:
            rows = conn.execute("""
                SELECT m.id, m.text, s.name as source_name
                FROM messages m
                JOIN sources s ON m.source_id = s.id
                WHERE m.analyzed = 0
                  AND length(m.text) >= 30
                ORDER BY m.collected_at DESC
                LIMIT ?
            """, (self.batch_size,)).fetchall()

            if not rows:
                logger.debug("No pending messages to analyze")
                return 0

            logger.info(f"Analyzing {len(rows)} pending messages...")

            for row in rows:
                try:
                    result = await self._analyze_message(
                        message_id=row["id"],
                        text=row["text"],
                        source_name=row["source_name"],
                    )

                    if result:
                        # Phase 3: normalize topic label using topics.json aliases
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

                        # Phase 1: store embedding in ChromaDB for semantic search
                        # Pass the already-open conn so _store_embedding
                        # doesn't need to open a second connection (avoids "database is locked")
                        await self._store_embedding(
                            conn=conn,
                            message_id=row["id"],
                            text=row["text"],
                            source_name=row["source_name"],
                            timestamp=row["collected_at"] if "collected_at" in row.keys() else datetime.utcnow().isoformat(),
                            temperature=result.get("temperature", 5.0),
                            topic=result.get("topic", "general"),
                        )

                    # Mark as analyzed + commit everything for this message at once
                    conn.execute(
                        "UPDATE messages SET analyzed=1 WHERE id=?",
                        (row["id"],)
                    )
                    conn.commit()
                    count += 1

                    # Phase 5: Instant Alerts -> directly to Telegram if temperature >= 9.0
                    temp = float(result.get("temperature", 5.0))
                    if temp >= 9.0 and self.cfg and self.cfg.get("instant_alerts_temperature", True):
                        source_n = row["source_name"]
                        raw_topic = result.get("topic", "general")
                        summary = result.get("summary", "No summary provided.")
                        text = row["text"]
                        asyncio.create_task(
                            self._send_instant_alert(row["id"], source_n, temp, raw_topic, summary, text)
                        )

                    # Real-time subscription matching
                    if subs_list and result:
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

                    # Small delay to avoid overloading the LLM
                    await asyncio.sleep(0.5)

                except Exception as e:
                    logger.error(f"Failed to analyze message {row['id']}: {e}")
                    conn.execute(
                        "UPDATE messages SET analyzed=1 WHERE id=?",
                        (row["id"],)
                    )
                    conn.commit()

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
        webhook_url = os.getenv("OPENCLAW_WEBHOOK_URL", "").strip()
        route_enabled = self.cfg.get("route_via_openclaw", False) if self.cfg else False

        # Convert old /hooks/wake URL to OpenAI /v1/chat/completions
        if webhook_url.endswith("/hooks/wake"):
            webhook_url = webhook_url.replace("/hooks/wake", "/v1/chat/completions")
        elif not webhook_url.endswith("/v1/chat/completions"):
            webhook_url = "http://host.docker.internal:18789/v1/chat/completions"

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

            token = os.getenv("OPENCLAW_WEBHOOK_TOKEN", "").strip()
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
                    logger.info(f"Event '{event_type}' \u2192 OpenClaw (HTTP {resp.status_code})")
            except Exception as e:
                logger.error(f"OpenClaw webhook failed ({event_type}): {e} \u2014 falling back to Telegram")
                await self._fallback_telegram(event_type, data)
        else:
            # \u2500\u2500 Direct Telegram (OpenClaw disabled or not configured) \u2500\u2500
            await self._fallback_telegram(event_type, data)

    async def _fallback_telegram(self, event_type: str, data: dict):
        """Send event directly to Telegram when OpenClaw is not configured."""
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        allowed_users = os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",")
        if not bot_token or not allowed_users or not allowed_users[0]:
            return

        if event_type == "breaking_alert":
            safe_source = data["source"].replace("_", "\_")
            source_url = data.get("source_url", f"https://t.me/{data['source']}")
            message = (
                f"🚨 *BREAKING NEWS* (Температура: {data['temperature']:.0f}/10)\n\n"
                f"🕹 Тема: `{data['topic']}`\n\n"
                f"📝 {data['summary']}\n\n"
                f"[\u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a]({source_url})"
            )
        elif event_type == "trend_alert":
            safe_topic = data["topic"].replace("_", "\_")
            message = (
                f"🔥 *TRENDING NOW*\n\n"
                f"📡 `{safe_topic}`\n"
                f"📈 Score: *{data['score']:.1f}* \u2014 {data['sources']} независимых канала\n\n"
                f"📝 {data['summary']}"
            )
        elif event_type == "digest":
            message = data.get("text", "")
        else:
            return

        async with httpx.AsyncClient() as client:
            for uid in allowed_users:
                uid = uid.strip()
                if not uid: continue
                try:
                    await client.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={"chat_id": uid, "text": message, "parse_mode": "Markdown",
                              "disable_web_page_preview": True}
                    )
                except Exception as e:
                    logger.error(f"Telegram fallback failed for {uid}: {e}")

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
        """Send a single message to the LLM for analysis."""
        prompt = SINGLE_MESSAGE_PROMPT.format(
            source_name=source_name,
            text=text[:2000],  # truncate very long messages
        )

        try:
            result = await self.llm.complete_json(
                user_prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=512,
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

    async def generate_digest(self, hours: int = 3, force: bool = False) -> str | None:
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
        # ── Load rules (hot-reload from settings if cfg available) ──
        rules = {}
        digest_max    = 7
        min_temp      = 5.0
        if self.cfg:
            rules      = self.cfg.get("digest_rules", {})
            digest_max = self.cfg.get("digest_max_items", 7)
            min_temp   = self.cfg.get("digest_min_temperature", 5.0)

        max_per_topic     = rules.get("max_per_topic", 2)
        include_alerts    = rules.get("always_include_alerts", True)
        trend_src_min     = rules.get("min_unique_sources_for_trend", 3)
        dedup_threshold   = rules.get("dedup_threshold", 0.85)

        since = datetime.utcnow() - timedelta(hours=hours)
        conn = get_db(self.db_path)

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

        # ── Build priority tiers ──
        alerts, trends_tier, high_tier, fill_tier = [], [], [], []
        for row in rows:
            topic = row["topic"] or "general"
            temp  = float(row["temperature"] or 5.0)
            is_alert = self._is_alert_topic(topic) or temp >= 9.0

            if is_alert and include_alerts:
                alerts.append(dict(row))
            elif row["in_hot_trend"]:
                trends_tier.append(dict(row))
            elif temp >= min_temp + 2:
                high_tier.append(dict(row))
            elif temp >= min_temp:
                fill_tier.append(dict(row))

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

        # Alerts bypass cap
        for item in alerts:
            if len(selected) >= digest_max:
                break
            try_add(item, force=True)

        for item in trends_tier + high_tier:
            if len(selected) >= digest_max:
                break
            try_add(item)

        # Diversity fill: one best per remaining topic
        seen_topics = set(topic_counts.keys())
        for item in fill_tier:
            if len(selected) >= digest_max:
                break
            topic = item["topic"] or "general"
            if topic not in seen_topics:
                try_add(item)
                seen_topics.add(topic)

        if not selected:
            logger.warning("Digest priority queue produced 0 candidates")
            return None

        # ── Semantic dedup via ChromaDB ──
        if dedup_threshold < 1.0:
            selected = self._dedup_by_similarity(selected, threshold=dedup_threshold)

        logger.info(
            f"Digest: {len(alerts)} alerts, {len(trends_tier)} trend msgs, "
            f"{len(high_tier)} high-temp → {len(selected)} selected after dedup"
        )

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

        period = f"last {hours} hours"
        prompt = DIGEST_PROMPT.format(
            period=period,
            count=len(selected),
            messages=messages_text,
        )

        try:
            digest = await self.llm.complete(
                user_prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                temperature=0.4,
                max_tokens=1500,
            )

            conn = get_db(self.db_path)
            try:
                conn.execute(
                    "INSERT INTO digests (content_md, period_start, period_end) VALUES (?, ?, ?)",
                    (digest, since.isoformat(), datetime.utcnow().isoformat()),
                )

                selected_ids = [row["id"] for row in selected]

                # Mark the selected messages themselves
                conn.executemany(
                    "UPDATE messages SET in_digest=1 WHERE id=?",
                    [(mid,) for mid in selected_ids]
                )

                # Also mark ALL messages in the same trend clusters — so the
                # same story (covered by 10 channels) doesn't resurface next
                # digest cycle from a different channel.
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

            # ── Route digest to OpenClaw or fallback to direct Telegram ──
            await self._route_event("digest", {
                "period": period,
                "text": digest,
                "message_count": len(selected),
            })

            return digest

        except Exception as e:
            logger.error(f"Failed to generate digest: {e}")
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
                result = self.chroma._collection.query(
                    query_texts=[msg["text"][:512]],
                    n_results=min(10, len(candidates)),
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


    async def run_loop(self) -> None:
        """
        Main loop — runs things on different schedules:
          1. LLM analysis of new messages (every ANALYZE_INTERVAL_MINUTES)
          2. TrendTracker clustering cycle (every TREND_INTERVAL_MINUTES, default 15 min)
          3. Digest generation (every DIGEST_INTERVAL_HOURS, default 6 hours)

        Both tasks share the same ChromaDB client and embedder instance.
        TrendTracker is non-blocking: if it fails, analysis continues.
        """
        trend_interval = int(os.environ.get("TREND_INTERVAL_MINUTES", "15")) * 60
        last_trend_run = datetime.utcnow() - timedelta(seconds=trend_interval)  # run immediately on start

        digest_interval_hours = int(os.environ.get("DIGEST_INTERVAL_HOURS", "6"))
        digest_interval = digest_interval_hours * 3600
        # Don't run immediately on start so it doesn't send broken empty digests on fast restarts,
        # but let's say it ran `digest_interval - 1_minute` ago to trigger soon.
        last_digest_run = datetime.utcnow() - timedelta(seconds=digest_interval - 60)

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
                await self.analyze_pending()
            except Exception as e:
                logger.error(f"Analyzer loop error: {e}")

            # ─── TrendTracker cycle (every 15 min) ───
            now = datetime.utcnow()
            if (now - last_trend_run).total_seconds() >= trend_interval:
                try:
                    await trend_tracker.run_cycle()
                    last_trend_run = now
                except Exception as e:
                    logger.error(f"TrendTracker cycle error: {e}")
                    last_trend_run = now  # don't retry immediately on error
                    
            # ─── Digest generation cycle ───
            now = datetime.utcnow()
            if (now - last_digest_run).total_seconds() >= digest_interval:
                engine = self.cfg.get("digest_engine", "legacy") if self.cfg else "legacy"
                if engine == "agent":
                    logger.info("digest_engine=agent — skipping legacy digest, waiting for OpenClaw NarrativeAgent")
                    last_digest_run = now
                else:
                    try:
                        logger.info(f"Generating periodic digest (last {digest_interval_hours} hours)...")
                        await self.generate_digest(hours=digest_interval_hours)
                        last_digest_run = now
                    except Exception as e:
                        logger.error(f"Digest generation cycle error: {e}")
                        last_digest_run = now

            await asyncio.sleep(self.interval)


async def main():
    """Docker entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    db_path = os.environ.get("DATABASE_PATH", "/app/data/news.db")
    interval = int(os.environ.get("ANALYZE_INTERVAL_MINUTES", "30"))

    init_db(db_path)

    llm = LLMClient()

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
