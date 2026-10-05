"""Decisions wire contract and response validation."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx

from llm_core.decisions import (ChoiceAnswer, ChoiceQuestion, DecisionsClient, DecisionsRequest,
                                DecisionsResponseError, NoulAnswer, NoulQuestion, ScoreAnswer, ScoreQuestion)


def request() -> DecisionsRequest:
    return DecisionsRequest("jev", "article", {
        "value": ScoreQuestion("Score", ("low", "high")),
        "kind": ChoiceQuestion("Choose", {"tutorial": "How to", "hype": "News"}),
        "is_ad": NoulQuestion("Promotion?", "Yes", "No"),
    })


def response() -> dict[str, object]:
    return {"model": "jev-1", "answers": {
        "value": {"type": "score", "score": 0.7, "probabilities": {"0": 0.3, "1": 0.7},
                  "confidence": 0.7},
        "kind": {"type": "choice", "choice": "tutorial", "probabilities": {"tutorial": 0.9},
                 "confidence": 0.9},
        "is_ad": {"type": "noul", "noul": 0.4},
    }, "usage": {"input_tokens": 12, "output_tokens": 3, "cost": 0.00002}}


@pytest.mark.asyncio
async def test_payload_and_parsing() -> None:
    with respx.mock() as mock:
        route = mock.post("https://example.test/alpha/decisions").mock(
            return_value=httpx.Response(200, json=response()))
        result = await DecisionsClient("https://example.test", "/alpha/decisions", "secret", 5).evaluate(request())
    sent = json.loads(route.calls[0].request.content)
    assert sent["questions"]["value"]["criteria"] == ["low", "high"]
    assert sent["questions"]["kind"]["criteria"] == {"tutorial": "How to", "hype": "News"}
    assert sent["questions"]["is_ad"]["criteria"] == {"true": "Yes", "false": "No"}
    assert isinstance(result.answers["value"], ScoreAnswer)
    assert isinstance(result.answers["kind"], ChoiceAnswer)
    assert isinstance(result.answers["is_ad"], NoulAnswer)
    assert result.usage is not None and result.usage.prompt_tokens == 12
    assert result.cost_usd == 0.00002


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "mistyped"])
async def test_invalid_answer(change: str) -> None:
    body = response()
    answers = body["answers"]
    assert isinstance(answers, dict)
    if change == "missing":
        del answers["kind"]
    else:
        answers["kind"] = {"type": "score", "score": 1}
    with respx.mock() as mock:
        mock.post("https://example.test/alpha/decisions").mock(return_value=httpx.Response(200, json=body))
        with pytest.raises(DecisionsResponseError):
            await DecisionsClient("https://example.test", "/alpha/decisions", "secret", 5).evaluate(request())


@pytest.mark.asyncio
async def test_missing_cost_and_masked_http_error(caplog: pytest.LogCaptureFixture) -> None:
    body = response()
    usage = body["usage"]
    assert isinstance(usage, dict)
    del usage["cost"]
    with respx.mock() as mock:
        route = mock.post("https://example.test/alpha/decisions")
        route.mock(side_effect=[httpx.Response(401, text="secret"), httpx.Response(200, json=body)])
        client = DecisionsClient("https://example.test", "/alpha/decisions", "secret", 5)
        with caplog.at_level(logging.ERROR):
            with pytest.raises(httpx.HTTPStatusError):
                await client.evaluate(request())
        assert "secret" not in caplog.text
        result = await client.evaluate(request())
        assert result.cost_usd is None
