"""Wire contracts for both transports, independent of the host facade."""

import json
from dataclasses import replace

import httpx
import pytest
import respx

from llm_core import (
    CompletionUsage, LLMEmptyResponseError, LLMRequest, LLMRetryExhaustedError,
    ModelPrice, ProviderProfile, create_transport,
)

BASE = "https://proxy.example.com/v1"


def profile(protocol="messages", auth="x-api-key"):
    return ProviderProfile(
        "proxy", BASE, protocol, auth, {"default": "m"}, "table",
        api_version="2023-06-01", max_tokens=4096,
        price_table={"m": ModelPrice(3, 15)}, input_overhead=1317,
        extra_headers={"X-Test": "yes"},
    )


def response(**overrides):
    data = {"model": "m", "content": [{"type": "text", "text": "hello"},
            {"type": "thinking", "text": "hidden"}, {"type": "text", "text": " world"}],
            "usage": {"input_tokens": 2000, "output_tokens": 100}, "stop_reason": "end_turn"}
    data.update(overrides)
    return httpx.Response(200, json=data)


@pytest.mark.asyncio
@pytest.mark.parametrize("auth", ["bearer", "x-api-key", "none"])
async def test_messages_payload_auth_usage_cost(auth):
    with respx.mock() as mock:
        route = mock.post(BASE + "/messages").mock(return_value=response())
        result = await create_transport(profile(auth=auth), "secret", 5).complete(LLMRequest(
            "m", [{"role": "system", "content": "first"}, {"role": "user", "content": "hi"},
                  {"role": "system", "content": "second"}, {"role": "assistant", "content": "ok"}],
            max_tokens=200, extra_payload={"chat_template_kwargs": {"enable_thinking": False},
                                           "temperature": 0.7},
        ))
        request = route.calls[0].request
        assert json.loads(request.content) == {
            "model": "m", "max_tokens": 200, "system": "first\n\nsecond", "temperature": 0.7,
            "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}],
        }
        assert request.headers.get("authorization") == ("Bearer secret" if auth == "bearer" else None)
        assert request.headers.get("x-api-key") == ("secret" if auth == "x-api-key" else None)
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.headers["x-test"] == "yes"
        assert result.content == "hello world"
        assert result.usage == CompletionUsage(2000, 100, 2100)
        assert result.cost_usd == pytest.approx(0.0075)
        assert result.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_retry_after_then_success():
    with respx.mock() as mock:
        route = mock.post(BASE + "/messages")
        route.side_effect = [httpx.Response(429, headers={"Retry-After": "0"}), response()]
        await create_transport(profile(), "secret", 5).complete(LLMRequest("m", []))
        assert route.call_count == 2
        body = json.loads(route.calls[1].request.content)
        assert "system" not in body
        assert body["max_tokens"] == 4096


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["max_tokens", "end_turn", None])
async def test_empty_text_retains_billed_usage(reason):
    with respx.mock() as mock:
        route = mock.post(BASE + "/messages").mock(return_value=response(content=[], stop_reason=reason))
        with pytest.raises(LLMEmptyResponseError) as error:
            await create_transport(profile(), "secret", 5).complete(LLMRequest("m", []))
        assert error.value.stop_reason == reason
        assert error.value.usage == CompletionUsage(2000, 100, 2100)
        assert error.value.cost_usd == pytest.approx(0.0075)
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_retry_exhaustion_and_masked_logging(caplog):
    secret = "sk-secret-never-printed"
    with respx.mock() as mock:
        route = mock.post(BASE + "/messages").mock(return_value=httpx.Response(
            503, headers={"Retry-After": "0"}, text=secret))
        with pytest.raises(LLMRetryExhaustedError):
            await create_transport(profile(), secret, 5).complete(LLMRequest("m", []))
        assert route.call_count == 4
    assert secret not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("auth", ["bearer", "x-api-key", "none"])
async def test_chat_preserves_legacy_payload_and_empty_response(auth):
    with respx.mock() as mock:
        route = mock.post(BASE + "/chat/completions").mock(return_value=httpx.Response(200, json={
            "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.04},
        }))
        result = await create_transport(replace(profile("chat_completions", auth), cost_source="provider"),
                                        "secret", 5).complete(LLMRequest(
            "", [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}],
            max_tokens=12, extra_payload={"chat_template_kwargs": {"enable_thinking": False}},
        ))
        request = route.calls[0].request
        assert json.loads(request.content) == {
            "messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}],
            "temperature": 0.3, "max_tokens": 12, "chat_template_kwargs": {"enable_thinking": False},
        }
        assert request.headers.get("authorization") == ("Bearer secret" if auth == "bearer" else None)
        assert request.headers.get("x-api-key") == ("secret" if auth == "x-api-key" else None)
        assert request.headers["content-type"] == "application/json"
        assert request.headers["x-test"] == "yes"
        assert result.content == ""
        assert result.stop_reason == "stop"
        assert result.cost_usd == 0.04


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,usage", [
    ("messages", {"output_tokens": 100}), ("chat_completions", {"completion_tokens": 100}),
])
async def test_partial_usage_is_not_priced(protocol, usage):
    with respx.mock() as mock:
        path = "/messages" if protocol == "messages" else "/chat/completions"
        mock.post(BASE + path).mock(return_value=httpx.Response(200, json={
            "content": [{"type": "text", "text": "ok"}],
            "choices": [{"message": {"content": "ok"}}], "usage": usage,
        }))
        result = await create_transport(profile(protocol), "secret", 5).complete(LLMRequest("m", []))
        assert result.usage is None
        assert result.cost_usd is None


@pytest.mark.asyncio
async def test_messages_non_retryable_status_fails_immediately():
    with respx.mock() as mock:
        route = mock.post(BASE + "/messages").mock(return_value=httpx.Response(400))
        with pytest.raises(httpx.HTTPStatusError):
            await create_transport(profile(), "secret", 5).complete(LLMRequest("m", []))
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_chat_bearer_request_is_identical_to_legacy_client():
    from llm_core import LLMCoreClient, LLMCoreConfig

    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}]
    extra = {"temperature": 0.7, "chat_template_kwargs": {"enable_thinking": False}}
    with respx.mock() as mock:
        route = mock.post(BASE + "/chat/completions").mock(return_value=httpx.Response(200, json={}))
        await LLMCoreClient(LLMCoreConfig(BASE, "secret", 5, {"X-Test": "yes"})).chat_completion(
            messages, "m", max_tokens=42, extra_payload=extra,
        )
        await create_transport(profile("chat_completions", "bearer"), "secret", 5).complete(
            LLMRequest("m", messages, max_tokens=42, extra_payload=extra),
        )
        legacy, wrapped = [call.request for call in route.calls]
        assert wrapped.content == legacy.content
        assert wrapped.headers == legacy.headers
