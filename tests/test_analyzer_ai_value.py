"""Value profile integration uses the existing preflight and persistence pipeline."""

from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, Mock

import pytest

import analyzer.analyzer as module
from analyzer.value_classifier import ClassifyOutcome, ValueVerdict
from database.schema import get_db, init_db


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = str(tmp_path / "value.db")
    init_db(path)
    conn = get_db(path)
    conn.execute("INSERT INTO sources (type, name) VALUES ('rss', 'feed')")
    conn.commit()
    source_id = conn.execute("SELECT id FROM sources WHERE name='feed'").fetchone()[0]
    conn.close()
    cfg_values = {"categories": {"articles": {
                      "enabled": True, "sources": ["rss", "hackernews"], "analyzer": "ai_value",
                      "hooks": ["embeddings"], "select": "quotas", "template": "ai_value", "extras": []}},
                  "min_message_length": 1,
                  "llm_concurrency": 3, "instant_alerts_temperature": False,
                  "ad_filter": {"enabled": True, "use_heuristic": True, "heuristic_keywords": ["#ad"]}}
    cfg = Mock()
    cfg.get.side_effect = lambda key, default=None: cfg_values.get(key, default)
    cfg.load_topics.return_value = {"canonical_agents": {"aliases": ["agents"]}}
    chroma = Mock()
    chroma.health_check.return_value = False
    embedder = Mock()
    embedder.encode.return_value = [1.0, 0.0]
    monkeypatch.setattr(module, "ChromaClient", lambda: chroma)
    monkeypatch.setattr(module, "get_embedder", lambda: embedder)
    monkeypatch.setattr(module, "is_llm_locked", lambda: False)
    llm = SimpleNamespace(router=object(), complete_json=AsyncMock(return_value={
        "temperature": 6, "topic": "crypto", "summary": "Legacy summary", "keywords": []}))
    classifier = SimpleNamespace(calls=[], classify=AsyncMock())
    factory = Mock(return_value=classifier)
    monkeypatch.setattr(module, "LLMValueClassifier", factory)
    analyzer = module.NewsAnalyzer(path, llm, batch_size=20, cfg=cfg)

    def insert(text="A practical AI article with useful details"):
        conn = get_db(path)
        cursor = conn.execute("INSERT INTO messages (source_id, external_id, text) VALUES (?, ?, ?)",
                              (source_id, str(uuid4()), text))
        mid = cursor.lastrowid
        conn.commit()
        conn.close()
        return mid

    def read(mid):
        conn = get_db(path)
        msg = dict(conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone())
        row = conn.execute("SELECT * FROM analysis WHERE message_id=?", (mid,)).fetchone()
        conn.close()
        return msg, dict(row) if row else None

    return SimpleNamespace(path=path, analyzer=analyzer, llm=llm, cfg=cfg_values,
                           classifier=classifier, factory=factory, chroma=chroma,
                           insert=insert, read=read)


def verdict(is_ad=False, summary="Кратко"):
    return ValueVerdict(8, "tutorial", 9, True, "Практический вывод", "agents", summary, ["агенты"], is_ad)


@pytest.mark.asyncio
@pytest.mark.parametrize("is_ad, summary", [(False, "Кратко"), (True, "Кратко"), (False, "")])
async def test_verdict_persisted(setup, is_ad, summary):
    mid = setup.insert()
    setup.classifier.classify.return_value = [ClassifyOutcome(str(mid), verdict(is_ad, summary), None, "tool")]
    assert await setup.analyzer.analyze_pending() == 1
    msg, row = setup.read(mid)
    assert msg["analyzed"] == msg["chroma_synced"] == 1
    assert msg["is_ad"] == int(is_ad)
    assert {key: row[key] for key in ("temperature", "topic", "summary", "keywords", "content_type",
                                    "value_score", "has_outcome", "takeaway")} == {
        "temperature": 8, "topic": "canonical_agents", "summary": summary, "keywords": '["агенты"]',
        "content_type": "tutorial", "value_score": 9, "has_outcome": 1, "takeaway": "Практический вывод"}
    setup.factory.assert_called_once_with(setup.llm.router, task="classify", concurrency=3)
    items = setup.classifier.classify.call_args.args[0]
    assert len(items) == 1 and items[0].id == str(mid) and items[0].source == "feed"
    setup.chroma.add_message.assert_called_once()
    setup.llm.complete_json.assert_not_called()


@pytest.mark.asyncio
async def test_error_stays_pending(setup, caplog):
    mid = setup.insert()
    setup.classifier.classify.return_value = [ClassifyOutcome(str(mid), None, "broken response", "text")]
    assert await setup.analyzer.analyze_pending() == 0
    msg, row = setup.read(mid)
    assert msg["analyzed"] == 0 and row is None
    assert "broken response" in caplog.text
    setup.chroma.add_message.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile, catalog", [("crypto", True), ("ai_value", False)])
async def test_crypto_and_missing_router_category(setup, profile, catalog, caplog):
    setup.cfg["categories"]["articles"]["analyzer"] = profile
    if not catalog:
        setup.llm.router = None
    mid = setup.insert()
    assert await setup.analyzer.analyze_pending() == (1 if catalog else 0)
    setup.factory.assert_not_called()
    msg, row = setup.read(mid)
    if catalog:
        setup.llm.complete_json.assert_awaited_once()
        assert msg["analyzed"] == 1 and row["content_type"] is None
        assert row["summary"] == "Legacy summary"
    else:
        setup.llm.complete_json.assert_not_awaited()
        assert msg["analyzed"] == 0 and row is None
        assert caplog.text.count("requires a catalog router") == 1


