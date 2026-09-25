"""Batch AI value classification behind an implementation-independent port."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from analyzer.prompts import AI_VALUE_MESSAGE_PROMPT
from llm_core.client import ChatMessage
from llm_core.client_messages import LLMEmptyResponseError
from llm_core.router import ProviderRouter
from llm_core.transport import JsonSchemaTool, LLMResponse

ContentType = Literal["practical_case", "tutorial", "tool_release", "research", "opinion", "hype_news", "crypto"]
Topic = Literal["agents", "llm_ops", "integrations", "models", "infra", "crypto", "other"]
Path = Literal["tool", "text", "none"]
CONTENT_TYPES = ("practical_case", "tutorial", "tool_release", "research", "opinion", "hype_news", "crypto")
TOPICS = ("agents", "llm_ops", "integrations", "models", "infra", "crypto", "other")


@dataclass(frozen=True)
class ValueItem:
    id: str
    text: str
    source: str = ""


@dataclass(frozen=True)
class ValueVerdict:
    temperature: int
    content_type: ContentType
    value_score: int
    has_outcome: bool
    takeaway: str
    topic: Topic
    summary: str
    keywords: list[str]
    is_ad: bool


@dataclass(frozen=True)
class ClassifyOutcome:
    item_id: str
    verdict: ValueVerdict | None
    error: str | None
    path: Path


class ValueClassifier(Protocol):
    async def classify(self, items: Sequence[ValueItem]) -> list[ClassifyOutcome]: ...


@dataclass(frozen=True)
class CallStats:
    latency_seconds: float
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float | None
    cost_source: str
    provider: str
    model: str
    path: Path
    error: str | None


def _tool() -> JsonSchemaTool:
    item: dict[str, object] = {
        "type": "object", "required": ["id", "temperature", "content_type", "value_score",
                                        "has_outcome", "takeaway", "topic", "summary", "keywords", "is_ad"],
        "properties": {
            "id": {"type": "string"},
            "temperature": {"type": "integer", "minimum": 1, "maximum": 10},
            "content_type": {"type": "string", "enum": list(CONTENT_TYPES)},
            "value_score": {"type": "integer", "minimum": 1, "maximum": 10},
            "has_outcome": {"type": "boolean"},
            "takeaway": {"type": "string"},
            "topic": {"type": "string", "enum": list(TOPICS)},
            "summary": {"type": "string"},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "is_ad": {"type": "boolean"},
        },
    }
    return JsonSchemaTool("submit_verdicts", "Submit one verdict per article", {
        "type": "object", "required": ["items"], "properties": {
            "items": {"type": "array", "items": item},
        },
    })


def _score(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("score must be an integer from 1 to 10")
    if not 1 <= value <= 10 or int(value) != value:
        raise ValueError("score must be an integer from 1 to 10")
    return int(value)


def _verdict(data: dict[str, object]) -> ValueVerdict:
    content_type = data.get("content_type")
    topic = data.get("topic")
    if content_type not in CONTENT_TYPES or topic not in TOPICS:
        raise ValueError(f"invalid content_type={content_type!r} or topic={topic!r}")
    if type(data.get("has_outcome")) is not bool or type(data.get("is_ad")) is not bool:
        raise ValueError("invalid boolean field")
    for key in ("takeaway", "summary"):
        if not isinstance(data.get(key), str):
            raise ValueError(f"invalid {key}")
    keywords = data.get("keywords")
    if not isinstance(keywords, list) or not all(isinstance(word, str) for word in keywords):
        raise ValueError("invalid keywords")
    assert isinstance(content_type, str) and isinstance(topic, str)
    assert isinstance(data["has_outcome"], bool) and isinstance(data["is_ad"], bool)
    assert isinstance(data["takeaway"], str) and isinstance(data["summary"], str)
    return ValueVerdict(_score(data.get("temperature")), cast(ContentType, content_type),
                        _score(data.get("value_score")), data["has_outcome"],
                        data["takeaway"], cast(Topic, topic), data["summary"],
                        cast(list[str], keywords), data["is_ad"])


def _text_json(content: str) -> dict[str, object]:
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in response")
    parsed: object = json.loads(content[start:end + 1])
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ValueError("response is not a JSON object")
    return parsed


def _escape(text: str) -> str:
    return text.replace("<<<ARTICLE", "&lt;&lt;&lt;ARTICLE").replace("<<<END ARTICLE>>>",
                                                                "&lt;&lt;&lt;END ARTICLE&gt;&gt;&gt;")


def _safe_id(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


class LLMValueClassifier:
    def __init__(self, router: ProviderRouter, *, model: str | None = None, batch_size: int = 5,
                 structured: Literal["tool", "text"] = "tool", task: str = "classify",
                 max_chars: int = 6000) -> None:
        if batch_size < 1 or max_chars < 1:
            raise ValueError("batch_size and max_chars must be positive")
        self.router = router
        self.model = model
        self.batch_size = batch_size
        self.structured = structured
        self.task = task
        self.max_chars = max_chars
        self.calls: list[CallStats] = []

    async def classify(self, items: Sequence[ValueItem]) -> list[ClassifyOutcome]:
        outcomes: list[ClassifyOutcome] = []
        for index in range(0, len(items), self.batch_size):
            batch = items[index:index + self.batch_size]
            frames = [f'<<<ARTICLE id="{_safe_id(item.id)}">>>\nSource: {_escape(item.source)}\n'
                      f'{_escape(item.text[:self.max_chars])}\n<<<END ARTICLE>>>' for item in batch]
            messages: list[ChatMessage] = [
                {"role": "system", "content": AI_VALUE_MESSAGE_PROMPT},
                {"role": "user", "content": "\n\n".join(frames)},
            ]
            started = time.monotonic()
            response: LLMResponse | None = None
            error: str | None = None
            path: Path = "none"
            try:
                response = await self.router.complete(self.task, messages, model=self.model,
                                                      tool=_tool() if self.structured == "tool" else None)
                if self.structured == "tool" and response.structured is not None:
                    path = "tool"
                    parsed = response.structured
                else:
                    path = "text"
                    parsed = _text_json(response.content)
                raw_items = parsed.get("items")
                if not isinstance(raw_items, list):
                    raise ValueError("items must be an array")
                by_id: dict[str, ValueVerdict | str] = {}
                expected = {item.id for item in batch}
                for raw in raw_items:
                    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                        continue
                    gid = raw["id"]
                    if gid not in expected or gid in by_id:
                        continue
                    try:
                        by_id[gid] = _verdict(raw)
                    except (ValueError, KeyError, TypeError) as exc:
                        by_id[gid] = str(exc)
                for item in batch:
                    result = by_id.get(item.id)
                    if isinstance(result, ValueVerdict):
                        outcomes.append(ClassifyOutcome(item.id, result, None, path))
                    else:
                        outcomes.append(ClassifyOutcome(item.id, None, result or "missing in batch", path))
            except LLMEmptyResponseError as exc:
                response = exc.response
                error = str(exc)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            if error is not None:
                outcomes.extend(ClassifyOutcome(item.id, None, error, path) for item in batch)
            usage = response.usage if response is not None else None
            self.calls.append(CallStats(time.monotonic() - started,
                                        usage.prompt_tokens if usage else None,
                                        usage.completion_tokens if usage else None,
                                        response.cost_usd if response else None,
                                        response.cost_source if response else "none",
                                        response.provider if response else self.router.primary().profile.name,
                                        response.model if response else (self.model or self.router.model_for(self.task)),
                                        path, error))
        return outcomes
