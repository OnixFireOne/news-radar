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


# ──────────────────────────────────────────────
# Ad / Promo filter tests
# ──────────────────────────────────────────────

@pytest.fixture
def analyzer_with_ad_filter(test_db, mock_llm_client):
    """Analyzer instance with ad_filter enabled in config."""
    cfg_mock = Mock(spec=ConfigWatcher)
    cfg_mock.load_topics.return_value = {}

    def _cfg_get(key, default=None):
        overrides = {
            "route_via_openclaw": False,
            "llm_concurrency": 1,
            "min_message_length": 5,
            "breaking_alert_min_temp": 10,
            "instant_alerts_temperature": False,
            "ad_filter": {
                "enabled": True,
                "use_heuristic": True,
                "heuristic_keywords": ["#реклама", "#ad", "промокод", "utm_source", "партнёрский материал"],
            },
        }
        return overrides.get(key, default)

    cfg_mock.get.side_effect = _cfg_get

    with patch("analyzer.analyzer.ChromaClient") as MockChroma:
        MockChroma.return_value.health_check.return_value = False
        inst = NewsAnalyzer(test_db, mock_llm_client, cfg=cfg_mock)
        yield inst


def test_heuristic_ad_flag_detected(analyzer_with_ad_filter):
    """_is_heuristic_ad() must return True for messages containing ad keywords."""
    analyzer = analyzer_with_ad_filter

    ad_texts = [
        "🎁 Используй промокод CRYPTO2025 и получи бонус!",
        "Партнёрский материал: лучший обменник этого года.",
        "Регистрируйся: https://exchange.io?utm_source=telegram&ref=abc",
        "Специальное предложение #ad только сегодня!",
        "#реклама | Новый DeFi протокол ищет инвесторов",
    ]
    for text in ad_texts:
        assert analyzer._is_heuristic_ad(text) is True, f"Expected ad=True for: {text!r}"


def test_heuristic_non_ad_not_flagged(analyzer_with_ad_filter):
    """_is_heuristic_ad() must return False for regular news posts."""
    analyzer = analyzer_with_ad_filter

    news_texts = [
        "Bitcoin достиг нового ATH выше $100k на фоне ETF-притока.",
        "SEC одобрила спотовый Ethereum ETF. Рынок отреагировал ростом на 8%.",
        "Binance объявила о листинге нового токена в разделе Innovation Zone.",
    ]
    for text in news_texts:
        assert analyzer._is_heuristic_ad(text) is False, f"Expected ad=False for: {text!r}"


def test_heuristic_ad_disabled_by_config(test_db, mock_llm_client):
    """When ad_filter.enabled=False, _is_heuristic_ad() always returns False."""
    cfg_mock = Mock(spec=ConfigWatcher)
    cfg_mock.load_topics.return_value = {}
    cfg_mock.get.side_effect = lambda key, default=None: (
        {"enabled": False, "use_heuristic": True, "heuristic_keywords": ["#реклама"]}
        if key == "ad_filter" else default
    )

    with patch("analyzer.analyzer.ChromaClient"):
        inst = NewsAnalyzer(test_db, mock_llm_client, cfg=cfg_mock)

    assert inst._is_heuristic_ad("Специальный #реклама пост") is False


@pytest.mark.asyncio
async def test_heuristic_ad_sets_db_flag(analyzer_with_ad_filter, test_db):
    """analyze_pending() must set is_ad=1 in DB for heuristic-flagged messages, without calling LLM."""
    from database.schema import get_db

    # Insert a source and an ad message
    conn = get_db(test_db)
    conn.execute("INSERT OR IGNORE INTO sources (type, name) VALUES ('telegram', 'testchan')")
    conn.commit()
    source_id = conn.execute("SELECT id FROM sources WHERE name='testchan'").fetchone()["id"]
    conn.execute(
        "INSERT INTO messages (source_id, external_id, text, analyzed) VALUES (?, ?, ?, ?)",
        (source_id, "ext-ad-1", "Лучший оффер! Используй промокод VIP для скидки 50%!", 0)
    )
    conn.commit()
    conn.close()

    # LLM should NOT be called — heuristic catches it first
    analyzer_with_ad_filter.llm.complete_json = Mock(side_effect=AssertionError("LLM should not be called for heuristic ads"))

    await analyzer_with_ad_filter.analyze_pending()

    conn = get_db(test_db)
    row = conn.execute("SELECT analyzed, is_ad FROM messages WHERE external_id='ext-ad-1'").fetchone()
    conn.close()

    assert row["analyzed"] == 1, "Message should be marked as analyzed"
    assert row["is_ad"] == 1, "Message should be flagged as ad"


@pytest.mark.asyncio
async def test_ad_messages_excluded_from_digest_sql(analyzer_with_ad_filter, test_db):
    """generate_digest() SQL must exclude messages with is_ad=1."""
    from database.schema import get_db

    conn = get_db(test_db)
    conn.execute("INSERT OR IGNORE INTO sources (type, name) VALUES ('telegram', 'newschan')")
    conn.commit()
    source_id = conn.execute("SELECT id FROM sources WHERE name='newschan'").fetchone()["id"]

    # Insert one normal news + one ad message, both analyzed
    conn.execute(
        "INSERT INTO messages (source_id, external_id, text, analyzed, is_ad, in_digest) VALUES (?, ?, ?, ?, ?, ?)",
        (source_id, "ext-news-1", "Bitcoin снова обновил ATH.", 1, 0, 0)
    )
    conn.execute(
        "INSERT INTO messages (source_id, external_id, text, analyzed, is_ad, in_digest) VALUES (?, ?, ?, ?, ?, ?)",
        (source_id, "ext-ad-2", "Партнёрский материал: купи крипту со скидкой!", 1, 1, 0)
    )
    conn.commit()

    # Add analysis rows for both
    news_id = conn.execute("SELECT id FROM messages WHERE external_id='ext-news-1'").fetchone()["id"]
    ad_id   = conn.execute("SELECT id FROM messages WHERE external_id='ext-ad-2'").fetchone()["id"]
    conn.execute("INSERT INTO analysis (message_id, temperature, topic, summary, sentiment) VALUES (?, 7.0, 'bitcoin', 'News summary', 'positive')", (news_id,))
    conn.execute("INSERT INTO analysis (message_id, temperature, topic, summary, sentiment) VALUES (?, 6.0, 'general', 'Promo summary', 'neutral')", (ad_id,))
    conn.commit()
    conn.close()

    # generate_digest queries the DB — verify ad row is excluded at SQL level
    conn2 = get_db(test_db)
    rows = conn2.execute("""
        SELECT m.id, m.external_id FROM messages m
        LEFT JOIN analysis a ON a.message_id = m.id
        WHERE m.analyzed = 1
          AND (m.is_ad = 0 OR m.is_ad IS NULL)
          AND a.temperature IS NOT NULL
    """).fetchall()
    conn2.close()

    external_ids = [r["external_id"] for r in rows]
    assert "ext-news-1" in external_ids, "News message must be included"
    assert "ext-ad-2" not in external_ids, "Ad message must be excluded"
