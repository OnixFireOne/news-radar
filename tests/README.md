# News Radar — Integration Tests

Tests for validating the interaction between:
- **`news-radar` API** — core endpoints, settings, dispatch log
- **`OpenClaw Agent`** — sending events, receiving acknowledgements

## Structure

```
tests/
├── run_all.py                          # Main runner
└── integration/
    ├── __init__.py                     # Shared utils (TestRunner, HTTP helpers)
    ├── test_api_endpoints.py           # All /feed, /stats, /trends, /dispatch-log etc.
    ├── test_settings.py                # GET/PATCH /settings validation + persistence
    └── test_agent_dispatch.py          # Agent comms: breaking_alert, hot_trend, digest_raw
```

## Running

```bash
# All suites
python tests/run_all.py

# Single suite
python tests/run_all.py api
python tests/run_all.py agent
python tests/run_all.py settings
```

## Environment Variables

| Variable           | Default                      | Description              |
|--------------------|------------------------------|--------------------------|
| `RADAR_API_URL`    | `http://localhost:8100`      | news-radar API base URL  |
| `AGENT_API_URL`    | `http://localhost:18789`     | OpenClaw gateway URL     |
| `OPENCLAW_API_TOKEN` | `` (empty)                 | Bearer token for agent   |

## What each suite tests

### `api` — Endpoint availability
- `/health`, `/stats`, `/feed`, `/topics`, `/trends`, `/sources`
- `/digest/latest`, `/dispatch-log`
- Schema validation of response fields

### `settings` — Live configuration
- GET returns all keys including new `hot_trend_min_sources`, `breaking_alert_min_temp`
- PATCH persists changes (re-read via GET)
- Type coercion (`"7"` → `int 7`)
- Unknown key → `422`
- All changes reverted after test

### `agent` — Bidirectional communication
- Agent reachability at `/v1/chat/completions`
- OpenAI schema compliance (`choices[].message.content`)
- `breaking_alert` payload accepted
- `hot_trend` payload accepted + acknowledged
- `digest_raw` payload sent correctly
- `dispatch_log` records all attempts with correct `sent_to`/`status`
