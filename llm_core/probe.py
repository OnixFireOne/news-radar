"""Opt-in endpoint diagnostics: python -m llm_core.probe (never used at runtime)."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Mapping, Sequence

import httpx

from llm_core.mask import mask_secret

TODO = "TODO(unverified)"
RESPONSE_HEADERS = ("content-type", "anthropic-version", "x-request-id", "retry-after")


@dataclass(frozen=True)
class ProbeConfig:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    key_env: str = TODO
    model_fallback: str | None = None
    api_version: str = "2023-06-01"
    timeout_sec: float = 15
    profile_name: str = TODO


@dataclass
class ProbeResult:
    index: int
    label: str
    method: str
    url: str
    headers_sent: dict[str, str]
    request_body: dict[str, object] | None
    status: int | None = None
    duration_ms: int = 0
    response_headers: dict[str, str] = field(default_factory=dict)
    body_text: str | None = None
    body_json: object = None
    error_kind: Literal["timeout", "network"] | None = None
    error_message: str | None = None
    skipped: bool = False
    skip_reason: str | None = None
    profile_name: str = TODO
    key_env: str = TODO


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text


def _safe_json(value: object, secret: str) -> object:
    # Scrub decoded strings as well: JSON escapes can hide literal key matches.
    if isinstance(value, str):
        return _scrub(value, secret)
    if isinstance(value, list):
        return [_safe_json(item, secret) for item in value]
    if isinstance(value, dict):
        return {_scrub(str(k), secret): _safe_json(v, secret) for k, v in value.items()}
    return value


def _mapping(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return {}


def _body(model: str, chat: bool = False) -> dict[str, object]:
    messages = [{"role": "user", "content": "reply with ok"}]
    body: dict[str, object] = {"model": model, "max_tokens": 32, "messages": messages}
    if chat:
        messages.insert(0, {"role": "system", "content": "probe"})
    else:
        body["system"] = "probe"
    return body


def _ok(result: ProbeResult | None) -> bool:
    return result is not None and not result.skipped and result.status is not None and 200 <= result.status < 300


def run_probes(config: ProbeConfig, *, transport: httpx.BaseTransport | None = None) -> list[ProbeResult]:
    """Run sequentially, without retries; injected transports make all probes offline-testable."""
    if not config.api_key:
        raise ValueError("API key must not be empty")
    if not math.isfinite(config.timeout_sec) or config.timeout_sec <= 0:
        raise ValueError("timeout_sec must be positive and finite")
    base = config.base_url.rstrip("/")
    key = config.api_key
    xkey = {"x-api-key": key}
    bearer = {"Authorization": f"Bearer {key}"}
    versioned = {**xkey, "anthropic-version": config.api_version}
    results: list[ProbeResult] = []
    # Disable implicit proxy/certificate environment reads; only main reads key_env.
    with httpx.Client(transport=transport, trust_env=False, follow_redirects=False) as client:
        def execute(index: int, label: str, path: str, headers: dict[str, str],
                    body: dict[str, object] | None = None, reason: str | None = None) -> None:
            sent = dict(headers)
            if body is not None:
                sent["Content-Type"] = "application/json"
            masked = {
                name: mask_secret(key) if value == key else
                f"Bearer {mask_secret(key)}" if value == f"Bearer {key}" else _scrub(value, key)
                for name, value in sent.items()
            }
            result = ProbeResult(
                index, _scrub(label, key), "GET" if body is None else "POST",
                _scrub(base + path, key), masked,
                _mapping(_safe_json(body, key)) if body is not None else None,
                skipped=reason is not None, skip_reason=reason,
                profile_name=_scrub(config.profile_name, key), key_env=_scrub(config.key_env, key),
            )
            results.append(result)
            if reason is not None:
                return
            started = time.perf_counter()
            try:
                response = client.request(result.method, base + path, headers=sent,
                                          json=body, timeout=config.timeout_sec)
                result.status = response.status_code
                result.response_headers = {
                    name: _scrub(response.headers[name], key)
                    for name in RESPONSE_HEADERS if name in response.headers
                }
                safe_text = _scrub(response.text, key)
                try:
                    parsed: object = json.loads(response.text)
                    result.body_json = _safe_json(parsed, key)
                    # Preserve raw formatting unless decoded JSON contains a secret.
                    if result.body_json != parsed:
                        safe_text = json.dumps(result.body_json, ensure_ascii=False)
                except ValueError:
                    pass
                result.body_text = safe_text[:2000]
            except Exception as exc:
                # Never propagate a transport exception carrying credentials or a request.
                result.status = None
                result.error_kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "network"
                result.error_message = _scrub(str(exc), key)
            finally:
                result.duration_ms = round((time.perf_counter() - started) * 1000)

        execute(1, "GET /models, x-api-key", "/models", xkey)
        execute(2, "GET /models, Bearer", "/models", bearer)
        execute(3, "POST /messages, x-api-key + version", "/messages", versioned, _body(config.model))
        execute(4, "POST /messages, x-api-key without version", "/messages", xkey, _body(config.model))
        execute(5, "POST /v1/messages", "/v1/messages", versioned, _body(config.model),
                None if results[2].status in (404, 405) else "probe 3 did not return 404/405")
        execute(6, "POST /messages, Bearer", "/messages",
                {**bearer, "anthropic-version": config.api_version}, _body(config.model))
        execute(7, "POST /chat/completions, Bearer", "/chat/completions", bearer, _body(config.model, True))
        execute(8, "POST /messages, fallback model", "/messages", versioned,
                _body(config.model_fallback or config.model),
                None if config.model_fallback else "no fallback model supplied")
        winner = next((r for r in results if r.index >= 3 and _ok(r)), None)
        if winner is None:
            execute(9, "POST max_tokens=1", "/messages", versioned, _body(config.model),
                    "no generation endpoint returned 2xx")
        else:
            headers = bearer if "Authorization" in winner.headers_sent else xkey
            if "anthropic-version" in winner.headers_sent:
                headers = {**headers, "anthropic-version": config.api_version}
            path = "/chat/completions" if winner.index == 7 else "/v1/messages" if winner.index == 5 else "/messages"
            body = _body(config.model_fallback if winner.index == 8 and config.model_fallback else config.model,
                         winner.index == 7)
            body["max_tokens"] = 1
            execute(9, f"POST {path}, max_tokens=1 (probe {winner.index})", path, headers, body)
    return results


def _field(value: object, source: str) -> dict[str, object]:
    return {"value": value, "source": source}


def _positive(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _cost_source(result: ProbeResult) -> str:
    usage = _mapping(_mapping(result.body_json).get("usage"))
    complete = (
        _positive(usage.get("input_tokens")) and _positive(usage.get("output_tokens"))
    ) or (_positive(usage.get("prompt_tokens")) and _positive(usage.get("completion_tokens")))
    if not complete:
        return "none"
    return "provider" if "cost" in usage else "table"


def _empty_finding(result: ProbeResult | None) -> dict[str, object]:
    if not _ok(result) or result is None:
        return _field(TODO, "probe 9 skipped or failed")
    body = _mapping(result.body_json)
    reason = body.get("stop_reason")
    content = body.get("content")
    text: str | None = None
    if isinstance(content, list):
        text = "".join(str(_mapping(item).get("text", "")) for item in content)
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        choice = _mapping(choices[0])
        reason = choice.get("finish_reason")
        message = _mapping(choice.get("message"))
        if "content" in message:
            text = str(message["content"] or "")
    usage = _mapping(body.get("usage"))
    billed = any(_positive(usage.get(k)) for k in (
        "input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens", "cost"))
    return _field(TODO if text is None else text == "" and reason in ("max_tokens", "length") and billed,
                  f"probe 9: empty_text={text == ''}, stop_reason/finish_reason={reason}, "
                  f"tokens_or_cost_reported={billed}; reported usage is not an invoice")


def derive_profile_draft(results: Sequence[ProbeResult]) -> dict[str, object]:
    """Return evidence-bearing fields; unresolved prices/settings are never guessed."""
    probes = {r.index: r for r in results}
    listing: ProbeResult | None = None
    model_ids: list[str] = []
    for index in (1, 2):
        candidate = probes.get(index)
        data = _mapping(candidate.body_json).get("data") if candidate else None
        if _ok(candidate) and isinstance(data, list) and all(
            isinstance(_mapping(item).get("id"), str) for item in data
        ):
            listing = candidate
            model_ids = [str(_mapping(item)["id"]) for item in data[:50]]
            break
    profiles: dict[str, object] = {}
    for protocol, indices, suffix in (
        ("messages", (3, 4, 5, 6, 8), "/messages"),
        ("chat_completions", (7,), "/chat/completions"),
    ):
        winner = next((probes[i] for i in indices if _ok(probes.get(i))), None)
        if winner is None:
            continue
        source = f"probe {winner.index} returned {winner.status}"
        primary = probes.get(3) if winner.index == 8 else winner
        models: dict[str, object] = {
            "default": _field((primary.request_body or {}).get("model", TODO) if primary else TODO,
                              source if winner.index != 8 else "probe 3 request; only fallback probe 8 succeeded")
        }
        fallback = probes.get(8)
        if _ok(fallback) and fallback is not None:
            models["fallback"] = _field((fallback.request_body or {}).get("model", TODO),
                                        "probe 8 returned 2xx on messages; chat fallback not independently probed")
        cost = _cost_source(winner)
        profile: dict[str, object] = {
            "base_url": _field(winner.url.removesuffix(suffix), source),
            "protocol": _field(protocol, source),
            "auth_style": _field("bearer" if "Authorization" in winner.headers_sent else "x-api-key", source),
            "api_key_env": _field(winner.key_env, f"--key-env used by probe {winner.index}"),
            "models": models,
            "cost_source": _field(cost, f"{source}; complete positive in/out counts required before cost/table"),
        }
        if protocol == "messages":
            profile["max_tokens"] = _field(1024, f"draft default after {source}; actual limit unverified (request used 32)")
            p3, p4 = probes.get(3), probes.get(4)
            if _ok(p3) and p3 is not None and p4 is not None and not _ok(p4) and not p4.skipped:
                profile["api_version"] = _field(p3.headers_sent.get("anthropic-version", TODO),
                                                "probe 3 succeeded, probe 4 failed; inferred requirement")
        if listing is not None:
            profile["models_path"] = _field("/models", f"probe {listing.index} returned a model list")
        if cost == "table":
            profile["price_table"] = _field(TODO, f"probe {winner.index} supplies counts, not verified prices")
        profiles[f"{winner.profile_name}.{protocol}"] = profile
    return {"profiles": profiles,
            "model_ids": _field(model_ids if listing else TODO, f"probe {listing.index}" if listing else "probes 1/2: no model list"),
            "empty_response_at_max_tokens": _empty_finding(probes.get(9))}


def summary_table(results: Sequence[ProbeResult]) -> str:
    rows = ["| # | Label | Status | ms |", "|---|---|---|---|"]
    for r in results:
        status = "skipped" if r.skipped else str(r.status) if r.status is not None else str(r.error_kind)
        rows.append(f"| {r.index} | {r.label} | {status} | {r.duration_ms} |")
    return "\n".join(rows)


def render_report(results: Sequence[ProbeResult], draft: Mapping[str, object], meta: Mapping[str, object]) -> str:
    lines = ["# LLM proxy probe", "", f"- Timestamp: {meta.get('timestamp', TODO)}",
             f"- Base URL: {meta.get('base_url', TODO)}", f"- Model: {meta.get('model', TODO)}",
             "", summary_table(results)]
    for r in results:
        lines.extend(["", f"## Probe {r.index}: {r.label}", f"{r.method} {r.url}",
                      f"Status: {r.status}; duration: {r.duration_ms} ms",
                      f"Skipped: {r.skip_reason}" if r.skipped else "",
                      f"Error: {r.error_kind}: {r.error_message}" if r.error_kind else "",
                      "Headers sent:", "```json", json.dumps(r.headers_sent, indent=2), "```",
                      "Request body:", "```json", json.dumps(r.request_body, indent=2), "```",
                      "Response headers:", "```json", json.dumps(r.response_headers, indent=2), "```",
                      "Response body (at most 2000 characters):", "```", r.body_text or "(empty)", "```"])
    lines.extend(["", "## Draft profiles and findings", "", "```json", json.dumps(draft, indent=2, ensure_ascii=False), "```", ""])
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-fallback")
    parser.add_argument("--key-env", required=True)
    parser.add_argument("--api-version", default="2023-06-01")
    parser.add_argument("--timeout-sec", type=float, default=15)
    parser.add_argument("--out-dir", default="data/llm_probe")
    parser.add_argument("--profile-name", default=TODO)
    args = parser.parse_args(argv)
    # The CLI entry is the ONLY place in llm_core that reads os.environ,
    # and it reads only the single variable explicitly named by --key-env.
    key = os.environ.get(args.key_env, "")
    if not key.strip():
        print(f"Empty API key environment variable: {args.key_env}", file=sys.stderr)
        return 2
    try:
        config = ProbeConfig(args.base_url.rstrip("/"), args.model, key, args.key_env,
                             args.model_fallback, args.api_version, args.timeout_sec, args.profile_name)
        started = datetime.now(timezone.utc)
        results = run_probes(config, transport=transport)
        draft = derive_profile_draft(results)
        meta = {"timestamp": started.isoformat(), "base_url": config.base_url, "model": config.model}
        report = _scrub(render_report(results, draft, meta), key)
        directory = Path(args.out_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"probe-{started.strftime('%Y%m%d-%H%M%S')}.md"
        # Exclusive creation preserves reports from earlier runs in the same second.
        with path.open("x", encoding="utf-8") as output:
            output.write(report)
        print(_scrub(summary_table(results) + "\n" + json.dumps(draft, indent=2, ensure_ascii=False), key))
        return 0
    except Exception as exc:
        print(_scrub(f"Probe failed: {exc}", key), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
