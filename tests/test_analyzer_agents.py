import pytest
import httpx
from unittest.mock import Mock, patch
from config.config_watcher import ConfigWatcher
from analyzer.analyzer import NewsAnalyzer
from analyzer.llm_client import LLMClient

@pytest.fixture
def mock_llm_client():
    client = Mock(spec=LLMClient)
    return client

@pytest.fixture
def analyzer(test_db, mock_llm_client):
    cfg_mock = Mock(spec=ConfigWatcher)
    # load_topics is called in __init__ — must return a real dict not a Mock
    cfg_mock.load_topics.return_value = {}
    # Enable agent routing for this test
    cfg_mock.get.side_effect = lambda key, default=None: True if key == "route_via_openclaw" else default
    
    # Needs a mock ChromaClient to prevent hitting real network
    with patch("analyzer.analyzer.ChromaClient") as MockChroma:
        analyzer_inst = NewsAnalyzer(test_db, mock_llm_client, cfg=cfg_mock)
        yield analyzer_inst

@pytest.mark.asyncio
async def test_route_event_to_openclaw(analyzer, mock_openclaw, monkeypatch, db_conn):
    """
    Tests that NewsAnalyzer correctly formats an event and sends it to the OpenClaw Agent,
    and writes the correct dispatch_log entry based on the Agent's response.
    """
    import os
    monkeypatch.setenv("OPENCLAW_WEBHOOK_URL", "http://agent_mock:8000/v1/chat/completions")
    monkeypatch.setenv("OPENCLAW_WEBHOOK_TOKEN", "fake_token")

    # Mock the Agent receiving the webhook
    mock_route = mock_openclaw.post("http://agent_mock:8000/v1/chat/completions").respond(
        status_code=200, 
        json={"choices": [{"message": {"content": "Agent processed event"}}]}
    )

    data = {
        "period": "12 часов",
        "message_count": 50,
        "text": "Bitcoin is up."
    }

    # Execute
    await analyzer._route_event("digest", data)

    # Verify Agent was triggered exactly once
    assert mock_route.called
    assert mock_route.call_count == 1
    
    # Check what payload we sent to the agent
    request = mock_route.calls[0].request
    assert request.headers.get("Authorization") == "Bearer fake_token"
    
    payload = request.read().decode("utf-8")
    assert "Bitcoin is up" in payload
    assert "[NEWS-RADAR EVENT: digest]" in payload

    # Verify internal DB state transition (dispatch_log)
    row = db_conn.execute("SELECT * FROM dispatch_log WHERE event_type='digest'").fetchone()
    assert row is not None
    assert row["sent_to"] == "agent"
    assert row["status"] == "ok"
    assert row["http_status"] == 200
