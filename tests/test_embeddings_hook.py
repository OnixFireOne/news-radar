"""The embeddings hook gates BGE-m3; digest failures tell an empty window from an LLM error."""
from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from analyzer.embedder import Embedder
from analyzer.pipeline.context import CategorySpec
from database.schema import get_db
from tests.test_pipeline_characterization import _message, _setup


def crypto_category(hooks: list[str]) -> dict[str, Any]:
    return {"crypto": {"enabled": True, "sources": ["telegram"], "analyzer": "crypto", "hooks": hooks,
                       "select": "tiers", "template": "classic", "extras": []}}


def spec(hooks: tuple[str, ...]) -> CategorySpec:
    return CategorySpec(name="crypto", analyzer="crypto", hooks=hooks, select="tiers",
                        template="classic", extras=(), sources=("telegram",))


@pytest.mark.asyncio
@pytest.mark.parametrize("hooks, synced", [([], 0), (["embeddings"], 1)])
async def test_analysis_touches_embeddings_only_with_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hooks: list[str], synced: int,
) -> None:
    analyzer, values, chroma, path = _setup(tmp_path, monkeypatch)
    values["categories"] = crypto_category(hooks)
    _message(path, 1, "Plain news text", "raw", 6, analyzed=0)
    monkeypatch.setattr(analyzer, "_analyze_message", AsyncMock(return_value={
        "temperature": 6, "topic": "raw", "summary": "Summary", "keywords": [], "sentiment": "neutral"}))
    assert await analyzer.analyze_pending() == 1
    embedder = cast(Mock, analyzer.embedder)
    assert embedder.encode.called == bool(synced)
    assert chroma.add_message.called == bool(synced)
    conn = get_db(path)
    row = conn.execute("SELECT analyzed, chroma_synced FROM messages WHERE id=1").fetchone()
    conn.close()
    assert tuple(row) == (1, synced)


@pytest.mark.asyncio
@pytest.mark.parametrize("hooks", [(), ("embeddings",)])
async def test_digest_dedup_needs_embeddings_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hooks: tuple[str, ...],
) -> None:
    analyzer, values, _, path = _setup(tmp_path, monkeypatch)
    values["digest_rules"]["dedup_threshold"] = 0.85
    values["digest_templates"]["classic"]["cross_dedup"] = True
    _message(path, 1, "Hot news", "raw", 9, age_hours=1)
    dedup = Mock(side_effect=lambda rows, threshold: rows)
    cross = Mock(side_effect=lambda rows, **kwargs: (rows, []))
    monkeypatch.setattr(analyzer, "_dedup_by_similarity", dedup)
    monkeypatch.setattr(analyzer, "_dedup_against_previous_digests", cross)
    assert await analyzer.run_category(spec(hooks), None, hours=12, return_raw=True)
    assert dedup.called == cross.called == bool(hooks)
    assert cast(Mock, analyzer.embedder).encode.call_count == 0


@pytest.mark.asyncio
async def test_digest_failures_separate_empty_window_from_llm_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer, _, _, path = _setup(tmp_path, monkeypatch)
    assert await analyzer.generate_digest(hours=12) is None
    assert analyzer.digest_failures == ["no_news"]
    _message(path, 1, "Hot news", "raw", 9, age_hours=1)
    cast(Any, analyzer.llm).complete = AsyncMock(side_effect=RuntimeError("provider down"))
    assert not await analyzer.generate_digest(hours=12)
    assert analyzer.digest_failures == ["llm"]


def test_model_loads_once_under_concurrent_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[str] = []

    class SlowModel:
        def __init__(self, name: str, cache_folder: str) -> None:
            time.sleep(0.05)
            loads.append(name)

        def get_sentence_embedding_dimension(self) -> int:
            return 3

    fake = types.ModuleType("sentence_transformers")
    setattr(fake, "SentenceTransformer", SlowModel)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    embedder = Embedder("bge", "/tmp")
    threads = [threading.Thread(target=embedder._load) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert loads == ["bge"]
