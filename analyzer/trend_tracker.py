"""
TrendTracker — Phase 2: Trend Detection Engine.

Core idea from the plan:
  "When MULTIPLE channels independently write about the same thing — that IS the hot news."

How it works (runs every 15 min as a background task inside the analyzer):
  1. Fetch recent analyzed messages from SQLite (last WINDOW_HOURS, default 4h)
  2. Get their BGE-m3 embeddings from ChromaDB in one batch call
  3. Cluster with HDBSCAN on pre-computed embeddings (no re-encoding needed)
  4. For each cluster: count unique_sources, compute trend_score, assign lifecycle
  5. LLM (Oobabooga) names the top clusters and writes a summary
  6. Upsert results into SQLite: trends + trend_messages tables

Trend Score Formula:
  trend_score = unique_sources × avg_temperature × recency_factor × (1 + log(1 + avg_views))
  recency_factor = exp(-0.3 × hours_since_first)

Status Lifecycle:
  emerging → 1-2 unique sources, fresh topic
  hot      → 5+ unique sources, velocity > 3 posts/hour
  cooling  → velocity < 1 post/hour, channels stopped writing
  dead     → no updates for > 6 hours

Why HDBSCAN directly (not full BERTopic):
  We already have BGE-m3 embeddings in ChromaDB. BERTopic's main value is
  computing embeddings (we skip that) and HDBSCAN (we use directly).
  This saves memory and avoids re-encoding. BERTopic can be added later for
  better topic labeling if needed (add c-TF-IDF on top of our clusters).

Fallback: if hdbscan is not installed, groups by LLM topic label from the
  analysis table. Less accurate but always functional.
"""

import asyncio
import logging
import math
import os
import httpx
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────

@dataclass
class TrendCluster:
    """
    Temporary in-memory representation of a semantic cluster.
    Built during a detection cycle, then persisted to SQLite as a Trend.
    """
    topic: str                  # placeholder ("cluster_N") until LLM names it
    message_ids: list[int]
    sources: list[str]          # all source names (may repeat across messages)
    unique_sources: int         # len(set(sources)) — the KEY signal
    temperatures: list[float]   # LLM temperature per message
    views: list[int]            # Telegram view count per message
    timestamps: list[datetime]
    texts: list[str]            # message texts (for LLM summarization)
    source_urls: dict[str, str] = field(default_factory=dict) # channel -> newest message url

    # Set by _name_cluster() after LLM call
    llm_summary: str = ""

    @property
    def message_count(self) -> int:
        return len(self.message_ids)

    @property
    def avg_temperature(self) -> float:
        return sum(self.temperatures) / len(self.temperatures) if self.temperatures else 5.0

    @property
    def avg_views(self) -> float:
        return sum(self.views) / len(self.views) if self.views else 0.0

    @property
    def first_seen(self) -> datetime:
        return min(self.timestamps)

    @property
    def last_seen(self) -> datetime:
        return max(self.timestamps)

    @property
    def hours_span(self) -> float:
        """Duration of the cluster in hours (at least 1 minute to avoid /0)."""
        delta = self.last_seen - self.first_seen
        return max(delta.total_seconds() / 3600, 1 / 60)

    @property
    def velocity(self) -> float:
        """Posts per hour within this cluster's time span."""
        return self.message_count / self.hours_span

    @property
    def hours_since_first(self) -> float:
        return (datetime.now(timezone.utc) - self.first_seen).total_seconds() / 3600

    @property
    def recency_factor(self) -> float:
        """
        Exponential decay: a brand-new trend scores higher than an old one.
        At 0h: factor=1.0, at 2.3h: factor=0.5, at 6h: factor=0.17
        """
        return math.exp(-0.3 * self.hours_since_first)

    def compute_trend_score(self) -> float:
        """
        trend_score = unique_sources × avg_temperature × recency_factor × (1 + log(1 + avg_views))

        unique_sources is the most important factor — a story covered by 10 channels
        independently is more significant than one channel with a very hot take.
        """
        return (
            self.unique_sources
            * self.avg_temperature
            * self.recency_factor
            * (1.0 + math.log1p(self.avg_views))  # log1p = log(1+x), safe for views=0
        )

    def compute_status(self) -> str:
        """
        Lifecycle state based on cluster activity metrics.

        emerging → fresh, ≤2 unique sources, just appeared
        hot      → 5+ channels, posting rapidly (>3/hour)
        cooling  → slowing down (<1 post/hour), or old
        dead     → nothing new in >6 hours
        """
        hours_since_update = (datetime.now(timezone.utc) - self.last_seen).total_seconds() / 3600

        if hours_since_update > 6:
            return "dead"
        elif self.unique_sources >= 5 and self.velocity >= 3:
            return "hot"
        elif self.velocity < 1.0 and self.hours_since_first > 2:
            return "cooling"
        else:
            return "emerging"