@pytest.mark.asyncio
async def test_category_analyzer_is_read_each_cycle(setup):
    setup.cfg["categories"]["articles"]["analyzer"] = "crypto"
    setup.insert()
    assert await setup.analyzer.analyze_pending() == 1
    setup.cfg["categories"]["articles"]["analyzer"] = "ai_value"
    mid = setup.insert()
    setup.classifier.classify.return_value = [ClassifyOutcome(str(mid), verdict(), None, "tool")]
    assert await setup.analyzer.analyze_pending() == 1
    setup.factory.assert_called_once()
    setup.llm.complete_json.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_value", [True, False])
async def test_duplicate_copies_sqlite_value_or_classifies(setup, has_value):
    original = setup.insert("Original article")
    conn = get_db(setup.path)
    conn.execute("UPDATE messages SET analyzed=1, is_ad=1 WHERE id=?", (original,))
    conn.execute("INSERT INTO analysis (message_id, temperature, topic, summary, keywords, "
                 "content_type, value_score, has_outcome, takeaway) VALUES (?, 7, 'models', "
                 "'Original summary', '[]', ?, ?, ?, ?)",
                 (original, "research" if has_value else None, 8 if has_value else None,
                  1 if has_value else 0, "Original takeaway" if has_value else None))
    conn.commit()
    conn.close()
    setup.chroma.health_check.return_value = True
    setup.chroma.search.return_value = [{"message_id": original, "similarity": 0.95,
                                        "topic": "wrong", "temperature": 1, "document": "wrong"}]
    mid = setup.insert()
    setup.classifier.classify.return_value = ([] if has_value else
        [ClassifyOutcome(str(mid), verdict(), None, "tool")])
    assert await setup.analyzer.analyze_pending() == 1
    msg, row = setup.read(mid)
    if has_value:
        assert row["content_type"] == "research" and row["value_score"] == 8
        assert row["has_outcome"] == 1 and row["takeaway"] == "Original takeaway"
        assert row["summary"] == "Original summary" and row["topic"] == "models" and row["temperature"] == 7
        assert msg["is_ad"] == 1
        assert setup.classifier.classify.call_args.args[0] == []
    else:
        assert row["content_type"] == "tutorial"
        assert len(setup.classifier.classify.call_args.args[0]) == 1


@pytest.mark.asyncio
async def test_heuristic_ads_are_not_sent_to_classifier(setup):
    ad = setup.insert("Promotional #ad")
    normal = setup.insert()
    setup.classifier.classify.return_value = [ClassifyOutcome(str(normal), verdict(), None, "tool")]
    assert await setup.analyzer.analyze_pending() == 2
    assert [item.id for item in setup.classifier.classify.call_args.args[0]] == [str(normal)]
    msg, row = setup.read(ad)
    assert msg["analyzed"] == msg["is_ad"] == 1 and row is None


@pytest.mark.asyncio
async def test_single_classify_call_for_all_pending_rows(setup):
    ids = [setup.insert(f"Article number {i}") for i in range(12)]
    setup.classifier.classify.return_value = [ClassifyOutcome(str(mid), verdict(), None, "tool") for mid in ids]
    assert await setup.analyzer.analyze_pending() == 12
    setup.classifier.classify.assert_awaited_once()
    assert {item.id for item in setup.classifier.classify.call_args.args[0]} == {str(mid) for mid in ids}


@pytest.mark.asyncio
async def test_digest_uses_value_quota_selection_after_dedup(setup):
    setup.cfg.update(digest_template="ai_value", digest_templates={"ai_value": {
        "max_items": 8, "cross_dedup": False, "ongoing_trends": False, "lookback_digests": 0}})
    records = [("tutorial", 8, 3), ("hype_news", 9, 10), ("hype_news", 7, 9),
               ("crypto", 10, 10), ("research", None, 9), ("research", 9, None)]
    ids = []
    for index, (kind, score, temperature) in enumerate(records):
        mid = setup.insert(f"Article{index}")
        ids.append(mid)
        conn = get_db(setup.path)
        conn.execute("UPDATE messages SET analyzed=1 WHERE id=?", (mid,))
        conn.execute("INSERT INTO analysis (message_id, temperature, topic, summary, content_type, value_score) "
                     "VALUES (?, ?, 'agents', 'Summary', ?, ?)", (mid, temperature, kind, score))
        conn.commit()
        conn.close()
    setup.analyzer._dedup_by_similarity = Mock(side_effect=lambda rows, threshold: rows)
    text = await setup.analyzer.generate_digest(hours=12, return_raw=True)
    setup.analyzer._dedup_by_similarity.assert_called_once()
    assert "Article0" in text and "Article1" in text
    assert all(f"Article{i}" not in text for i in range(2, 6))
    assert text.index("Article1") < text.index("Article0")
    assert [setup.read(mid)[0]["in_digest"] for mid in ids] == [2, 2, 0, 0, 0, 0]


@pytest.mark.asyncio
async def test_classifier_call_summary_reports_unknown_cost(setup, caplog):
    from analyzer.value_classifier import CallStats

    mid = setup.insert()
    setup.classifier.classify.return_value = [ClassifyOutcome(str(mid), verdict(), None, "tool")]
    setup.classifier.calls = [CallStats(1.5, 10, 5, 0.01, "table", "test", "m", "tool", None),
                              CallStats(2.0, 20, 7, None, "none", "test", "m", "text", None)]
    with caplog.at_level("INFO", logger="analyzer.analyzer"):
        await setup.analyzer.analyze_pending()
    summaries = [r.message for r in caplog.records if r.message.startswith("Value classification:")]
    assert len(summaries) == 1
    assert "calls=2 prompt_tokens=30 completion_tokens=12 cost_usd=None latency_seconds=3.500" in summaries[0]
