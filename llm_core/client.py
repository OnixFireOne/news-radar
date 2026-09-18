"""
LLMCoreClient — minimal OpenAI-compatible chat-completions client with retry.

Retries on timeout, 429, and 5xx. When the server sends a Retry-After header,
that value wins over the exponential backoff. If every attempt fails, raises
LLMRetryExhaustedError (never fails silently).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, TypedDict

import httpx
from tenacity import (
    RetryCallState,
    RetryError,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from llm_core.config import LLMCoreConfig
from llm_core.mask import mask_secret

logger = logging.getLogger(__name__)

Role = Literal["system", "user", "assistant"]


class ChatMessage(TypedDict):
    role: Role
    content: str


class _Usage(TypedDict, total=False):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class _ChoiceMessage(TypedDict, total=False):
    role: str
    content: str | None


class _Choice(TypedDict, total=False):
    message: _ChoiceMessage


class _ChatCompletionResponse(TypedDict, total=False):
    choices: list[_Choice]
    usage: _Usage
    model: str


@dataclass(frozen=True)
class CompletionUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class CompletionResult:
    content: str
    model: str
    usage: CompletionUsage | None


class LLMCoreError(Exception):
    """Base error for llm_core failures."""


class LLMRetryExhaustedError(LLMCoreError):
    """All retry attempts failed. Carries the last underlying error for context."""

    def __init__(self, message: str, last_error: BaseException) -> None:
        super().__init__(message)
        self.last_error = last_error


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    return False


def _wait_for_retry(retry_state: RetryCallState) -> float:
    """Prefer the server's Retry-After header; fall back to exponential backoff."""
    outcome = retry_state.outcome
    if outcome is not None:
        exc = outcome.exception()
        if isinstance(exc, httpx.HTTPStatusError):
            retry_after = exc.response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    return float(retry_after)
                except ValueError:
                    pass
    return wait_exponential(multiplier=1, min=2, max=30)(retry_state)


class LLMCoreClient:
    """
    Minimal OpenAI-compatible chat-completions client.

    Config is injected by the host app — this class has no knowledge of
    env vars, settings.json, or any other app-specific state.
    """

    def __init__(self, cfg: LLMCoreConfig) -> None:
        self._cfg = cfg

    @retry(
        stop=stop_after_attempt(4),
        wait=_wait_for_retry,
        retry=retry_if_exception(_is_retryable),
        reraise=False,
    )
    async def _post_chat_completion(self, payload: dict[str, object]) -> _ChatCompletionResponse:
        async with httpx.AsyncClient(timeout=self._cfg.timeout) as client:
            try:
                resp = await client.post(
                    f"{self._cfg.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._cfg.api_key}",
                        "Content-Type": "application/json",
                        **self._cfg.default_headers,
                    },
                    json=payload,
                )
                resp.raise_for_status()
                data: _ChatCompletionResponse = resp.json()
                return data
            except httpx.HTTPStatusError as e:
                logger.error(
                    "llm_core: HTTP %s from %s (key=%s): %s",
                    e.response.status_code,
                    self._cfg.base_url,
                    mask_secret(self._cfg.api_key),
                    e.response.text[:200],
                )
                raise
            except httpx.TimeoutException:
                logger.error("llm_core: request to %s timed out after %ss", self._cfg.base_url, self._cfg.timeout)
                raise

    async def chat_completion(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        extra_payload: dict[str, object] | None = None,
    ) -> CompletionResult:
        """
        Send a chat-completions request and return the parsed result.

        Raises LLMRetryExhaustedError if every retry attempt failed — callers
        must not treat a missing result as "no answer", it is a hard failure.
        """
        payload: dict[str, object] = {
            "messages": messages,
            "temperature": temperature,
        }
        # Some OpenAI-compatible backends (e.g. Oobabooga) don't require "model"
        # and behave differently if an empty one is sent — omit it entirely.
        if model:
            payload["model"] = model
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if extra_payload:
            payload.update(extra_payload)

        try:
            data = await self._post_chat_completion(payload)
        except RetryError as e:
            last_exc = e.last_attempt.exception()
            assert last_exc is not None
            raise LLMRetryExhaustedError(
                f"LLM request to {self._cfg.base_url} failed after all retries: {last_exc}",
                last_error=last_exc,
            ) from last_exc

        choices = data.get("choices", [])
        content = ""
        if choices:
            msg = choices[0].get("message")
            if msg is not None:
                content = msg.get("content") or ""

        usage_raw = data.get("usage")
        usage: CompletionUsage | None = None
        if usage_raw:
            usage = CompletionUsage(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),
                completion_tokens=usage_raw.get("completion_tokens", 0),
                total_tokens=usage_raw.get("total_tokens", 0),
            )

        return CompletionResult(
            content=content.strip(),
            model=data.get("model", model),
            usage=usage,
        )
