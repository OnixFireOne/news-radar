"""
ChromaDB client — vector store for semantic search and deduplication.

Architecture:
  - One collection: "news_radar"
  - Each document = one analyzed news message
  - Metadata stored alongside embedding (source, timestamp, temperature, topic)
  - cosine similarity used for search (default in ChromaDB)

Why a separate ChromaDB container (not polymarket-ai's instance):
  - news-radar owns its data independently
  - If polymarket-ai restarts, news-radar is unaffected
  - Separate backups and volume management

Connection config (from environment):
  CHROMA_HOST       — default: chromadb (docker service name)
  CHROMA_PORT       — default: 8000
  CHROMA_COLLECTION — default: news_radar
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class ChromaClient:
    """
    Wrapper around ChromaDB for semantic operations on news messages.

    All methods that read/write embeddings assume the embedding was already
    computed by the Embedder (embedder.py) and passed in as a list[float].
    This keeps the ChromaClient free from ML dependencies.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        collection_name: str | None = None,
    ):
        self.host = host or os.getenv("CHROMA_HOST", "chromadb")
        self.port = int(port or os.getenv("CHROMA_PORT", "8000"))
        self.collection_name = collection_name or os.getenv("CHROMA_COLLECTION", "news_radar")
        self._client = None
        self._collection = None

    def _connect(self):
        """Lazy connect to ChromaDB. Called on first use."""
        if self._client is not None:
            return

        try:
            import chromadb
            self._client = chromadb.HttpClient(
                host=self.host,
                port=self.port,
            )
            # Get or create the collection
            # metadata={"hnsw:space": "cosine"} ensures cosine similarity
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            count = self._collection.count()
            logger.info(f"Connected to ChromaDB at {self.host}:{self.port}. "
                        f"Collection '{self.collection_name}' has {count} documents.")
        except ImportError:
            logger.error("chromadb not installed. Run: pip install chromadb")
            raise
        except Exception as e:
            logger.error(f"Failed to connect to ChromaDB at {self.host}:{self.port}: {e}")
            raise

    def health_check(self) -> bool:
        """Check if ChromaDB is reachable and the collection is accessible."""
        try:
            self._connect()
            # Use count() as health check — works with both v1 and v2 API
            _ = self._collection.count()
            return True
        except Exception:
            return False

    def add_message(
        self,
        message_id: int,
        embedding: list[float],
        text: str,
        source_name: str,
        timestamp: str,          # ISO format string
        temperature: float,
        topic: str,
    ) -> None:
        """
        Store a message embedding in ChromaDB.

        Args:
            message_id: SQLite message ID (used as ChromaDB document ID)
            embedding:  pre-computed embedding vector from Embedder
            text:       original message text (stored as document)
            source_name: Telegram channel @username
            timestamp:  collection time as ISO string
            temperature: LLM hype score 1.0-10.0
            topic:      LLM-assigned topic label
        """
        self._connect()

        try:
            self._collection.upsert(
                ids=[str(message_id)],
                embeddings=[embedding],
                documents=[text[:2000]],   # ChromaDB stores documents for inspection
                metadatas=[{
                    "source": source_name,
                    "timestamp": timestamp,
                    "temperature": float(temperature),
                    "topic": topic or "unknown",
                    "message_id": message_id,
                }],
            )
        except Exception as e:
            logger.error(f"Failed to add message {message_id} to ChromaDB: {e}")
            raise

    def search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        min_temperature: float = 0.0,
        since_hours: int = 168,  # 7 days default
    ) -> list[dict]:
        """
        Semantic search: find messages closest to the query embedding.

        Returns list of dicts with:
          {message_id, source, topic, temperature, distance, document}
        where distance = 1 - cosine_similarity (lower = more similar).
        """
        self._connect()

        where_filter = {}
        if min_temperature > 0:
            where_filter["temperature"] = {"$gte": min_temperature}

        try:
            kwargs = {
                "query_embeddings": [query_embedding],
                "n_results": min(limit, self._collection.count() or 1),
                "include": ["metadatas", "documents", "distances"],
            }
            if where_filter:
                kwargs["where"] = where_filter

            results = self._collection.query(**kwargs)

            output = []
            if results["ids"] and results["ids"][0]:
                for i, doc_id in enumerate(results["ids"][0]):
                    meta = results["metadatas"][0][i]
                    output.append({
                        "message_id": int(meta.get("message_id", doc_id)),
                        "source": meta.get("source", ""),
                        "topic": meta.get("topic", ""),
                        "temperature": meta.get("temperature", 0),
                        "distance": results["distances"][0][i],
                        "similarity": round(1 - results["distances"][0][i], 4),
                        "document": results["documents"][0][i],
                    })
            return output

        except Exception as e:
            logger.error(f"ChromaDB search failed: {e}")
            raise

    def find_similar(
        self,
        message_id: int,
        limit: int = 5,
    ) -> list[dict]:
        """
        Find messages semantically similar to a given message.
        The message must already be stored in ChromaDB.
        Returns empty list on any error (non-fatal).
        """
        self._connect()

        try:
            # First: get the stored embedding for this message
            result = self._collection.get(
                ids=[str(message_id)],
                include=["embeddings"],
            )
            # Use len() instead of truthiness — ChromaDB may return numpy arrays
            # which raise "truth value of array is ambiguous" with plain `if not`
            embeddings = result.get("embeddings") or []
            if len(embeddings) == 0:
                return []

            embedding = embeddings[0]
            if embedding is None or len(embedding) == 0:
                return []

            # Then: search for similar (exclude itself)
            similar = self.search(query_embedding=list(embedding), limit=limit + 1)
            # Filter out the message itself from results
            return [r for r in similar if r["message_id"] != message_id][:limit]

        except Exception as e:
            logger.warning(f"find_similar skipped for message {message_id}: {e}")
            return []  # non-fatal — analysis must continue

    def find_duplicates(
        self,
        message_ids: list[int],
        threshold: float = 0.92,
    ) -> list[list[int]]:
        """
        Among the given message IDs, find groups of near-duplicates.

        Two messages are duplicates if cosine_similarity >= threshold (default 0.92).
        Returns a list of groups, e.g. [[101, 205, 340], [88, 221]] where each
        group contains semantically identical messages from different channels.

        This is used by the digest to avoid repeating the same news N times.
        """
        self._connect()

        if not message_ids:
            return []

        try:
            # Get all embeddings in one batch call
            result = self._collection.get(
                ids=[str(mid) for mid in message_ids],
                include=["embeddings"],
            )
            if not result["ids"]:
                return []

            stored_ids = [int(doc_id) for doc_id in result["ids"]]
            embeddings = result["embeddings"]

            # Union-Find to group duplicates
            parent = {mid: mid for mid in stored_ids}

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(x, y):
                parent[find(x)] = find(y)

            # Compare each pair (O(n²) — fine for <1000 messages per window)
            import numpy as np
            emb_array = np.array(embeddings)

            for i in range(len(stored_ids)):
                for j in range(i + 1, len(stored_ids)):
                    # dot product of normalized vectors = cosine similarity
                    sim = float(np.dot(emb_array[i], emb_array[j]))
                    if sim >= threshold:
                        union(stored_ids[i], stored_ids[j])

            # Collect groups
            groups: dict[int, list[int]] = {}
            for mid in stored_ids:
                root = find(mid)
                groups.setdefault(root, []).append(mid)

            # Only return groups with 2+ members
            return [sorted(g) for g in groups.values() if len(g) >= 2]

        except Exception as e:
            logger.error(f"find_duplicates failed: {e}")
            raise

    def get_recent_ids(self, hours: int = 6) -> list[int]:
        """
        Get message IDs stored in ChromaDB within the last N hours.
        Used by the API /duplicates endpoint.

        Note: ChromaDB doesn't support time-range queries natively,
        so we use the SQLite DB for that and just verify in ChromaDB.
        This method returns ALL stored IDs (caller should pre-filter by time).
        """
        self._connect()
        try:
            result = self._collection.get(include=["metadatas"])
            if not result["ids"]:
                return []

            cutoff = datetime.utcnow() - timedelta(hours=hours)
            cutoff_str = cutoff.isoformat()

            ids = []
            for i, doc_id in enumerate(result["ids"]):
                meta = result["metadatas"][i]
                ts = meta.get("timestamp", "")
                if ts >= cutoff_str:
                    ids.append(int(meta.get("message_id", doc_id)))
            return ids

        except Exception as e:
            logger.error(f"get_recent_ids failed: {e}")
            return []

    @property
    def count(self) -> int:
        """Total number of documents in the collection."""
        self._connect()
        return self._collection.count()
