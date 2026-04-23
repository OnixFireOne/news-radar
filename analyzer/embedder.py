"""
Embedder — sentence-transformers wrapper for BGE-m3 (multilingual embeddings).

Why BGE-m3:
  - Best multilingual model: Russian + English + 100 languages
  - 1024-dim embeddings, cosine similarity works great for news deduplication
  - Open source, runs locally, no API costs

Why sentence-transformers (not Ollama):
  - No extra service to manage
  - Model cached locally after first download (~570 MB)
  - Loaded once per process, then very fast (10-50ms per message)

Usage:
    from analyzer.embedder import get_embedder
    embedder = get_embedder()
    vector = embedder.encode("Bitcoin ETF outflows hit record")
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level singleton — loaded once, reused for entire process lifetime
_embedder: Optional["Embedder"] = None


class Embedder:
    """
    Wraps SentenceTransformer (BGE-m3) with lazy initialization.

    The model is large (~570MB), so we load it only on first call
    and keep it in memory for the duration of the process.
    """

    def __init__(self, model_name: str, cache_dir: str):
        import threading
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None  # lazy load
        self._lock = threading.Lock()

    def _load(self):
        """Load the model into memory. Called on first encode() call."""
        if self._model is not None:
            return

        with self._lock:
            if self._model is not None:
                return
            logger.info(f"Loading embedding model '{self.model_name}' (first run may take a while)...")

        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(
                self.model_name,
                cache_folder=self.cache_dir,
            )
            logger.info(f"Embedding model loaded. Dim={self._model.get_sentence_embedding_dimension()}")
        except ImportError:
            logger.error("sentence-transformers not installed. Run: pip install sentence-transformers")
            raise
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            raise

    def encode(self, text: str) -> list[float]:
        """
        Encode text into a vector embedding.

        Args:
            text: any text, Russian or English, up to 8192 tokens

        Returns:
            List of floats (1024 dimensions for BGE-m3)
        """
        self._load()

        # Truncate very long texts — BGE-m3 max is 8192 tokens
        # but news messages rarely exceed 512 tokens
        text = text.strip()[:4096]

        try:
            # normalize_embeddings=True → cosine similarity = dot product
            # This is what ChromaDB uses by default
            vector = self._model.encode(
                text,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return vector.tolist()
        except Exception as e:
            logger.error(f"Failed to encode text: {e}")
            raise

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Encode multiple texts at once (faster than one-by-one).
        Useful for bulk re-indexing.
        """
        self._load()
        texts = [t.strip()[:4096] for t in texts]
        vectors = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=32,
        )
        return [v.tolist() for v in vectors]

    @property
    def dimension(self) -> int:
        """Embedding vector size (1024 for BGE-m3)."""
        self._load()
        return self._model.get_sentence_embedding_dimension()


def get_embedder() -> Embedder:
    """
    Get the global Embedder singleton.
    Creates it on first call, returns the same instance afterwards.

    Model name and cache dir are read from environment:
      EMBEDDING_MODEL      — default: BAAI/bge-m3
      EMBEDDING_CACHE_DIR  — default: /app/data/models
    """
    global _embedder
    if _embedder is None:
        model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
        cache_dir = os.getenv("EMBEDDING_CACHE_DIR", "/app/data/models")
        _embedder = Embedder(model_name=model_name, cache_dir=cache_dir)
    return _embedder