# ─────────────────────────────────────────────────────────
# TrendTracker
# ─────────────────────────────────────────────────────────

class TrendTracker:
    """
    Detects trending topics by clustering recent news embeddings from ChromaDB.

    Designed to run as a background task inside the analyzer process.
    Call run_cycle() periodically (every 15 minutes).

    Dependencies (injected from NewsAnalyzer to avoid re-creating):
      db_path    — SQLite database path
      llm_client — LLMClient (for naming clusters)
      chroma     — ChromaClient (for fetching embeddings)
    """

    # Tunable constants (can be overridden via env vars)
    MIN_CLUSTER_SIZE = int(os.getenv("TREND_MIN_CLUSTER_SIZE", "2"))
    MIN_UNIQUE_SOURCES = int(os.getenv("TREND_MIN_UNIQUE_SOURCES", "2"))
    WINDOW_HOURS = int(os.getenv("TREND_WINDOW_HOURS", "4"))
    MAX_MESSAGES = int(os.getenv("TREND_MAX_MESSAGES", "500"))
    LLM_NAME_TOP_N = 10          # name only top N clusters by score (saves LLM calls)
    HDBSCAN_EPSILON = 0.35       # cluster_selection_epsilon — tune if too few/many clusters
    DEAD_TREND_HOURS = 12        # mark old trends as dead even if not in current window

    def __init__(self, db_path: str, llm_client, chroma_client, analyzer=None):
        self.db_path = db_path
        self.llm = llm_client
        self.chroma = chroma_client
        self.analyzer = analyzer

    async def run_cycle(self) -> int:
        """
        Execute one full detection cycle.

        Returns:
            Number of trends upserted into SQLite
        """
        logger.info(f"TrendTracker: starting cycle (window={self.WINDOW_HOURS}h)")
        
        from analyzer.llm_client import is_llm_locked
        if is_llm_locked():
            logger.info("TrendTracker: LLM is locked (digest generation in progress). Skipping cycle.")
            return 0

        # Step 1: fetch recent analyzed+embedded messages from SQLite
        messages = self._fetch_recent_messages()
        if len(messages) < self.MIN_CLUSTER_SIZE * 2:
            logger.info(f"TrendTracker: only {len(messages)} messages in window, skipping")
            return 0

        # Step 2: get embeddings from ChromaDB in one batch call
        embeddings, valid_messages = self._fetch_embeddings(messages)
        if not embeddings:
            logger.warning("TrendTracker: no embeddings in ChromaDB for the window")
            return 0

        logger.info(f"TrendTracker: {len(embeddings)} embeddings for clustering")

        # Step 3: cluster using HDBSCAN on pre-computed embeddings
        clusters = await self._cluster_messages(valid_messages, embeddings)
        if not clusters:
            logger.info("TrendTracker: HDBSCAN found no clusters (all noise)")
            return 0

        # Step 4: filter — only clusters with enough unique sources
        significant = [c for c in clusters if c.unique_sources >= self.MIN_UNIQUE_SOURCES]
        logger.info(
            f"TrendTracker: {len(clusters)} total clusters, "
            f"{len(significant)} with unique_sources >= {self.MIN_UNIQUE_SOURCES}"
        )

        if not significant:
            return 0

        # Step 5: sort by trend_score, name top N via LLM
        significant.sort(key=lambda c: c.compute_trend_score(), reverse=True)
        for cluster in significant[:self.LLM_NAME_TOP_N]:
            # Only call LLM if cluster still has placeholder name
            if cluster.topic.startswith("cluster_"):
                await self._name_cluster(cluster)

        # Step 6: mark old active trends as dead
        self._expire_old_trends()

        # Step 7: upsert clusters into SQLite trends table
        count = self._upsert_trends(significant)
        logger.info(f"TrendTracker: upserted {count} trends")
        return count

    # ─────────────────────────────────────────────────
    # Private: data fetching
    # ─────────────────────────────────────────────────

    def _fetch_recent_messages(self) -> list[dict]:
        """
        Load recent analyzed + ChromaDB-synced messages from SQLite.
        Only messages with chroma_synced=1 have embeddings we can cluster.
        """
        from database.schema import get_db

        # Use SQLite's own datetime() so the format matches collected_at exactly.
        # collected_at is stored as '2026-04-17 16:25:02+00:00' — SQLite datetime()
        # returns the same space-separated UTC format, avoiding string-compare mismatch.
        conn = get_db(self.db_path)
        try:
            min_len = int(self.analyzer.cfg.get("min_message_length", 30)) if self.analyzer and self.analyzer.cfg else 30
            rows = conn.execute("""
                SELECT
                    m.id,
                    m.text,
                    m.views,
                    m.collected_at,
                    s.name AS source_name,
                    m.forward_from_channel,
                    a.temperature,
                    a.topic AS llm_topic
                FROM messages m
                JOIN sources s ON m.source_id = s.id
                JOIN analysis a ON a.message_id = m.id
                WHERE m.collected_at >= datetime('now', ? )
                  AND m.analyzed = 1
                  AND m.chroma_synced = 1
                  AND length(m.text) >= ?
                ORDER BY m.collected_at DESC
                LIMIT ?
            """, (f"-{self.WINDOW_HOURS} hours", min_len, self.MAX_MESSAGES)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _fetch_embeddings(
        self, messages: list[dict]
    ) -> tuple[list[list[float]], list[dict]]:
        """
        Fetch BGE-m3 embeddings from ChromaDB for the given message IDs.

        Returns:
            Tuple of (embeddings_list, messages_that_have_embeddings)
            Only messages found in ChromaDB are returned.
        """
        if not messages:
            return [], []

        message_ids = [m["id"] for m in messages]
        try:
            self.chroma._connect()
            result = self.chroma._collection.get(
                ids=[str(mid) for mid in message_ids],
                include=["embeddings"],
            )

            if not result["ids"]:
                return [], []

            # Build a lookup: message_id → embedding
            id_to_embedding = {
                int(doc_id): emb
                for doc_id, emb in zip(result["ids"], result["embeddings"])
            }

            # Keep only messages that have embeddings (some may not be in ChromaDB yet)
            valid_messages = []
            embeddings = []
            for msg in messages:
                if msg["id"] in id_to_embedding:
                    valid_messages.append(msg)
                    embeddings.append(id_to_embedding[msg["id"]])

            return embeddings, valid_messages

        except Exception as e:
            logger.error(f"TrendTracker: ChromaDB fetch failed: {e}")
            return [], []

    # ─────────────────────────────────────────────────
    # Private: clustering
    # ─────────────────────────────────────────────────

    async def _cluster_messages(
        self,
        messages: list[dict],
        embeddings: list[list[float]],
    ) -> list[TrendCluster]:
        """
        Cluster messages using HDBSCAN on pre-computed BGE-m3 embeddings.

        Why not BERTopic here:
          BERTopic would re-encode texts (we skip that since we have embeddings).
          We use HDBSCAN directly — same algorithm BERTopic uses internally.
          c-TF-IDF topic words can be added later as an enhancement.

        HDBSCAN params:
          min_cluster_size=2  → a cluster can start with just 2 messages
          min_samples=1       → more clusters, less noise
          metric="euclidean"  → works with normalized embeddings (= cosine-equivalent)
          epsilon=0.35        → merge clusters closer than this distance
        """
        try:
            from hdbscan import HDBSCAN
        except ImportError:
            logger.warning(
                "hdbscan not installed — falling back to LLM topic-based grouping. "
                "Install with: pip install hdbscan"
            )
            return self._fallback_cluster_by_topic(messages)

        import numpy as np

        loop = asyncio.get_running_loop()

        def _run_hdbscan():
            arr = np.array(embeddings, dtype=np.float32)
            clusterer = HDBSCAN(
                min_cluster_size=self.MIN_CLUSTER_SIZE,
                min_samples=1,
                metric="euclidean",
                cluster_selection_epsilon=self.HDBSCAN_EPSILON,
                core_dist_n_jobs=1,   # single-threaded for Docker containers
            )
            return clusterer.fit_predict(arr)

        try:
            labels = await loop.run_in_executor(None, _run_hdbscan)
        except Exception as e:
            logger.error(f"TrendTracker: HDBSCAN failed: {e}")
            return self._fallback_cluster_by_topic(messages)

        # Group messages by cluster label (-1 = noise, skip those)
        cluster_map: dict[int, list[dict]] = {}
        for msg, label in zip(messages, labels):
            if label == -1:
                continue   # noise point — doesn't belong to any cluster
            cluster_map.setdefault(int(label), []).append(msg)

        logger.debug(
            f"TrendTracker: HDBSCAN produced {len(cluster_map)} clusters, "
            f"{sum(1 for l in labels if l == -1)} noise points"
        )

        return [
            self._build_cluster(f"cluster_{label}", msgs)
            for label, msgs in cluster_map.items()
        ]

    def _fallback_cluster_by_topic(self, messages: list[dict]) -> list[TrendCluster]:
        """
        Fallback grouping when HDBSCAN is unavailable.
        Uses LLM-assigned topic labels from the analysis table instead of embeddings.
        Less accurate (LLM can use different labels for the same topic) but always works.
        """
        topic_map: dict[str, list[dict]] = {}
        for msg in messages:
            topic = (msg.get("llm_topic") or "unknown").lower().strip()
            topic_map.setdefault(topic, []).append(msg)

        clusters = []
        for topic, msgs in topic_map.items():
            if len(msgs) < self.MIN_CLUSTER_SIZE:
                continue
            cluster = self._build_cluster(topic, msgs)
            clusters.append(cluster)

        return clusters

    def _build_cluster(self, label: str, messages: list[dict]) -> TrendCluster:
        """Build a TrendCluster from a group of messages.

        Uses the effective source for unique counting:
        - If a message is a forward (forward_from_channel is set), use that as the source key.
        - Otherwise use source_name (the channel that published natively).
        This prevents aggregators that forward content from inflating unique_sources.
        """
        timestamps = []
        for msg in messages:
            try:
                timestamps.append(datetime.fromisoformat(msg["collected_at"]))
            except Exception:
                timestamps.append(datetime.now(timezone.utc))

        # Effective source: forward origin > publisher name
        sources = [
            (m.get("forward_from_channel") or m["source_name"])
            for m in messages
        ]
        # Always include the publisher name too so subscriber channels get credit
        all_channel_names = [m["source_name"] for m in messages]

        # Build source URLs (keep the latest message URL per source)
        source_urls = {}
        for m in sorted(messages, key=lambda x: x.get("collected_at", ""), reverse=True):
            source = m.get("forward_from_channel") or m["source_name"]
            if source not in source_urls and m.get("external_id"):
                source_urls[source] = f"https://t.me/{source}/{m['external_id']}"
            # Also keep URL for the direct publisher in case they are mentioned
            publisher = m["source_name"]
            if publisher not in source_urls and m.get("external_id"):
                source_urls[publisher] = f"https://t.me/{publisher}/{m['external_id']}"

        return TrendCluster(
            topic=label,
            message_ids=[m["id"] for m in messages],
            sources=sources,
            unique_sources=len(set(sources)),
            temperatures=[float(m.get("temperature") or 5.0) for m in messages],
            views=[int(m.get("views") or 0) for m in messages],
            timestamps=timestamps,
            texts=[m["text"][:600] for m in messages],
            source_urls=source_urls,
        )

    # ─────────────────────────────────────────────────
    # Private: LLM enrichment
    # ─────────────────────────────────────────────────

    async def _name_cluster(self, cluster: TrendCluster) -> None:
        """
        Ask the LLM to give a human-readable name and summary to a cluster.
        Updates cluster.topic and cluster.llm_summary in place.

        Uses at most 5 representative posts (highest temperature first, estimated by index).
        Sends a compact JSON prompt — same LLM endpoint as the main analyzer.
        """
        # Take up to 5 posts from the cluster (first ones, already ordered by collected_at DESC)
        sample = cluster.texts[:5]
        posts_text = "\n\n".join(f"[{i+1}] {t}" for i, t in enumerate(sample))

        prompt = (
            f"You are analyzing {cluster.message_count} news posts from "
            f"{cluster.unique_sources} different Telegram channels about the same topic.\n\n"
            f"Sample posts:\n{posts_text}\n\n"
            "Reply ONLY with valid JSON (no markdown, no explanation):\n"
            '{"topic": "Short topic name in English (3-6 words)", '
            '"summary": "2-3 sentence summary of what this cluster is about"}'
        )

        try:
            result = await self.llm.complete_json(
                user_prompt=prompt,
                system_prompt=(
                    "You are a financial news analyst specializing in crypto and macro markets. "
                    "Be concise and precise. Always respond with valid JSON only."
                ),
                temperature=0.1,
                disable_thinking=False,  # cluster naming: always full thinking for accuracy
            )
            cluster.topic = result.get("topic", cluster.topic)[:100]  # cap length
            cluster.llm_summary = result.get("summary", "")[:500]
            logger.debug(f"TrendTracker: LLM named cluster → '{cluster.topic}'")

        except Exception as e:
            logger.warning(f"TrendTracker: LLM naming failed: {e} — using placeholder name")

    # ─────────────────────────────────────────────────
    # Private: persistence
    # ─────────────────────────────────────────────────

    def _upsert_trends(self, clusters: list[TrendCluster]) -> int:
        """
        Save or update trends in SQLite.

        Matching logic: a cluster matches an existing trend if it has the same
        topic name AND was active within the last 24 hours. This prevents creating
        duplicate trends for the same ongoing story.

        Returns:
            Number of trends inserted or updated.
        """
        from database.schema import get_db

        conn = get_db(self.db_path)
        count = 0

        try:
            recent_cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

            for cluster in clusters:
                score = cluster.compute_trend_score()
                status = cluster.compute_status()
                summary = cluster.llm_summary or None

                # Lookup existing trend with same topic (active in last 24h)
                existing = conn.execute("""
                    SELECT id, status FROM trends
                    WHERE topic = ?
                      AND datetime(last_seen) >= datetime(?)
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (cluster.topic, recent_cutoff)).fetchone()

                # SEMANTIC STORY MERGING:
                # If exact topic match failed, check ChromaDB for semantic similarity
                if not existing and self.chroma and cluster.message_ids:
                    try:
                        # Grab embedding of the first message in cluster (already in ChromaDB)
                        similar_msgs = self.chroma.find_similar(cluster.message_ids[0], limit=15)
                        # Threshold 0.82 catches paraphrased or heavily related coverage
                        high_sim_ids = [m["message_id"] for m in similar_msgs if m["similarity"] >= 0.82]
                        
                        if high_sim_ids:
                            placeholders = ",".join("?" for _ in high_sim_ids)
                            query = f"""
                                SELECT t.id, t.status 
                                FROM trends t
                                JOIN trend_messages tm ON tm.trend_id = t.id
                                WHERE tm.message_id IN ({placeholders})
                                  AND datetime(t.last_seen) >= datetime(?)
                                ORDER BY t.created_at DESC
                                LIMIT 1
                            """
                            params = tuple(high_sim_ids) + (recent_cutoff,)
                            existing = conn.execute(query, params).fetchone()
                    except Exception as e:
                        # This happens if cluster.message_ids[0] hasn't been synced to ChromaDB yet,
                        # which is rare but possible depending on the pipeline timing.
                        pass

                if existing:
                    trend_id = existing["id"]
                    existing_status = existing["status"]
                    conn.execute("""
                        UPDATE trends SET
                            trend_score    = ?,
                            unique_sources = ?,
                            message_count  = ?,
                            last_seen      = ?,
                            velocity       = ?,
                            status         = ?,
                            summary        = COALESCE(?, summary),
                            updated_at     = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (
                        score,
                        cluster.unique_sources,
                        cluster.message_count,
                        cluster.last_seen.isoformat(),
                        cluster.velocity,
                        status,
                        summary,
                        trend_id,
                    ))
                else:
                    cursor = conn.execute("""
                        INSERT INTO trends
                            (topic, trend_score, unique_sources, message_count,
                             first_seen, last_seen, velocity, status, summary)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        cluster.topic,
                        score,
                        cluster.unique_sources,
                        cluster.message_count,
                        cluster.first_seen.isoformat(),
                        cluster.last_seen.isoformat(),
                        cluster.velocity,
                        status,
                        summary,
                    ))
                    trend_id = cursor.lastrowid
                    existing_status = "emerging" # New trend, default to emerging to see if it hit hot

                # Hot Trend Alert: fires ONCE when unique_sources crosses the threshold
                # and the trend has never been alerted before (alerted_at IS NULL)
                existing_alerted_at = conn.execute(
                    "SELECT alerted_at FROM trends WHERE id=?", (trend_id,)
                ).fetchone()
                alerted_at_val = existing_alerted_at["alerted_at"] if existing_alerted_at else None

                min_sources = int(os.getenv(
                    "HOT_TREND_MIN_SOURCES",
                    str((self.analyzer.cfg or {}).get("hot_trend_min_sources", 5) if self.analyzer else 5)
                ))
                is_fresh = (datetime.now(timezone.utc) - cluster.last_seen).total_seconds() < 7200 # 2 hours
                if cluster.unique_sources >= min_sources and not alerted_at_val and is_fresh:
                    conn.execute(
                        "UPDATE trends SET alerted_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (trend_id,)
                    )
                    if self.analyzer:
                        asyncio.create_task(
                            self.analyzer._route_event("hot_trend", {
                                "topic": cluster.topic,
                                "score": score,
                                "sources": cluster.unique_sources,
                                "channels": list(set(cluster.sources))[:8],
                                "source_urls": cluster.source_urls,
                                "message_count": cluster.message_count,
                                "summary": summary or "Hot trend detected.",
                            })
                        )

                # Link messages to this trend (INSERT OR IGNORE = idempotent)
                for message_id in cluster.message_ids:
                    conn.execute("""
                        INSERT OR IGNORE INTO trend_messages (trend_id, message_id)
                        VALUES (?, ?)
                    """, (trend_id, message_id))

                conn.commit()
                count += 1

        except Exception as e:
            logger.error(f"TrendTracker: database upsert failed: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            conn.close()

        return count

    def _expire_old_trends(self) -> None:
        """
        Mark trends that haven't been updated in DEAD_TREND_HOURS as 'dead'.
        Runs at the end of each cycle to keep lifecycle statuses accurate.
        """
        from database.schema import get_db

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=self.DEAD_TREND_HOURS)).isoformat()
        conn = get_db(self.db_path)
        try:
            result = conn.execute("""
                UPDATE trends
                SET status = 'dead', updated_at = CURRENT_TIMESTAMP
                WHERE status != 'dead'
                  AND last_seen < ?
            """, (cutoff,))
            if result.rowcount:
                logger.info(f"TrendTracker: expired {result.rowcount} old trends → 'dead'")
            conn.commit()
        except Exception as e:
            logger.error(f"TrendTracker: expire_old_trends failed: {e}")
        finally:
            conn.close()

    async def _send_trend_alert(self, topic: str, score: float, sources: int, summary: str):
        """Immediately dispatch a Telegram message when a trend hits HOT."""
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        allowed_users = os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",")
        if not bot_token or not allowed_users or not allowed_users[0]:
            return

        safe_topic = topic.replace("_", "\_")

        message = (
            f"🔥 *TRENDING NOW*\n\n"
            f"📡 `{safe_topic}`\n"
            f"📈 Score: *{score:.1f}* \u2014 {sources} независимых канала\n\n"
            f"📝 {summary}"
        )

        async with httpx.AsyncClient() as client:
            for uid in allowed_users:
                uid = uid.strip()
                if not uid: continue
                try:
                    await client.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={"chat_id": uid, "text": message, "parse_mode": "Markdown", "link_preview_options": {"is_disabled": True}}
                    )
                except Exception as e:
                    logger.error(f"Failed to send trend alert to {uid}: {e}")
