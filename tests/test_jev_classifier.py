"""Jev classification preserves per-item outcomes and typed scores."""

from __future__ import annotations

import asyncio

import pytest

from analyzer.jev_classifier import JevValueClassifier
from analyzer.value_classifier import ValueItem
from llm_core.decisions import (ChoiceAnswer, DecisionsClient, DecisionsRequest, DecisionsResponse,
                                NoulAnswer, ScoreAnswer)


def response(score: float = 2.68, *, kind: str = "tutorial", yes: float = 0.5) -> DecisionsResponse:
    return DecisionsResponse("jev-1", {
        "value_score": ScoreAnswer(score, {}, 0.85),
        "content_type": ChoiceAnswer(kind, {}, 0.9),
        "topic": ChoiceAnswer("agents", {}, 0.8),
        "has_outcome": NoulAnswer(yes),
        "is_ad": NoulAnswer(yes),
    }, None, 0.00002, {"cost": 0.00002})


class FakeClient(DecisionsClient):
    def __init__(self, values: dict[str, DecisionsResponse | Exception]) -> None:
        super().__init__("https://example.test", "/alpha/decisions", "key", 5)
        self.values = values
        self.active = 0
        self.peak = 0

    async def evaluate(self, request: DecisionsRequest) -> DecisionsResponse:
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01 if "first" in request.state else 0)
        self.active -= 1
        value = self.values[request.state.split("<<<ARTICLE DATA>>>\n", 1)[1].split("\n<<<END", 1)[0]]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.asyncio
@pytest.mark.parametrize(("score", "expected"), [(2.68, 4), (9.0, 10), (0.0, 1)])
async def test_score_index_and_noul_threshold(score: float, expected: int) -> None:
    classifier = JevValueClassifier(FakeClient({"body": response(score, yes=0.5)}))
    outcome = (await classifier.classify([ValueItem("a", "body")]))[0]
    assert outcome.verdict is not None
    assert outcome.verdict.value_score == expected
    assert outcome.verdict.has_outcome and outcome.verdict.is_ad
    assert outcome.verdict.temperature == 5 and outcome.verdict.takeaway == ""
    assert classifier.confidences["a"]["value_score"] == 0.85


@pytest.mark.asyncio
async def test_order_concurrency_and_isolated_errors() -> None:
    client = FakeClient({"first": response(), "bad": RuntimeError("failed"),
                         "last": response(kind="unknown")})
    classifier = JevValueClassifier(client, concurrency=2)
    results = await classifier.classify([ValueItem("a", "first"), ValueItem("b", "bad"),
                                         ValueItem("c", "last")])
    assert [item.item_id for item in results] == ["a", "b", "c"]
    assert results[0].verdict is not None
    assert results[1].verdict is None and results[1].error
    assert results[2].verdict is None and results[2].error
    assert client.peak == 2
    assert len(classifier.calls) == 3
    assert all(call.path == "decisions" for call in classifier.calls)
