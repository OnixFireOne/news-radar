"""Structured output is optional and preserves the legacy wire format."""

import json

import httpx
import pytest
import respx

from llm_core import JsonSchemaTool, LLMEmptyResponseError, LLMRequest, ProviderProfile, create_transport

TOOL = JsonSchemaTool("submit_verdicts", "Submit", {"type": "object"})
BASE = "https://structured.example/v1"


def profile(protocol):
    return ProviderProfile("structured", BASE, protocol, "none", {"default": "m"},
                           "none", max_tokens=200)


@pytest.mark.asyncio
async def test_messages_tool_payload_and_tool_only_reply():
    with respx.mock() as mock:
        route = mock.post(BASE + "/messages").mock(return_value=httpx.Response(200, json={
            "model": "m", "content": [{"type": "tool_use", "name": "submit_verdicts",
                                       "input": {"items": []}}], "stop_reason": "tool_use",
        }))
        transport = create_transport(profile("messages"), "", 5)
        result = await transport.complete(LLMRequest("m", [], tool=TOOL))
        body = json.loads(route.calls[0].request.content)
        assert body["tools"] == [{"name": TOOL.name, "description": TOOL.description,
                                  "input_schema": TOOL.schema}]
        assert body["tool_choice"] == {"type": "tool", "name": TOOL.name}
        assert result.structured == {"items": []}
        assert result.tool_calls_seen == ("submit_verdicts",)
        await transport.complete(LLMRequest("m", [], tool=TOOL))
        with pytest.raises(LLMEmptyResponseError):
            await transport.complete(LLMRequest("m", []))
        assert "tools" not in json.loads(route.calls[2].request.content)


@pytest.mark.asyncio
async def test_chat_function_payload_and_parsing():
    with respx.mock() as mock:
        route = mock.post(BASE + "/chat/completions").mock(return_value=httpx.Response(200, json={
            "choices": [{"message": {"content": None, "tool_calls": [
                {"function": {"name": "WebSearch", "arguments": "{}"}},
                {"function": {"name": "submit_verdicts", "arguments": '{"items":[]}'}},
            ]}}], "model": "m",
        }))
        transport = create_transport(profile("chat_completions"), "", 5)
        result = await transport.complete(LLMRequest("m", [], tool=TOOL))
        body = json.loads(route.calls[0].request.content)
        assert body["tools"] == [{"type": "function", "function": {
            "name": TOOL.name, "description": TOOL.description, "parameters": TOOL.schema}}]
        assert body["tool_choice"] == {"type": "function", "function": {"name": TOOL.name}}
        assert result.structured == {"items": []}
        assert result.tool_calls_seen == ("WebSearch", "submit_verdicts")
        await transport.complete(LLMRequest("m", []))
        assert "tools" not in json.loads(route.calls[1].request.content)
