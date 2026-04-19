"""
Suite: test_settings
Tests the GET /settings and PATCH /settings live configuration endpoints.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import TestRunner, get, patch, RADAR_API


def run() -> tuple[int, int]:
    t = TestRunner("settings")
    print(f"  Target: {RADAR_API}/settings")

    # ── 1. GET returns full schema ───────────────────────
    try:
        r = get("/settings")
        t.check("GET /settings → 200", r.status_code == 200)
        cfg = r.json()
        required_keys = [
            "hot_trend_min_sources", "breaking_alert_min_temp",
            "route_via_openclaw", "digest_engine", "instant_alerts_temperature"
        ]
        for key in required_keys:
            t.check(f"  has key: {key}", key in cfg)
    except Exception as e:
        t.fail("GET /settings", str(e))
        return t.results()

    # ── 2. PATCH single numeric key ──────────────────────
    original_sources = cfg.get("hot_trend_min_sources", 5)
    test_value = 3
    try:
        r = patch("/settings", json={"hot_trend_min_sources": test_value})
        t.check("PATCH numeric → 200", r.status_code == 200)
        resp = r.json()
        t.check("response.status == applied", resp.get("status") == "applied", str(resp))
        t.check("response.updated has key", "hot_trend_min_sources" in resp.get("updated", {}))
        t.check("updated value correct", resp["updated"].get("hot_trend_min_sources") == test_value)
    except Exception as e:
        t.fail("PATCH numeric key", str(e))

    # ── 3. Verify change persisted via GET ───────────────
    try:
        r2 = get("/settings")
        cfg2 = r2.json()
        t.check("change persisted in GET", cfg2.get("hot_trend_min_sources") == test_value,
                f"expected {test_value}, got {cfg2.get('hot_trend_min_sources')}")
    except Exception as e:
        t.fail("Persistence check", str(e))

    # ── 4. PATCH bool key ────────────────────────────────
    try:
        r = patch("/settings", json={"instant_alerts_temperature": False})
        t.check("PATCH bool key → 200", r.status_code == 200)
        r2 = get("/settings")
        t.check("bool change persisted", r2.json().get("instant_alerts_temperature") is False)
        # Revert
        patch("/settings", json={"instant_alerts_temperature": True})
    except Exception as e:
        t.fail("PATCH bool key", str(e))

    # ── 5. PATCH string key ──────────────────────────────
    try:
        r = patch("/settings", json={"digest_engine": "legacy"})
        t.check("PATCH string key → 200", r.status_code == 200)
        r2 = get("/settings")
        t.check("string change persisted", r2.json().get("digest_engine") == "legacy")
        # Revert
        patch("/settings", json={"digest_engine": "agent"})
    except Exception as e:
        t.fail("PATCH string key", str(e))

    # ── 6. Unknown key rejected ───────────────────────────
    try:
        r = patch("/settings", json={"totally_fake_setting": 999})
        t.check("unknown key → 422", r.status_code == 422,
                f"got {r.status_code}: {r.text[:200]}")
    except Exception as e:
        t.fail("Unknown key rejection", str(e))

    # ── 7. Type coercion: string "7" → int ───────────────
    try:
        r = patch("/settings", json={"hot_trend_min_sources": "7"})
        t.check("string→int coercion → 200", r.status_code == 200)
        resp = r.json()
        t.check("coerced value is int 7", resp.get("updated", {}).get("hot_trend_min_sources") == 7)
    except Exception as e:
        t.fail("Type coercion", str(e))

    # ── 8. Restore original value ─────────────────────────
    try:
        r = patch("/settings", json={"hot_trend_min_sources": original_sources})
        t.check(f"restored original ({original_sources})", r.status_code == 200)
    except Exception as e:
        t.fail("Restore original", str(e))

    return t.results()
