"""
llm_core.client retry behavior — ТЗ #4 И1 acceptance criteria:
  - retry on 429 and 5xx (not just timeout), respecting Retry-After
  - after retries are exhausted, raise a clear error (never fail silently)
  - a non-retryable error (e.g. 400) is not retried
"""

import httpx
import pytest
import respx

from llm_core.client import LLMCoreClient, LLMRetryExhaustedError
from llm_core.config import LLMCoreConfig

BASE_URL = "https://proxy.example.com/v1"


def _client() -> LLMCoreClient:
    return LLMCoreClient(LLMCoreConfig(base_url=BASE_URL, api_key="secret-key-123", timeout=5.0))


def _ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "claude-sonnet-5",
            "choices": [{"message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        },
    )


@pytest.mark.asyncio
async def test_retries_on_429_and_respects_retry_after() -> None:
    with respx.mock() as router:
        route = router.post(f"{BASE_URL}/chat/completions")
        route.side_effect = [
            httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate limited"}),
            _ok_response(),
        ]
        result = await _client().chat_completion(
            messages=[{"role": "user", "content": "hi"}], model="claude-sonnet-5"
        )
        assert result.content == "hello"
        assert route.call_count == 2


@pytest.mark.asyncio
async def test_retries_on_5xx_then_succeeds() -> None:
    with respx.mock() as router:
        route = router.post(f"{BASE_URL}/chat/completions")
        route.side_effect = [
            httpx.Response(503, headers={"Retry-After": "0"}, json={"error": "unavailable"}),
            _ok_response(),
        ]
        result = await _client().chat_completion(
            messages=[{"role": "user", "content": "hi"}], model="claude-sonnet-5"
        )
        assert result.content == "hello"
        assert route.call_count == 2


@pytest.mark.asyncio
async def test_retry_exhausted_raises_clear_error_not_silent() -> None:
    with respx.mock() as router:
        route = router.post(f"{BASE_URL}/chat/completions")
        route.side_effect = [
            httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate limited"}) for _ in range(10)
        ]
        with pytest.raises(LLMRetryExhaustedError) as excinfo:
            await _client().chat_completion(
                messages=[{"role": "user", "content": "hi"}], model="claude-sonnet-5"
            )
        assert isinstance(excinfo.value.last_error, httpx.HTTPStatusError)
        assert route.call_count == 4  # stop_after_attempt(4)


@pytest.mark.asyncio
async def test_non_retryable_status_fails_immediately() -> None:
    with respx.mock() as router:
        route = router.post(f"{BASE_URL}/chat/completions")
        route.side_effect = [httpx.Response(400, json={"error": "bad request"})]
        with pytest.raises(httpx.HTTPStatusError):
            await _client().chat_completion(
                messages=[{"role": "user", "content": "hi"}], model="claude-sonnet-5"
            )
        assert route.call_count == 1  # no retry attempted
