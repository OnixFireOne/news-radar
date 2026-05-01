import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock

@pytest.fixture
def test_data(db_conn):
    """Insert a source + one unanalyzed message into the test DB."""
    db_conn.execute("INSERT INTO sources (id, name, type, active) VALUES (1, 'test_channel', 'telegram', 1)")
    db_conn.execute(
        "INSERT INTO messages (id, external_id, source_id, text, analyzed, chroma_synced) "
        "VALUES (101, '999', 1, 'Long test message text that exceeds the minimum length requirement.', 0, 0)"
    )
    db_conn.commit()
    return db_conn


@pytest.mark.asyncio
async def test_analyzer_updates_chroma_synced_flag(test_db, test_data, mocker):
    """
    Regression test for the chroma_synced bug:
    When a message is analyzed, BOTH analyzed=1 AND chroma_synced=1 must be set.
    If chroma_synced stays at 0, TrendTracker will not see the message (WHERE chroma_synced=1).
    """
    from analyzer.analyzer import NewsAnalyzer

    # 1. Mock ChromaDB: no real vector DB needed
    mock_chroma = mocker.patch("analyzer.analyzer.ChromaClient").return_value
    mock_chroma.find_duplicates.return_value = []
    mock_chroma.add_message = Mock()

    # 2. Mock Embedder: no 1GB model download during tests
    mock_embedder = mocker.patch("analyzer.analyzer.get_embedder").return_value
    mock_embedder.encode = Mock(return_value=[0.1] * 1024)

    # 3. Mock LLM client — this is the async method the analyzer awaits
    mock_llm = Mock()

    # 4. Mock ConfigWatcher with realistic numeric values (avoids MagicMock > float errors)
    cfg_mock = Mock()
    cfg_mock.load_topics.return_value = {}
    cfg_mock.get.side_effect = lambda key, default=None: {
        "route_via_openclaw":   False,
        "breaking_alert_min_temp": 10,
        "instant_alerts_temperature": False,
        "min_message_length": 30,
        "dedup_threshold": 0.92,
    }.get(key, default)

    analyzer = NewsAnalyzer(test_db, mock_llm, cfg=cfg_mock)
    analyzer.chroma   = mock_chroma
    analyzer.embedder = mock_embedder

    # 5. Mock the internal async analyze method to return a canned result
    mocker.patch.object(
        analyzer, "_analyze_message",
        new=AsyncMock(return_value={
            "topic": "Bitcoin",
            "summary": "BTC pump",
            "temperature": 8.0,
            "keywords": ["crypto"],
            "sentiment": "positive",
        })
    )

    # Execute
    analyzed_count = await analyzer.analyze_pending()

    # Assertions
    assert analyzed_count == 1, "Expected 1 message to be analyzed"

    row = test_data.execute(
        "SELECT analyzed, chroma_synced FROM messages WHERE id=101"
    ).fetchone()

    assert row["analyzed"] == 1, \
        "Bug: message not marked as analyzed=1"
    assert row["chroma_synced"] == 1, \
        "Bug: chroma_synced=0 — TrendTracker would go blind! (the bug we fixed earlier)"

    # Verify analysis results were saved
    analysis = test_data.execute(
        "SELECT topic, temperature FROM analysis WHERE message_id=101"
    ).fetchone()
    assert analysis is not None, "Analysis row was not written to DB"
    assert analysis["temperature"] == 8.0
