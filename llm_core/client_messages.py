"""Messages wire protocol using the legacy client's retry policy."""

from __future__ import annotations

import logging
from typing import TypedDict

import httpx
from tenacity import RetryError, retry, retry_if_exception, stop_after_attempt

from llm_core.catalog import CatalogError, ProviderProfile, compute_cost
from llm_core.client import (
    CompletionUsage, LLMCoreError, LLMRetryExhaustedError, _is_retryable, _wait_for_retry,
)
from llm_core.config import auth_headers
from llm_core.mask import mask_secret
from llm_core.transport import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)


class _Block(TypedDict, total=False):
    type: str
    text: str


class _MessagesResponse(TypedDict, total=False):
    content: list[_Block]
    model: str
    usage: dict[str, object]
    stop_reason: str | None


class LLMEmptyResponseError(LLMCoreError):
    """A billed response without text, retaining accounting for the host."""

    def __init__(self, response: LLMResponse) -> None:
        super().__init__(f'provider "{response.provider}": empty response (stop_reason={response.stop_reason})')
        self.response = response
        self.stop_reason = response.stop_reason
        self.usage = response.usage
        self.raw_usage = response.raw_usage
        self.cost_usd = response.cost_usd


class MessagesTransport:
    def __init__(self, profile: ProviderProfile, api_key: str, timeout: float) -> None:
        if profile.max_tokens is None:
            raise CatalogError(f'provider "{profile.name}": max_tokens is required for messages')
        self._profile = profile
        self._api_key = api_key
        self._timeout = timeout

    @retry(stop=stop_after_attempt(4), wait=_wait_for_retry,
           retry=retry_if_exception(_is_retryable), reraise=False)
    async def _post(self, payload: dict[str, object]) -> _MessagesResponse:
        profile = self._profile
        headers = {**auth_headers(profile.auth_style, self._api_key),
                   "Content-Type": "application/json"}
        if profile.api_version is not None:
            headers["anthropic-version"] = profile.api_version
        headers.update(profile.extra_headers)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(f"{profile.base_url}/messages", headers=headers, json=payload)
                resp.raise_for_status()
                data: _MessagesResponse = resp.json()
                return data
            except httpx.HTTPStatusError as exc:
                # Never log a provider's body: it may echo request credentials.
                logger.error("llm_core: HTTP %s from provider %s (key=%s)",
                             exc.response.status_code, profile.name, mask_secret(self._api_key))
                raise
            except httpx.TimeoutException:
                logger.error("llm_core: provider %s timed out after %ss", profile.name, self._timeout)
                raise

    async def complete(self, req: LLMRequest) -> LLMResponse:
        profile = self._profile
        payload: dict[str, object] = {
            "model": req.model, "max_tokens": req.max_tokens or profile.max_tokens,
            "messages": [message for message in req.messages if message["role"] != "system"],
            "temperature": req.temperature,
        }
        systems = [message["content"] for message in req.messages if message["role"] == "system"]
        if systems:
            payload["system"] = "\n\n".join(systems)
        if req.extra_payload:
            payload.update(req.extra_payload)
        if "chat_template_kwargs" in payload:
            del payload["chat_template_kwargs"]
            logger.debug("llm_core: dropping chat_template_kwargs for messages")
        try:
            data = await self._post(payload)
        except RetryError as exc:
            last = exc.last_attempt.exception()
            assert last is not None
            raise LLMRetryExhaustedError(
                f'LLM request to provider "{profile.name}" failed after all retries', last,
            ) from last
        raw = data.get("usage")
        usage = None
        if raw is not None:
            tokens_in, tokens_out = raw.get("input_tokens"), raw.get("output_tokens")
            if type(tokens_in) is int and type(tokens_out) is int:
                usage = CompletionUsage(tokens_in, tokens_out, tokens_in + tokens_out)
        content = "".join(block.get("text", "") for block in data.get("content", [])
                          if block.get("type") == "text")
        model = data.get("model", req.model)
        response = LLMResponse(
            content=content, model=model, usage=usage, stop_reason=data.get("stop_reason"),
            raw_usage=raw, cost_usd=compute_cost(profile, model, usage, raw),
            cost_source=profile.cost_source, provider=profile.name,
        )
        if not content.strip():
            raise LLMEmptyResponseError(response)
        return response
