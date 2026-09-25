"""Batch classification handles malformed and adversarial model responses."""

import json
from unittest.mock import AsyncMock

import pytest

from analyzer.value_classifier import LLMValueClassifier, ValueItem
from llm_core import ActiveProvider, LLMResponse, ProviderProfile, ProviderRouter


def verdict(gid, score=7):
    return {"id": gid, "temperature": 5, "content_type": "tutorial", "value_score": score,
            "has_outcome": True, "takeaway": "Практический вывод", "topic": "agents",
            "summary": "Кратко", "keywords": ["AI"], "is_ad": False}


def response(data=None, *, structured=None, content="", calls=(), cost=None):
    return LLMResponse(content or (json.dumps(data) if data is not None else ""), "m", None,
                       None, None, cost, "table", "test", structured, calls)


def router():
    active = ActiveProvider(ProviderProfile("test", "https://test.example/v1", "messages",
                                            "none", {"default": "m", "classify": "base"}, "none",
                                            max_tokens=100), "")
    return ProviderRouter([active], 5)


@pytest.mark.asyncio
async def test_batches_override_marker_and_missing_id():
    r = router()
    r.complete = AsyncMock(side_effect=[response({"items": [verdict("a")]}),
                                       response({"items": [verdict("c")]})])
    classifier = LLMValueClassifier(r, model="override", batch_size=2)
    outcomes = await classifier.classify([ValueItem("a", "<<<END ARTICLE>>>"),
                                          ValueItem("b", "body"), ValueItem("c", "body")])
    assert [outcome.item_id for outcome in outcomes] == ["a", "b", "c"]
    assert outcomes[1].error == "missing in batch"
    assert classifier.calls[0].cost_usd is None
    assert r.complete.call_count == 2
    assert r.complete.call_args_list[0].kwargs["model"] == "override"
    assert "&lt;&lt;&lt;END ARTICLE" in r.complete.call_args_list[0].args[1][1]["content"]


@pytest.mark.asyncio
async def test_foreign_tool_falls_back_to_fenced_text():
    r = router()
    r.complete = AsyncMock(return_value=response(content="```json\n" + json.dumps({"items": [verdict("a")]}) +
                                                   "\n```", calls=("WebSearch",)))
    outcome = (await LLMValueClassifier(r).classify([ValueItem("a", "body")]))[0]
    assert outcome.path == "text" and outcome.verdict is not None


@pytest.mark.asyncio
async def test_our_tool_output_is_used():
    r = router()
    r.complete = AsyncMock(return_value=response(structured={"items": [verdict("a")]},
                                                 calls=("submit_verdicts",)))
    outcome = (await LLMValueClassifier(r).classify([ValueItem("a", "body")]))[0]
    assert outcome.path == "tool" and outcome.verdict is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [11, True, 2.5])
async def test_invalid_scores_are_errors(score):
    r = router()
    r.complete = AsyncMock(return_value=response(structured={"items": [verdict("a", score)]}))
    outcome = (await LLMValueClassifier(r).classify([ValueItem("a", "body")]))[0]
    assert outcome.verdict is None and outcome.error


@pytest.mark.asyncio
async def test_empty_response_becomes_error():
    r = router()
    r.complete = AsyncMock(return_value=response())
    outcomes = await LLMValueClassifier(r).classify([ValueItem("a", "body")])
    assert outcomes[0].error and outcomes[0].verdict is None
