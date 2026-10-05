"""Strict JSON routing and schema contracts."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from analyzer.json_schemas import (DIGEST_AI_VALUE_SCHEMA, KNOWLEDGE_BRIEF_SCHEMA,
                                   KNOWLEDGE_CHUNK_SCHEMA, KNOWLEDGE_FULL_SCHEMA)
from analyzer.knowledge_publisher import generate_doc
from analyzer.llm_client import LLMClient
from analyzer.pipeline.writers import _ai_value_compose, _spoiler_compose
from analyzer.value_classifier import _tool
from config.config_watcher import DEFAULT_CONFIG
from llm_core.catalog import ProviderProfile, load_catalog
from llm_core.client_messages import MessagesTransport
from llm_core.transport import (ChatCompletionsTransport, JsonSchemaTool, LLMRequest,
                                LLMResponse, strict_schema)


def test_schema_copy_closes_nested_objects() -> None:
    original = DIGEST_AI_VALUE_SCHEMA.schema
    result = strict_schema(original)
    assert result["additionalProperties"] is False
    assert result["required"] == ["items"]
    item = cast(dict[str, Any], cast(dict[str, Any], result["properties"])["items"])["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"source_id", "title", "takeaway", "summary"}
    assert "additionalProperties" not in original


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_strict,profile_strict,expected", [
    (False, False, False), (True, False, False), (False, True, False), (True, True, True),
])
async def test_chat_strict_needs_both_flags(tool_strict: bool, profile_strict: bool,
                                            expected: bool) -> None:
    profile = ProviderProfile("test", "https://example.org/v1", "chat_completions", "none",
                              {"default": "model"}, "none", strict_tools=profile_strict)
    transport = ChatCompletionsTransport(profile, "", 1)
    payloads: list[dict[str, object]] = []

    async def post(payload: dict[str, object]) -> dict[str, Any]:
        payloads.append(payload)
        return {"choices": [{"message": {"content": "ok"}}], "model": "model"}

    transport._client._post_chat_completion = post  # type: ignore[method-assign,assignment]
    await transport.complete(LLMRequest("model", [{"role": "user", "content": "x"}],
                                        tool=replace(KNOWLEDGE_BRIEF_SCHEMA, strict=tool_strict)))
    function = cast(dict[str, Any], cast(list[dict[str, Any]], payloads[0]["tools"])[0]["function"])
    assert function.get("strict", False) is expected
    assert ("additionalProperties" in function["parameters"]) is expected


@pytest.mark.asyncio
async def test_messages_ignores_strict() -> None:
    profile = ProviderProfile("test", "https://example.org/v1", "messages", "none",
                              {"default": "model"}, "none", max_tokens=100)
    transport = MessagesTransport(profile, "", 1)
    payloads: list[dict[str, object]] = []

    async def post(payload: dict[str, object]) -> dict[str, Any]:
        payloads.append(payload)
        return {"content": [{"type": "tool_use", "name": "submit_knowledge_brief", "input": {}}]}

    transport._post = post  # type: ignore[method-assign,assignment]
    for strict in (False, True):
        await transport.complete(LLMRequest("model", [{"role": "user", "content": "x"}],
                                            tool=replace(KNOWLEDGE_BRIEF_SCHEMA, strict=strict)))
    assert payloads[0] == payloads[1]


@pytest.mark.asyncio
async def test_client_tool_and_text_paths() -> None:
    router = Mock()
    router.complete = AsyncMock()
    router.primary.return_value.profile.base_url = "https://example.org/v1"
    router.primary.return_value.profile.chat_template_kwargs = False
    router.primary.return_value.api_key = ""
    router.model_for.return_value = "model"
    client = LLMClient(router=router)
    client.strict_json_tasks = frozenset({"knowledge"})
    router.complete.return_value = LLMResponse("", "model", None, None, None, None,
                                                "none", "test", {"title": "ok"})
    assert await client.complete_json("x", task="knowledge", schema=KNOWLEDGE_BRIEF_SCHEMA) == {"title": "ok"}
    assert router.complete.await_args.kwargs["tool"].strict
    router.complete.return_value = replace(router.complete.return_value,
                                           structured=None, content='{"title": "text"}')
    assert await client.complete_json("x", task="knowledge", schema=KNOWLEDGE_BRIEF_SCHEMA) == {"title": "text"}
    assert await client.complete_json("x", task="digest", schema=KNOWLEDGE_BRIEF_SCHEMA) == {"title": "text"}
    assert "tool" not in router.complete.await_args.kwargs
    assert await client.complete_json("x", task="knowledge") == {"title": "text"}
    assert "tool" not in router.complete.await_args.kwargs


@pytest.mark.asyncio
async def test_legacy_json_ignores_schema() -> None:
    client = LLMClient()
    client.strict_json_tasks = frozenset({"knowledge"})
    client.complete = AsyncMock(return_value='{"title": "legacy"}')  # type: ignore[method-assign]
    assert await client.complete_json("x", task="knowledge", schema=KNOWLEDGE_BRIEF_SCHEMA) == {
        "title": "legacy"}


def test_config_and_classifier_schema() -> None:
    assert DEFAULT_CONFIG["llm_strict_json_tasks"] == []
    settings = json.loads(Path("config/settings.json").read_text())
    assert settings["llm_strict_json_tasks"] == ["knowledge", "digest", "classify"]
    catalog = load_catalog("config/providers.json")
    assert catalog["openai.chat_completions"].strict_tools
    assert all(not profile.strict_tools for name, profile in catalog.items()
               if name != "openai.chat_completions")
    strict = _tool(True)
    ordinary = _tool()
    strict_item = cast(dict[str, Any], cast(dict[str, Any], strict.schema["properties"])["items"])["items"]
    ordinary_item = cast(dict[str, Any], cast(dict[str, Any], ordinary.schema["properties"])["items"])["items"]
    assert strict.strict and "minimum" not in strict_item["properties"]["value_score"]
    assert ordinary_item["properties"]["value_score"]["minimum"] == 1


@pytest.mark.asyncio
async def test_knowledge_schemas() -> None:
    llm = AsyncMock()
    llm.complete_json.return_value = {"title": "Title", "idea": "Idea", "conclusion": "End",
                                      "tags": ["ai", "tools"]}
    row = {"id": 1, "text": "text", "collected_at": "2026-10-01"}
    await generate_doc(llm, row, {"format": "brief"})
    assert llm.complete_json.await_args.kwargs["schema"] is KNOWLEDGE_BRIEF_SCHEMA
    full_answer = {"title": "Title", "tldr": "Summary", "context": "",
        "key_points": ["one", "two", "three"], "how": "", "results": "", "limitations": "",
        "takeaways": ["lesson"], "read_original_if": "", "tags": ["ai", "tools"]}

    async def complete_json(**kwargs: Any) -> dict[str, Any]:
        return {"notes": ["a note"]} if kwargs["schema"] is KNOWLEDGE_CHUNK_SCHEMA else full_answer

    llm.complete_json.side_effect = complete_json
    await generate_doc(llm, {**row, "text": "x" * 3000},
                       {"format": "full", "split_over_chars": 1000})
    assert llm.complete_json.await_args.kwargs["schema"] is KNOWLEDGE_FULL_SCHEMA
    assert any(call.kwargs["schema"] is KNOWLEDGE_CHUNK_SCHEMA for call in llm.complete_json.await_args_list)


@pytest.mark.asyncio
async def test_digest_schema_only_for_ai_value() -> None:
    llm = AsyncMock()
    ctx = AsyncMock()
    ctx.analyzer.llm = llm
    ctx.template_cfg = {}
    ctx.artifacts = {"period": "today", "messages_text": "text"}
    ctx.digest_max = 5
    await _ai_value_compose([], ctx)
    assert llm.complete_json.await_args.kwargs["schema"] is DIGEST_AI_VALUE_SCHEMA
    await _spoiler_compose([], ctx)
    assert "schema" not in llm.complete_json.await_args.kwargs
