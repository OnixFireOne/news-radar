"""Per-model request quirks from the catalog reach the wire, and only for that model."""

import json

import httpx
import pytest
import respx

from llm_core import CatalogError, LLMRequest, ProviderProfile, create_transport
from llm_core.catalog import _validate

BASE = "https://params.example/v1"
PARAMS = {"quirky": {"temperature": None, "reasoning_effort": "none"}}


def profile(protocol):
    return ProviderProfile("params", BASE, protocol, "none", {"default": "quirky"}, "none",
                           max_tokens=100, model_params=PARAMS)


def raw(model_params):
    return {"name": "p", "base_url": BASE, "protocol": "chat_completions", "auth_style": "none",
            "models": {"default": "m"}, "cost_source": "none", "model_params": model_params}


def test_catalog_accepts_scalars_and_null_rejects_nested():
    assert _validate("p", raw(PARAMS)).request_params("quirky") == PARAMS["quirky"]
    assert _validate("p", raw({})).request_params("quirky") == {}
    with pytest.raises(CatalogError, match="model_params.m.x"):
        _validate("p", raw({"m": {"x": [1]}}))


@pytest.mark.asyncio
async def test_chat_completions_drops_and_adds_params_for_that_model_only():
    ok = {"choices": [{"message": {"content": "ok"}}], "model": "m"}
    with respx.mock() as mock:
        route = mock.post(BASE + "/chat/completions").mock(return_value=httpx.Response(200, json=ok))
        transport = create_transport(profile("chat_completions"), "", 5)
        await transport.complete(LLMRequest("quirky", [{"role": "user", "content": "hi"}]))
        await transport.complete(LLMRequest("plain", [{"role": "user", "content": "hi"}]))
        quirky, plain = (json.loads(call.request.content) for call in route.calls)
    assert "temperature" not in quirky and quirky["reasoning_effort"] == "none"
    assert plain["temperature"] == 0.3 and "reasoning_effort" not in plain


@pytest.mark.asyncio
async def test_messages_drops_and_adds_params_for_that_model_only():
    ok = {"content": [{"type": "text", "text": "ok"}], "model": "m"}
    with respx.mock() as mock:
        route = mock.post(BASE + "/messages").mock(return_value=httpx.Response(200, json=ok))
        transport = create_transport(profile("messages"), "", 5)
        await transport.complete(LLMRequest("quirky", [{"role": "user", "content": "hi"}]))
        await transport.complete(LLMRequest("plain", [{"role": "user", "content": "hi"}]))
        quirky, plain = (json.loads(call.request.content) for call in route.calls)
    assert "temperature" not in quirky and quirky["reasoning_effort"] == "none"
    assert plain["temperature"] == 0.3 and "reasoning_effort" not in plain
