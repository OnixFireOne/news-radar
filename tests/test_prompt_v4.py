"""ТЗ #4 И4.2: the value prompt version is selectable and v3 stays the default."""

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from analyzer.prompts import (AI_VALUE_MESSAGE_PROMPT, AI_VALUE_MESSAGE_PROMPT_V4, AI_VALUE_PROMPT_VERSION,
                              AI_VALUE_PROMPTS)
from analyzer.value_classifier import ClassifyOutcome, LLMValueClassifier, ValueItem, ValueVerdict
from config.config_watcher import DEFAULT_CONFIG
from llm_core import ActiveProvider, LLMResponse, ProviderProfile, ProviderRouter
from tests.test_analyzer_ai_value import setup  # noqa: F401  (pytest fixture)


def router() -> ProviderRouter:
    active = ActiveProvider(ProviderProfile("test", "https://test.example/v1", "messages", "none",
                                            {"default": "m", "classify": "base"}, "none", max_tokens=100), "")
    return ProviderRouter([active], 5)


def response(gid: str) -> LLMResponse:
    item = {"id": gid, "temperature": 5, "content_type": "tutorial", "value_score": 7, "has_outcome": True,
            "takeaway": "Вывод", "topic": "agents", "summary": "Кратко", "keywords": [], "is_ad": False}
    return LLMResponse(json.dumps({"items": [item]}), "m", None, None, None, None, "table", "test", None, ())


def test_v4_shares_output_format_and_ad_rules_with_v3() -> None:
    tail = AI_VALUE_MESSAGE_PROMPT[AI_VALUE_MESSAGE_PROMPT.index("Return only a JSON object"):]
    assert AI_VALUE_MESSAGE_PROMPT_V4.endswith(tail)
    assert "always written in Russian" in AI_VALUE_MESSAGE_PROMPT_V4
    assert set(AI_VALUE_PROMPTS) == {"ai_value-v3", "ai_value-v4"}


def test_config_default_keeps_v3() -> None:
    with open("config/settings.json", encoding="utf-8") as fh:
        settings = json.load(fh)
    assert DEFAULT_CONFIG["ai_value_prompt_version"] == AI_VALUE_PROMPT_VERSION == "ai_value-v3"
    assert settings["ai_value_prompt_version"] == "ai_value-v3"


@pytest.mark.asyncio
@pytest.mark.parametrize("version, expected", [(None, AI_VALUE_MESSAGE_PROMPT),
                                               ("ai_value-v3", AI_VALUE_MESSAGE_PROMPT),
                                               ("ai_value-v4", AI_VALUE_MESSAGE_PROMPT_V4)])
async def test_classifier_sends_selected_prompt(version: str | None, expected: str) -> None:
    r = router()
    r.complete = AsyncMock(return_value=response("a"))  # type: ignore[method-assign]
    await LLMValueClassifier(r, prompt_version=version).classify([ValueItem("a", "body")])
    assert r.complete.call_args.args[1][0]["content"] == expected


def test_unknown_prompt_version_fails_fast() -> None:
    with pytest.raises(ValueError, match="unknown prompt version"):
        LLMValueClassifier(router(), prompt_version="ai_value-v9")


def _verdict() -> ValueVerdict:
    return ValueVerdict(5, "tutorial", 7, True, "Вывод", "agents", "Кратко", [], False)


@pytest.mark.asyncio
@pytest.mark.parametrize("version, passed", [(None, None), ("ai_value-v3", None), ("ai_value-v4", "ai_value-v4")])
async def test_pipeline_passes_version_only_when_not_default(setup: Any, version: str | None,  # noqa: F811
                                                             passed: str | None) -> None:
    if version is not None:
        setup.cfg["ai_value_prompt_version"] = version
    mid = setup.insert()
    setup.classifier.classify.return_value = [ClassifyOutcome(str(mid), _verdict(), None, "tool")]
    assert await setup.analyzer.analyze_pending() == 1
    assert setup.factory.call_args.kwargs.get("prompt_version") == passed
