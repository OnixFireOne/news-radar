"""Protocol-neutral requests and the single transport dispatch point."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from llm_core.catalog import CatalogError, ProviderProfile, compute_cost
from llm_core.client import ChatMessage, CompletionUsage, LLMCoreClient
from llm_core.config import LLMCoreConfig


@dataclass(frozen=True)
class JsonSchemaTool:
    name: str
    description: str
    schema: dict[str, object]


@dataclass(frozen=True)
class LLMRequest:
    model: str
    messages: list[ChatMessage]
    temperature: float = 0.3
    max_tokens: int | None = None
    extra_payload: dict[str, object] | None = None
    tool: JsonSchemaTool | None = None


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    usage: CompletionUsage | None
    stop_reason: str | None
    raw_usage: dict[str, object] | None
    cost_usd: float | None
    cost_source: str
    provider: str
    structured: dict[str, object] | None = None
    tool_calls_seen: tuple[str, ...] = ()


class Transport(Protocol):
    async def complete(self, req: LLMRequest) -> LLMResponse: ...


class ChatCompletionsTransport:
    def __init__(self, profile: ProviderProfile, api_key: str, timeout: float) -> None:
        self._profile = profile
        self._client = LLMCoreClient(LLMCoreConfig(
            base_url=profile.base_url, api_key=api_key, timeout=timeout,
            default_headers=dict(profile.extra_headers), auth_style=profile.auth_style,
        ))

    async def complete(self, req: LLMRequest) -> LLMResponse:
        result = await self._client.chat_completion(
            messages=req.messages, model=req.model, temperature=req.temperature,
            max_tokens=req.max_tokens, extra_payload=req.extra_payload,
            tool=req.tool,
        )
        usage = result.usage
        raw = result.raw_usage
        if raw is not None and not all(type(raw.get(key)) is int for key in
                                       ("prompt_tokens", "completion_tokens")):
            usage = None
        return LLMResponse(
            content=result.content, model=result.model, usage=usage,
            stop_reason=result.finish_reason, raw_usage=raw,
            cost_usd=compute_cost(self._profile, result.model, usage, raw),
            cost_source=self._profile.cost_source, provider=self._profile.name,
            structured=result.structured, tool_calls_seen=result.tool_calls_seen,
        )


def create_transport(profile: ProviderProfile, api_key: str, timeout: float) -> Transport:
    match profile.protocol:
        case "chat_completions":
            return ChatCompletionsTransport(profile, api_key, timeout)
        case "messages":
            from llm_core.client_messages import MessagesTransport
            return MessagesTransport(profile, api_key, timeout)
        case _:
            raise CatalogError(f'provider "{profile.name}": protocol is unknown')
