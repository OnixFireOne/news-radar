"""Models listed in no_tool_models skip the strict JSON tool path."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from analyzer.json_schemas import DIGEST_AI_VALUE_SCHEMA
from analyzer.llm_client import LLMClient
from llm_core import CatalogError
from llm_core.catalog import _validate, load_catalog
from llm_core.transport import LLMResponse


def raw(no_tool_models: object) -> dict[str, Any]:
    return {"name": "p", "base_url": "https://example.org/v1", "protocol": "chat_completions",
            "auth_style": "none", "models": {"default": "m"}, "cost_source": "none",
            "no_tool_models": no_tool_models}


def test_catalog_parses_no_tool_models() -> None:
    profile = _validate("p", raw(["text-only"]))
    assert not profile.supports_tools("text-only")
    assert profile.supports_tools("m")
    assert _validate("p", {k: v for k, v in raw([]).items() if k != "no_tool_models"}).supports_tools("m")
    with pytest.raises(CatalogError, match="no_tool_models"):
        _validate("p", raw("text-only"))


@pytest.mark.asyncio
@pytest.mark.parametrize("model,tool_expected", [("tools-ok", True), ("text-only", False)])
async def test_complete_json_uses_text_path_for_no_tool_model(model: str, tool_expected: bool) -> None:
    profile = _validate("p", raw(["text-only"]))
    router = Mock()
    router.primary.return_value.profile = profile
    router.primary.return_value.api_key = ""
    router.model_for.return_value = model
    router.complete = AsyncMock(return_value=LLMResponse(
        '{"items": []}', model, None, None, None, None, "none", "p",
        {"items": []} if tool_expected else None))
    client = LLMClient(router=router)
    client.strict_json_tasks = frozenset({"digest"})
    assert await client.complete_json("x", task="digest", schema=DIGEST_AI_VALUE_SCHEMA) == {"items": []}
    assert ("tool" in router.complete.await_args.kwargs) is tool_expected


def test_openai_digest_route_is_text_only_model() -> None:
    profile = load_catalog("config/providers.json")["openai.chat_completions"]
    assert profile.models["digest"] == "gpt-6.1-sol"
    assert not profile.supports_tools("gpt-6.1-sol")
    assert profile.supports_tools(profile.models["classify"])
