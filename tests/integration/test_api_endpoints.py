"""
Suite: test_api_endpoints
Tests all core news-radar API endpoints for basic availability and schema.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # makes utils importable
from utils import TestRunner, get, post, RADAR_API


def run() -> tuple[int, int]:
    t = TestRunner("api_endpoints")
    print(f"  Target: {RADAR_API}")

    # ── Health ──────────────────────────────────────────
    try:
        r = get("/health")
        t.check("/health → 200", r.status_code == 200)
        data = r.json()
        t.check("/health.status == ok", data.get("status") == "ok", str(data))
        t.check("/health has chroma field", "chroma" in data)
    except Exception as e:
        t.fail("/health", str(e))

    # ── Settings ────────────────────────────────────────
    try:
        r = get("/settings")
        t.check("/settings → 200", r.status_code == 200)
        cfg = r.json()
        for key in ("hot_trend_min_sources", "breaking_alert_min_temp", "route_via_openclaw"):
            t.check(f"/settings has '{key}'", key in cfg, f"keys: {list(cfg)}")
    except Exception as e:
        t.fail("/settings GET", str(e))

    # ── Feed ────────────────────────────────────────────
    try:
        r = get("/feed?limit=5&hours=24")
        t.check("/feed → 200", r.status_code == 200)
        feed = r.json()
        t.check("/feed returns list", isinstance(feed, list))
        if feed:
            msg = feed[0]
            for field in ("id", "text", "source_name", "collected_at"):
                t.check(f"/feed[0].{field} exists", field in msg)
    except Exception as e:
        t.fail("/feed", str(e))

    # ── Topics ──────────────────────────────────────────
    try:
        r = get("/topics?hours=24&limit=5")
        t.check("/topics → 200", r.status_code == 200)
        t.check("/topics returns list", isinstance(r.json(), list))
    except Exception as e:
        t.fail("/topics", str(e))

    # ── Stats ───────────────────────────────────────────
    try:
        r = get("/stats")
        t.check("/stats → 200", r.status_code == 200)
        stats = r.json()
        for field in ("total_messages", "analyzed_messages", "active_sources"):
            t.check(f"/stats.{field} exists", field in stats)
        t.check("/stats.total_messages > 0", stats.get("total_messages", 0) > 0,
                f"got {stats.get('total_messages')}")
    except Exception as e:
        t.fail("/stats", str(e))

    # ── Dispatch Log ─────────────────────────────────────
    try:
        r = get("/dispatch-log?limit=10")
        t.check("/dispatch-log → 200", r.status_code == 200)
        logs = r.json()
        t.check("/dispatch-log returns list", isinstance(logs, list))
        if logs:
            entry = logs[0]
            for field in ("event_type", "sent_to", "status", "created_at"):
                t.check(f"/dispatch-log[0].{field} exists", field in entry)
    except Exception as e:
        t.fail("/dispatch-log", str(e))

    # ── Trends ──────────────────────────────────────────
    try:
        r = get("/trends?limit=5")
        t.check("/trends → 200", r.status_code == 200)
        trends = r.json()
        t.check("/trends returns list", isinstance(trends, list))
        if trends:
            tr = trends[0]
            for field in ("id", "topic", "unique_sources", "status"):
                t.check(f"/trends[0].{field} exists", field in tr)
    except Exception as e:
        t.fail("/trends", str(e))

    # ── Digest latest ───────────────────────────────────
    try:
        r = get("/digest/latest")
        # 404 is OK if no digest exists yet
        t.check("/digest/latest → 200 or 404",
                r.status_code in (200, 404),
                f"got {r.status_code}")
    except Exception as e:
        t.fail("/digest/latest", str(e))

    # ── Sources ─────────────────────────────────────────
    try:
        r = get("/sources")
        t.check("/sources → 200", r.status_code == 200)
        sources = r.json()
        t.check("/sources returns list", isinstance(sources, list))
        t.check("/sources has entries", len(sources) > 0, f"got {len(sources)}")
    except Exception as e:
        t.fail("/sources", str(e))

    return t.results()
