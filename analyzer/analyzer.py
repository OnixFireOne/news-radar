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
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzer.llm_client import LLMClient
from analyzer.prompts import SINGLE_MESSAGE_PROMPT, DIGEST_PROMPT, SYSTEM_PROMPT
from analyzer.embedder import get_embedder
from analyzer.chroma_client import ChromaClient
from analyzer.trend_tracker import TrendTracker   # Phase 2: trend detection
from database.schema import get_db, init_db

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
    ):
        self.db_path = db_path
        self.llm = llm_client
        self.batch_size = batch_size
        self.interval = interval_minutes * 60  # convert to seconds

        # Vector store for semantic search + dedup (Phase 1)
        # Initialized lazily on first use; failure here does not stop analysis
        self.chroma = ChromaClient()
        self.embedder = get_embedder()  # BGE-m3, loaded on first encode() call

    async def analyze_pending(self) -> int:
        """
        Process all unanalyzed messages.
        Returns the number of messages successfully analyzed.
        """
        conn = get_db(self.db_path)
        count = 0

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
                        conn.execute("""
                            INSERT INTO analysis
                                (message_id, temperature, topic, summary, keywords, sentiment)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            row["id"],
                            result.get("temperature", 5.0),
                            result.get("topic", "general"),
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

    async def generate_digest(self, hours: int = 3) -> str | None:
        """
        Generate a digest of the last N hours of analyzed messages.
        Returns Markdown text or None if no data available.
        """
        since = datetime.utcnow() - timedelta(hours=hours)
        conn = get_db(self.db_path)

        try:
            rows = conn.execute("""
                SELECT
                    m.text,
                    s.name as source_name,
                    a.temperature,
                    a.topic,
                    a.summary
                FROM messages m
                JOIN sources s ON m.source_id = s.id
                LEFT JOIN analysis a ON a.message_id = m.id
                WHERE datetime(m.collected_at) >= datetime(?)
                  AND m.analyzed = 1
                ORDER BY a.temperature DESC
                LIMIT 30
            """, (since.isoformat(),)).fetchall()

        finally:
            conn.close()

        if not rows:
            logger.warning("No analyzed messages available for digest")
            return None

        # Format messages for the prompt
        messages_text = "\n\n".join([
            f"[{i+1}] Channel: @{row['source_name']} | Temperature: {row['temperature']}/10\n"
            f"Topic: {row['topic']}\n"
            f"Text: {row['text'][:300]}"
            for i, row in enumerate(rows)
        ])

        period = f"last {hours} hours"
        prompt = DIGEST_PROMPT.format(
            period=period,
            count=len(rows),
            messages=messages_text,
        )

        try:
            digest = await self.llm.complete(
                user_prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                temperature=0.4,
                max_tokens=1500,
            )

            # Persist digest to DB
            conn = get_db(self.db_path)
            try:
                conn.execute("""
                    INSERT INTO digests (content_md, period_start, period_end)
                    VALUES (?, ?, ?)
                """, (digest, since.isoformat(), datetime.utcnow().isoformat()))
                conn.commit()
            finally:
                conn.close()

            return digest

        except Exception as e:
            logger.error(f"Failed to generate digest: {e}")
            return None

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

    analyzer = NewsAnalyzer(
        db_path=db_path,
        llm_client=llm,
        interval_minutes=interval,
    )

    await analyzer.run_loop()


if __name__ == "__main__":
    asyncio.run(main())
