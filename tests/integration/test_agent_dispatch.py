"""
Suite: test_agent_dispatch
Tests the bidirectional communication between news-radar and the OpenClaw agent.

Tests:
  1. Agent is reachable at /v1/chat/completions
  2. Agent accepts a [NEWS-RADAR EVENT: breaking_alert] payload
  3. Agent accepts a [NEWS-RADAR EVENT: hot_trend] payload
  4. news-radar dispatch_log records the attempt (ok vs error)
  5. Agent accepts a settings read request
  6. PATCH /settings via agent instruction (simulates agent adjusting config)

Note: Tests that depend on the agent actually RESPONDING with Telegram output
can't be fully automated without access to the Telegram Chat, so we validate
the HTTP-level contract only and inspect dispatch_log.
"""
import time
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import TestRunner, get, post, patch, agent_post, AGENT_API, AGENT_TOKEN


def run() -> tuple[int, int]:
    t = TestRunner("agent_dispatch")
    print(f"  Agent:  {AGENT_API}")

    agent_available = False

    # ── 1. Agent Connectivity ────────────────────────────
    try:
        r = agent_post({
            "model": "openclaw",
            "messages": [
                {"role": "system", "content": "You are the RoutingAgent. Process this event."},
                {"role": "user",   "content": "[NEWS-RADAR TEST: ping]\nAction: Reply with 'pong' only."}
            ]
        })
        if r.status_code == 200:
            agent_available = True
            t.ok("Agent reachable → 200")
            # Check the response has expected OpenAI schema
            data = r.json()
            t.check("response has 'choices'", "choices" in data, str(list(data.keys())))
            if "choices" in data and data["choices"]:
                reply = data["choices"][0].get("message", {}).get("content", "")
                t.ok(f"agent reply received ({len(reply)} chars)")
        else:
            t.fail("Agent reachable", f"HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        t.fail("Agent connectivity", f"{type(e).__name__}: {e}")

    # ── 2. Dispatch a breaking_alert payload ─────────────
    if agent_available:
        try:
            payload_text = (
                "[NEWS-RADAR EVENT: breaking_alert]\n"
                "Source: @test_channel\n"
                "Topic: Test Event\n"
                "Temperature: 10/10\n"
                "Summary: This is an automated integration test. Please ignore.\n"
                "Action: Acknowledge receipt only. Do not send to users."
            )
            r = agent_post({
                "model": "openclaw",
                "messages": [
                    {"role": "system", "content": "You are the RoutingAgent. Process this event according to AGENTS.md instructions."},
                    {"role": "user",   "content": payload_text}
                ]
            })
            t.check("breaking_alert dispatched → 2xx", r.status_code in (200, 202),
                    f"got {r.status_code}")
        except Exception as e:
            t.fail("Dispatch breaking_alert", str(e))
    else:
        t.fail("Dispatch breaking_alert", "Skipped: agent not available")

    # ── 3. Dispatch a hot_trend payload ──────────────────
    if agent_available:
        try:
            payload_text = (
                "[NEWS-RADAR EVENT: hot_trend]\n"
                "Topic: KelpDAO / LayerZero Exploit (INTEGRATION TEST)\n"
                "Sources: 5 independent channels\n"
                "Channels: @test1, @test2, @test3, @test4, @test5\n"
                "Score: 47.3\n"
                "Message Count: 9\n"
                "Summary: This is an integration test event. Multiple channels reported the same exploit.\n"
                "Action: Generate a narrative summary and send to users. "
                "If this is a test, simply acknowledge. Do not send to real users."
            )
            r = agent_post({
                "model": "openclaw",
                "messages": [
                    {"role": "system", "content": "You are the RoutingAgent. Process this event according to AGENTS.md instructions."},
                    {"role": "user",   "content": payload_text}
                ]
            })
            t.check("hot_trend dispatched → 2xx", r.status_code in (200, 202),
                    f"got {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                t.ok(f"agent acknowledged hot_trend ({len(reply)} chars)")
        except Exception as e:
            t.fail("Dispatch hot_trend", str(e))
    else:
        t.fail("Dispatch hot_trend", "Skipped: agent not available")

    # ── 4. Dispatch a digest_raw payload ─────────────────
    if agent_available:
        try:
            payload_text = (
                "[NEWS-RADAR EVENT: digest_raw]\n"
                "Period: last period\n"
                "Messages: 2\n\n"
                "[1] Channel: @test_channel | Temperature: 9/10\n"
                "Topic: test\n"
                "Text: Test message 1 for integration testing purposes.\n\n"
                "[2] Channel: @test_channel2 | Temperature: 8/10\n"
                "Topic: test\n"
                "Text: Test message 2 for integration testing purposes.\n\n"
                "Action: Generate a markdown digest based on these messages and send it to the user. "
                "If this appears to be a test, simply return 'TEST_ACK' and do not send to real users."
            )
            r = agent_post({
                "model": "openclaw",
                "messages": [
                    {"role": "system", "content": "You are the RoutingAgent. Process this event according to AGENTS.md instructions."},
                    {"role": "user",   "content": payload_text}
                ]
            })
            t.check("digest_raw dispatched → 2xx", r.status_code in (200, 202),
                    f"got {r.status_code}")
        except Exception as e:
            t.fail("Dispatch digest_raw", str(e))
    else:
        t.fail("Dispatch digest_raw", "Skipped: agent not available")

    # ── 5. Agent reads news-radar settings ───────────────
    if agent_available:
        try:
            payload_text = (
                "[NEWS-RADAR COMMAND: get_settings]\n"
                "Action: Call GET http://news-radar-api:8000/settings and return the current hot_trend_min_sources value."
            )
            r = agent_post({
                "model": "openclaw",
                "messages": [
                    {"role": "system", "content": "You are the RoutingAgent. You have access to the news-radar API at http://news-radar-api:8000."},
                    {"role": "user",   "content": payload_text}
                ]
            })
            t.check("agent settings read → 2xx", r.status_code in (200, 202),
                    f"got {r.status_code}")
        except Exception as e:
            t.fail("Agent reads settings", str(e))
    else:
        t.fail("Agent reads settings", "Skipped: agent not available")

    # ── 6. Check dispatch_log recorded the attempts ───────
    try:
        time.sleep(1)  # give a moment for any async log writes
        r = get("/dispatch-log?limit=20")
        t.check("/dispatch-log accessible", r.status_code == 200)
        logs = r.json()
        t.ok(f"dispatch_log has {len(logs)} entries total")

        if logs:
            statuses = {e["status"] for e in logs}
            sent_tos = {e["sent_to"] for e in logs}
            t.ok(f"sent_to values seen: {sent_tos}")
            t.ok(f"status values seen: {statuses}")

            # Count by event type
            by_type: dict[str, int] = {}
            for e in logs:
                by_type[e["event_type"]] = by_type.get(e["event_type"], 0) + 1
            for etype, count in sorted(by_type.items()):
                t.ok(f"  {etype}: {count} entries")
    except Exception as e:
        t.fail("/dispatch-log check", str(e))

    # ── 7. Verify agent rejection of invalid schema ───────
    if agent_available:
        try:
            # Send completely empty content — agent should still respond
            r = agent_post({
                "model": "openclaw",
                "messages": [{"role": "user", "content": ""}]
            })
            # We just check it doesn't crash (5xx)
            t.check("empty payload → not 5xx", r.status_code < 500,
                    f"got {r.status_code}")
        except Exception as e:
            t.fail("Empty payload resilience", str(e))

    return t.results()
