"""Ordered providers with lazy transports; task models belong to profiles."""

from __future__ import annotations

from collections.abc import Sequence

from llm_core.catalog import ActiveProvider, CatalogError
from llm_core.client import ChatMessage
from llm_core.transport import LLMRequest, LLMResponse, Transport, create_transport


class ProviderRouter:
    def __init__(self, active: Sequence[ActiveProvider], timeout: float) -> None:
        if not active:
            raise CatalogError("router: at least one active provider is required")
        self._active = tuple(active)
        self._timeout = timeout
        self._transports: dict[str, Transport] = {}

    def candidates(self) -> list[ActiveProvider]:
        return list(self._active)

    def primary(self) -> ActiveProvider:
        return self._active[0]

    def model_for(self, task: str, provider: ActiveProvider | None = None) -> str:
        models = (provider or self.primary()).profile.models
        return models.get(task, models["default"])

    async def complete(self, task: str, messages: list[ChatMessage], temperature: float = 0.3,
                       max_tokens: int | None = None,
                       extra_payload: dict[str, object] | None = None) -> LLMResponse:
        provider = self.primary()
        name = provider.profile.name
        if name not in self._transports:
            self._transports[name] = create_transport(provider.profile, provider.api_key, self._timeout)
        # failover: next iteration
        return await self._transports[name].complete(LLMRequest(
            model=self.model_for(task), messages=messages, temperature=temperature,
            max_tokens=max_tokens, extra_payload=extra_payload,
        ))
