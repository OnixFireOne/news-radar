"""
ТЗ #4 И1 acceptance: llm_local_mode gates two local-GPU-only behaviors —
LLMLock/is_llm_locked() and chat_template_kwargs. False (cloud proxy) must
disable both; True (default, current prod behavior) must keep them exactly
as before.
"""

import logging
from unittest.mock import AsyncMock

import pytest

from analyzer import llm_client
from llm_core.client import CompletionResult, CompletionUsage


@pytest.fixture(autouse=True)
def _restore_local_mode():
    """Each test flips the module-level toggle — restore it so tests don't leak state."""
    original = llm_client.is_local_mode()
    yield
    llm_client.set_local_mode(original)


def test_lock_is_noop_in_cloud_mode(tmp_path, monkeypatch) -> None:
    lock_path = tmp_path / "llm.lock"
    monkeypatch.setattr(llm_client, "LLM_LOCK_FILE", str(lock_path))
    llm_client.set_local_mode(False)

    assert llm_client.is_llm_locked() is False
    with llm_client.LLMLock():
        assert not lock_path.exists()
    assert not lock_path.exists()
    assert llm_client.is_llm_locked() is False


def test_lock_still_works_in_local_mode(tmp_path, monkeypatch) -> None:
    lock_path = tmp_path / "llm.lock"
    monkeypatch.setattr(llm_client, "LLM_LOCK_FILE", str(lock_path))
    llm_client.set_local_mode(True)

    assert llm_client.is_llm_locked() is False
    with llm_client.LLMLock():
        assert lock_path.exists()
        assert llm_client.is_llm_locked() is True
    assert not lock_path.exists()
    assert llm_client.is_llm_locked() is False


@pytest.mark.asyncio
async def test_chat_template_kwargs_sent_only_in_local_mode() -> None:
    client = llm_client.LLMClient(base_url="http://x/v1", api_key="k", model="m")
    mock_chat = AsyncMock(return_value=CompletionResult(content="ok", model="m", usage=None))
    client._core.chat_completion = mock_chat  # type: ignore[method-assign]

    llm_client.set_local_mode(True)
    await client.complete("hi", disable_thinking=True)
    assert mock_chat.call_args.kwargs["extra_payload"] == {"chat_template_kwargs": {"enable_thinking": False}}

    llm_client.set_local_mode(False)
    await client.complete("hi", disable_thinking=True)
    assert mock_chat.call_args.kwargs["extra_payload"] is None


@pytest.mark.asyncio
async def test_usage_is_tracked_and_logged_per_call(caplog) -> None:
    client = llm_client.LLMClient(base_url="http://x/v1", api_key="k", model="m")
    mock_chat = AsyncMock(
        return_value=CompletionResult(
            content="ok",
            model="claude-sonnet-5",
            usage=CompletionUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
        )
    )
    client._core.chat_completion = mock_chat  # type: ignore[method-assign]
    llm_client.get_usage_tracker().clear()

    with caplog.at_level(logging.INFO, logger="analyzer.llm_client"):
        await client.complete("hi")

    assert any(
        "claude-sonnet-5" in r.getMessage()
        and "prompt_tokens=5" in r.getMessage()
        and "completion_tokens=3" in r.getMessage()
        and "total_tokens=8" in r.getMessage()
        for r in caplog.records
    )

    records = llm_client.get_usage_tracker().all()
    assert len(records) == 1
    assert records[0].model == "claude-sonnet-5"
    assert records[0].total_tokens == 8
