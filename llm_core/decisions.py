"""Typed Decisions API client, independent of the host application."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Mapping, TypeAlias

import httpx
from tenacity import RetryError, retry, retry_if_exception, stop_after_attempt

from llm_core.client import CompletionUsage, LLMCoreError, LLMRetryExhaustedError, _is_retryable, _wait_for_retry
from llm_core.config import auth_headers
from llm_core.mask import mask_secret

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScoreQuestion:
    instructions: str
    levels: tuple[str, ...]


@dataclass(frozen=True)
class ChoiceQuestion:
    instructions: str
    options: Mapping[str, str]


@dataclass(frozen=True)
class NoulQuestion:
    instructions: str
    true_desc: str
    false_desc: str


Question: TypeAlias = ScoreQuestion | ChoiceQuestion | NoulQuestion


@dataclass(frozen=True)
class DecisionsRequest:
    model: str
    state: str
    questions: Mapping[str, Question]


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    probabilities: dict[str, float]
    confidence: float | None


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    probabilities: dict[str, float]
    confidence: float | None


@dataclass(frozen=True)
class NoulAnswer:
    probability: float


Answer: TypeAlias = ScoreAnswer | ChoiceAnswer | NoulAnswer


@dataclass(frozen=True)
class DecisionsResponse:
    model: str
    answers: dict[str, Answer]
    usage: CompletionUsage | None
    cost_usd: float | None
    raw_usage: dict[str, object] | None


class DecisionsResponseError(LLMCoreError):
    """The provider returned an invalid or incomplete Decisions response."""


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DecisionsResponseError("invalid numeric answer")
    return float(value)


def _probability(value: object) -> float:
    number = _number(value)
    if not 0 <= number <= 1:
        raise DecisionsResponseError("probability outside 0..1")
    return number


def _probabilities(value: object) -> dict[str, float]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise DecisionsResponseError("invalid probabilities")
    return {key: _probability(entry) for key, entry in value.items()}


def _confidence(value: object) -> float | None:
    return None if value is None else _probability(value)


def _question_payload(question: Question) -> dict[str, object]:
    if isinstance(question, ScoreQuestion):
        if not 2 <= len(question.levels) <= 10:
            raise ValueError("score questions require 2..10 levels")
        return {"type": "score", "instructions": question.instructions, "criteria": list(question.levels)}
    if isinstance(question, ChoiceQuestion):
        return {"type": "choice", "instructions": question.instructions, "criteria": dict(question.options)}
    return {"type": "noul", "instructions": question.instructions,
            "criteria": {"true": question.true_desc, "false": question.false_desc}}


def _parse(data: object, request: DecisionsRequest) -> DecisionsResponse:
    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
        raise DecisionsResponseError("missing answers")
    model = data.get("model")
    if not isinstance(model, str):
        raise DecisionsResponseError("missing model")
    raw_answers = data["answers"]
    answers: dict[str, Answer] = {}
    for name, question in request.questions.items():
        raw = raw_answers.get(name)
        if not isinstance(raw, dict):
            raise DecisionsResponseError(f"missing answer: {name}")
        if isinstance(question, ScoreQuestion) and raw.get("type") == "score":
            score = _number(raw.get("score"))
            if not 0 <= score <= len(question.levels) - 1:
                raise DecisionsResponseError(f"score outside levels: {name}")
            answers[name] = ScoreAnswer(score, _probabilities(raw.get("probabilities")),
                                        _confidence(raw.get("confidence")))
        elif isinstance(question, ChoiceQuestion) and raw.get("type") == "choice":
            choice = raw.get("choice")
            if not isinstance(choice, str) or choice not in question.options:
                raise DecisionsResponseError(f"invalid choice: {name}")
            answers[name] = ChoiceAnswer(choice, _probabilities(raw.get("probabilities")),
                                         _confidence(raw.get("confidence")))
        elif isinstance(question, NoulQuestion) and raw.get("type") == "noul":
            answers[name] = NoulAnswer(_probability(raw.get("noul")))
        else:
            raise DecisionsResponseError(f"wrong answer type: {name}")
    raw_usage = data.get("usage")
    if raw_usage is not None and not isinstance(raw_usage, dict):
        raise DecisionsResponseError("invalid usage")
    usage = None
    cost = None
    if isinstance(raw_usage, dict):
        tokens_in, tokens_out = raw_usage.get("input_tokens"), raw_usage.get("output_tokens")
        if type(tokens_in) is int and type(tokens_out) is int and tokens_in >= 0 and tokens_out >= 0:
            usage = CompletionUsage(tokens_in, tokens_out, tokens_in + tokens_out)
        raw_cost = raw_usage.get("cost")
        if isinstance(raw_cost, (int, float)) and not isinstance(raw_cost, bool) and math.isfinite(raw_cost):
            cost = float(raw_cost)
    return DecisionsResponse(model, answers, usage, cost, raw_usage)


class DecisionsClient:
    def __init__(self, base_url: str, path: str, api_key: str, timeout: float,
                 auth_style: str = "bearer") -> None:
        self.base_url = base_url
        self.path = path
        self.api_key = api_key
        self.timeout = timeout
        self.auth_style = auth_style

    @retry(stop=stop_after_attempt(4), wait=_wait_for_retry,
           retry=retry_if_exception(_is_retryable), reraise=False)
    async def _post(self, payload: dict[str, object]) -> object:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(f"{self.base_url.rstrip('/')}/{self.path.lstrip('/')}",
                                             headers={**auth_headers(self.auth_style, self.api_key),
                                                      "Content-Type": "application/json"}, json=payload)
                response.raise_for_status()
                try:
                    data: object = response.json()
                except ValueError as exc:
                    raise DecisionsResponseError("invalid Decisions JSON") from exc
                return data
            except httpx.HTTPStatusError as exc:
                logger.error("llm_core decisions: HTTP %s (key=%s)", exc.response.status_code,
                             mask_secret(self.api_key))
                raise
            except httpx.TimeoutException:
                logger.error("llm_core decisions: timed out after %ss", self.timeout)
                raise

    async def evaluate(self, request: DecisionsRequest) -> DecisionsResponse:
        payload: dict[str, object] = {"model": request.model, "state": request.state,
                                      "questions": {name: _question_payload(question)
                                                    for name, question in request.questions.items()}}
        try:
            data = await self._post(payload)
        except RetryError as exc:
            last = exc.last_attempt.exception()
            assert last is not None
            raise LLMRetryExhaustedError("Decisions request failed after all retries", last) from last
        return _parse(data, request)
