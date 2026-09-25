"""Offline behavioral checks for the standalone endpoint probe."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable, cast

import httpx
import pytest

from llm_core.probe import ProbeConfig, ProbeResult, derive_profile_draft, main, render_report, run_probes

KEY = "sk-test-secret-never-in-reports-42"


def config(model_fallback: str | None = None) -> ProbeConfig:
    return ProbeConfig(base_url="https://example.test/v1///", model="primary", api_key=KEY,
                       key_env="PROBE_TEST_KEY", profile_name="test", model_fallback=model_fallback)


def run(handler: Callable[[httpx.Request], httpx.Response], model_fallback: str | None = None) -> list[ProbeResult]:
    return run_probes(config(model_fallback), transport=httpx.MockTransport(handler))


def mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def profile(results: list[ProbeResult], protocol: str = "messages") -> dict[str, object]:
    return mapping(mapping(derive_profile_draft(results)["profiles"])[f"test.{protocol}"])


def value(profile_fields: dict[str, object], name: str) -> object:
    field = mapping(profile_fields[name])
    assert field["source"]
    return field["value"]


def messages_response() -> httpx.Response:
    return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}],
                                     "usage": {"input_tokens": 4, "output_tokens": 1}})


@pytest.mark.parametrize("escaped", [False, True])
def test_secret_never_reaches_records_report_or_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], escaped: bool,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-api-key") == KEY or request.headers.get("Authorization") == f"Bearer {KEY}"
        payload = json.dumps({"echo": KEY, "nested": [{KEY: KEY}]})
        if escaped:
            payload = payload.replace("s", "\\u0073")
        return httpx.Response(200, text=payload, headers={"x-request-id": KEY, "set-cookie": "drop-me"})

    results = run(handler)
    assert KEY not in repr(config())
    assert KEY not in repr([asdict(r) for r in results])
    assert all("set-cookie" not in r.response_headers for r in results)
    report = render_report(results, derive_profile_draft(results), {})
    assert KEY not in report
    assert "[REDACTED]" in report
    monkeypatch.setenv("PROBE_TEST_KEY", KEY)
    assert main(["--base-url", "https://example.test/v1/", "--model", "primary",
                 "--key-env", "PROBE_TEST_KEY", "--profile-name", "test", "--out-dir", str(tmp_path)],
                transport=httpx.MockTransport(handler)) == 0
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err
    reports = list(tmp_path.glob("probe-????????-??????.md"))
    assert len(reports) == 1
    assert KEY not in reports[0].read_text()
    assert "| # | Label | Status | ms |" in captured.out
    assert '"test.messages"' in captured.out


@pytest.mark.parametrize("status,expected", [(404, True), (405, True), (200, False), (401, False), (500, False)])
def test_alternative_path_only_after_404_or_405(status: int, expected: bool) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/v1/messages":
            return httpx.Response(status)
        return httpx.Response(400)

    results = run(handler)
    assert ("/v1/v1/messages" in paths) is expected
    assert results[4].skipped is not expected
    assert [r.index for r in results] == list(range(1, 10))
    assert results[7].skipped


def test_version_required_and_fallback_verified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": f"model-{i}"} for i in range(60)]})
        if request.url.path.endswith("/chat/completions") or "anthropic-version" not in request.headers:
            return httpx.Response(400)
        return messages_response()

    results = run(handler, model_fallback="backup")
    fields = profile(results)
    assert value(fields, "auth_style") == "x-api-key"
    assert value(fields, "api_version") == "2023-06-01"
    assert value(fields, "base_url") == "https://example.test/v1"
    assert value(fields, "api_key_env") == "PROBE_TEST_KEY"
    assert value(fields, "models_path") == "/models"
    assert value(fields, "max_tokens") == 1024
    assert value(mapping(fields["models"]), "default") == "primary"
    assert value(mapping(fields["models"]), "fallback") == "backup"
    ids = mapping(derive_profile_draft(results)["model_ids"])["value"]
    assert isinstance(ids, list) and len(ids) == 50


@pytest.mark.parametrize("usage,expected", [
    ({"prompt_tokens": 0, "completion_tokens": 2, "cost": 0.01}, "none"),
    ({"completion_tokens": 2}, "none"),
    ({}, "none"),
    ({"prompt_tokens": 3, "completion_tokens": 2}, "table"),
    ({"prompt_tokens": 3, "completion_tokens": 2, "cost": 0.01}, "provider"),
])
def test_chat_cost_requires_complete_usage(usage: dict[str, object], expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(201, json={"choices": [{"message": {"content": "ok"}}], "usage": usage})
        return httpx.Response(404)

    results = run(handler)
    fields = profile(results, "chat_completions")
    assert value(fields, "cost_source") == expected
    assert ("price_table" in fields) is (expected == "table")
    if expected == "table":
        assert value(fields, "price_table") == "TODO(unverified)"
    assert value(fields, "auth_style") == "bearer"
    assert "api_version" not in fields and "max_tokens" not in fields
    assert "models_path" not in fields
    assert results[8].url.endswith("/chat/completions")


@pytest.mark.parametrize("chat", [False, True])
def test_empty_response_with_billed_tokens_is_flagged(chat: bool) -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404)
        body = mapping(json.loads(request.content))
        seen.append(body)
        if chat and not request.url.path.endswith("/chat/completions"):
            return httpx.Response(400)
        if body["max_tokens"] == 1:
            if chat:
                payload: dict[str, object] = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                                               "usage": {"prompt_tokens": 5, "completion_tokens": 1}}
            else:
                payload = {"content": [], "stop_reason": "max_tokens", "usage": {"input_tokens": 5, "output_tokens": 1}}
            return httpx.Response(200, json=payload)
        return messages_response()

    results = run(handler)
    draft = derive_profile_draft(results)
    assert seen[-1]["max_tokens"] == 1
    assert all(body["max_tokens"] == 32 for body in seen[:-1])
    assert mapping(draft["empty_response_at_max_tokens"])["value"] is True
    report = render_report(results, draft, {})
    assert '"empty_response_at_max_tokens": {\n    "value": true' in report
    assert "probe 9: empty_text=True" in report


def test_timeout_is_redacted_and_run_continues() -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if count == 1:
            raise httpx.ReadTimeout(f"echo {KEY}", request=request)
        return httpx.Response(403)

    results = run(handler)
    assert results[0].error_kind == "timeout"
    assert results[0].status is None
    assert KEY not in str(results[0].error_message)
    assert results[1].status == 403
    assert results[8].skipped
    assert len(results) == 9


@pytest.mark.parametrize("key", [None, "", "  "])
def test_empty_key_exits_two_and_names_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], key: str | None,
) -> None:
    if key is None:
        monkeypatch.delenv("PROBE_TEST_KEY", raising=False)
    else:
        monkeypatch.setenv("PROBE_TEST_KEY", key)
    assert main(["--base-url", "https://example.test", "--model", "primary", "--key-env", "PROBE_TEST_KEY"]) == 2
    assert "PROBE_TEST_KEY" in capsys.readouterr().err


def test_alternative_messages_base_and_bearer_only_auth() -> None:
    def alternative(request: httpx.Request) -> httpx.Response:
        return messages_response() if request.url.path == "/v1/v1/messages" else httpx.Response(404)

    results = run(alternative)
    assert value(profile(results), "base_url") == "https://example.test/v1/v1"
    assert results[8].url == results[4].url

    def bearer(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/messages") and "Authorization" in request.headers:
            return messages_response()
        return httpx.Response(401)

    results = run(bearer)
    assert value(profile(results), "auth_style") == "bearer"
    assert "Authorization" in results[8].headers_sent


def test_optional_version_omitted_and_profiles_are_independent() -> None:
    results = run(lambda request: messages_response(), model_fallback="backup")
    assert "api_version" not in profile(results)
    assert value(mapping(profile(results, "chat_completions")["models"]), "fallback") == "backup"
    assert "chat fallback not independently probed" in str(profile(results, "chat_completions")["models"])


def test_non_json_body_is_scrubbed_before_truncation() -> None:
    results = run(lambda request: httpx.Response(502, text=KEY + "x" * 3000 + KEY))
    assert results[0].body_json is None
    assert results[0].body_text is not None and len(results[0].body_text) == 2000
    assert KEY not in repr(results)
    assert derive_profile_draft(results)["profiles"] == {}


def test_network_failure_is_classified_and_does_not_stop_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            raise httpx.ConnectError(f"cannot connect: {KEY}", request=request)
        return messages_response()

    results = run(handler)
    assert results[0].error_kind == "network" and results[0].status is None
    assert results[2].status == 200
    assert KEY not in repr(results)
