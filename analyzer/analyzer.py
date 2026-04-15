"""
Analyzer — AI-анализ собранных сообщений.

Запускается по расписанию (ANALYZE_INTERVAL_MINUTES).
Берёт необработанные сообщения из БД → отправляет в LLM → сохраняет результат.
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
from database.schema import get_db, init_db

logger = logging.getLogger(__name__)


class NewsAnalyzer:
    """
    Батч-обработка сообщений через LLM.
    
    Логика:
    1. Берёт сообщения где analyzed=0
    2. Для каждого: запрашивает temperature, topic, summary, keywords
    3. Сохраняет в таблицу analysis
    4. Отмечает message.analyzed=1
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
        self.interval = interval_minutes * 60  # в секундах

    async def analyze_pending(self) -> int:
        """
        Обработать все необработанные сообщения.
        Возвращает количество проанализированных.
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
                        # Сохранить анализ
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

                        # Отметить как обработанное
                        conn.execute(
                            "UPDATE messages SET analyzed=1 WHERE id=?",
                            (row["id"],)
                        )
                        conn.commit()
                        count += 1

                    # Пауза чтобы не перегружать LLM
                    await asyncio.sleep(0.5)

                except Exception as e:
                    logger.error(f"Failed to analyze message {row['id']}: {e}")
                    # Помечаем как обработанное с ошибкой чтобы не зависало
                    conn.execute(
                        "UPDATE messages SET analyzed=1 WHERE id=?",
                        (row["id"],)
                    )
                    conn.commit()

        finally:
            conn.close()

        logger.info(f"✅ Analyzed {count} messages")
        return count

    async def _analyze_message(
        self,
        message_id: int,
        text: str,
        source_name: str,
    ) -> dict | None:
        """Проанализировать одно сообщение через LLM."""
        prompt = SINGLE_MESSAGE_PROMPT.format(
            source_name=source_name,
            text=text[:2000],  # обрезаем очень длинные
        )

        try:
            result = await self.llm.complete_json(
                user_prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=512,
            )

            # Валидируем temperature в диапазоне 1-10
            if "temperature" in result:
                result["temperature"] = max(1.0, min(10.0, float(result["temperature"])))

            return result

        except Exception as e:
            logger.error(f"LLM analysis failed for msg {message_id}: {e}")
            return None

    async def generate_digest(self, hours: int = 3) -> str | None:
        """
        Сгенерировать дайджест за последние N часов.
        Возвращает Markdown-текст.
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
                WHERE m.collected_at >= ?
                  AND m.analyzed = 1
                ORDER BY a.temperature DESC
                LIMIT 30
            """, (since.isoformat(),)).fetchall()

        finally:
            conn.close()

        if not rows:
            logger.warning("No analyzed messages for digest")
            return None

        # Форматируем для промпта
        messages_text = "\n\n".join([
            f"[{i+1}] Канал: @{row['source_name']} | Температура: {row['temperature']}/10\n"
            f"Тема: {row['topic']}\n"
            f"Текст: {row['text'][:300]}"
            for i, row in enumerate(rows)
        ])

        period = f"последние {hours} часа" if hours <= 4 else f"последние {hours} часов"
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

            # Сохранить дайджест в БД
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
        """Основной цикл — анализ каждые N минут."""
        logger.info(f"Starting analyzer loop (every {self.interval // 60} min)")

        while True:
            try:
                await self.analyze_pending()
            except Exception as e:
                logger.error(f"Analyzer loop error: {e}")

            await asyncio.sleep(self.interval)


async def main():
    """Точка входа для Docker."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    db_path = os.environ.get("DATABASE_PATH", "/app/data/news.db")
    interval = int(os.environ.get("ANALYZE_INTERVAL_MINUTES", "30"))

    init_db(db_path)

    llm = LLMClient()

    # Проверка доступности LLM
    logger.info(f"Checking LLM at {llm.base_url}...")
    healthy = await llm.health_check()
    if not healthy:
        logger.warning("⚠️  LLM not available yet, will retry...")
    else:
        logger.info("✅ LLM is available")

    analyzer = NewsAnalyzer(
        db_path=db_path,
        llm_client=llm,
        interval_minutes=interval,
    )

    await analyzer.run_loop()


if __name__ == "__main__":
    asyncio.run(main())
