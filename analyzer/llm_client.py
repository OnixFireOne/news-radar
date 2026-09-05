"""
LLM Client — wrapper for Oobabooga (or any OpenAI-compatible API).

Thin wrapper over llm_core.client.LLMCoreClient: this module owns everything
app-specific (env vars, the local-vs-cloud toggle, the local-GPU lock file,
llama.cpp/Qwen3 thinking-mode quirks); llm_core owns the actual HTTP call,
retry policy, and usage/mask/validation primitives, and knows nothing about
this app.

local_mode (see set_local_mode/is_local_mode) controls two local-GPU-only
behaviors:
  - True  (default — preserves the original behavior of this module):
        LLMLock / is_llm_locked() work as before, and chat_template_kwargs
        (llama.cpp/Qwen3 "disable thinking" flag) is sent when requested.
  - False (cloud proxy mode, ТЗ #4 И1):
        LLMLock/is_llm_locked() become no-ops (no single-GPU to protect),
        and chat_template_kwargs is never sent (cloud models don't understand it).
"""

import json
import logging
import os
import time
from typing import Any

import httpx

from llm_core.client import (
    ChatMessage,
    LLMCoreClient,
    LLMRetryExhaustedError,
)
from llm_core.config import LLMCoreConfig
from llm_core.usage import UsageRecord, UsageTracker

logger = logging.getLogger(__name__)

LLM_LOCK_FILE = "/app/data/llm.lock"

# ── Local vs. cloud proxy toggle (settings.json -> llm_local_mode) ──────────
# Default True: preserves the original local-GPU behavior for existing prod
# deployments that never set this key. Flipped via set_local_mode(), wired to
# ConfigWatcher by the process entry point (see analyzer/analyzer.py main()).
_local_mode: bool = True


def set_local_mode(enabled: bool) -> None:
    """Toggle local-GPU-only behaviors (LLMLock, chat_template_kwargs). See module docstring."""
    global _local_mode
    _local_mode = bool(enabled)
    logger.info(f"LLM local_mode set to {_local_mode}")


def is_local_mode() -> bool:
    return _local_mode


# ── Usage accounting (ТЗ #4 И1: token spend must be visible per call) ───────
_usage_tracker = UsageTracker()


def get_usage_tracker() -> UsageTracker:
    """Shared tracker accumulating UsageRecord for every complete()/complete_json() call."""
    return _usage_tracker


def is_llm_locked() -> bool:
    """Check if the LLM is currently locked by another process (e.g. digest generation).

    No-op (always False) when local_mode is False — the lock exists only to
    protect a single local GPU from concurrent requests; a cloud proxy has no
    such constraint.
    """
    if not _local_mode:
        return False
    if os.path.exists(LLM_LOCK_FILE):
        if time.time() - os.path.getmtime(LLM_LOCK_FILE) < 900:  # 15 min max lock
            return True
        else:
            try:
                os.remove(LLM_LOCK_FILE)
            except Exception:
                pass
    return False


class LLMLock:
    """Context manager for locking the LLM across processes.

    No-op when local_mode is False (see module docstring).
    """

    def __enter__(self) -> "LLMLock":
        if not _local_mode:
            return self
        try:
            with open(LLM_LOCK_FILE, "w") as f:
                f.write("1")
        except Exception:
            pass
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if not _local_mode:
            return
        try:
            if os.path.exists(LLM_LOCK_FILE):
                os.remove(LLM_LOCK_FILE)
        except Exception:
            pass


class LLMClient:
    """
    Simple client for any OpenAI-compatible chat completions API.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str = "not-needed",
        model: str | None = None,
        timeout: int = 300,
    ):
        default_base_url = os.getenv("LLM_BASE_URL", "http://localhost:5000/v1")
        self.base_url = (base_url or default_base_url).rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "not-needed")
        default_model = os.getenv("LLM_MODEL", "")
        self.model: str = model or default_model
        self.timeout = timeout
        # Default max_tokens headroom: Qwen3 with --jinja uses ~300-500 tokens for thinking
        # before writing the actual answer. Callers can override per-request.
        self._thinking_overhead = 700  # extra tokens reserved for <think> block
        # Instance-level default for thinking (from env). Per-call override is preferred —
        # pass disable_thinking=True/False to complete()/complete_json() directly.
        # LLM_DISABLE_THINKING=true: fast but lower quality (see settings.json llm_thinking_mode)
        _dt = os.getenv("LLM_DISABLE_THINKING", "false").lower()
        self.disable_thinking: bool = _dt in ("1", "true", "yes")

        self._core = LLMCoreClient(
            LLMCoreConfig(base_url=self.base_url, api_key=self.api_key, timeout=float(self.timeout))
        )

    async def complete(
        self,
        user_prompt: str,
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = -1,
        disable_thinking: bool | None = None,
    ) -> str:
        """
        Send a request to the LLM and return the text response.

        Args:
            user_prompt: main user message
            system_prompt: system instruction
            temperature: 0 = deterministic, 1 = creative
            max_tokens: max tokens in response. -1 = unlimited (server default).
            disable_thinking: override instance default. True=skip reasoning (fast),
                False=full thinking (quality). None=use LLM_DISABLE_THINKING env var.

        Returns:
            Model response as plain text (from content field)

        Raises:
            LLMRetryExhaustedError: every retry attempt (timeout/429/5xx) failed.
        """
        messages: list[ChatMessage] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        # Disable reasoning via Jinja chat template (llama.cpp / Qwen3), local mode only.
        # Tested: ~8-16x speedup, but lower quality (temp underestimated, topic less precise).
        # Per-call override takes priority; falls back to instance default from env.
        _no_think = self.disable_thinking if disable_thinking is None else disable_thinking
        extra_payload: dict[str, object] = {}
        if _local_mode and _no_think:
            extra_payload["chat_template_kwargs"] = {"enable_thinking": False}

        resolved_max_tokens = None if max_tokens == -1 else max_tokens

        try:
            result = await self._core.chat_completion(
                messages=messages,
                model=self.model,
                temperature=temperature,
                max_tokens=resolved_max_tokens,
                extra_payload=extra_payload or None,
            )
        except LLMRetryExhaustedError as e:
            logger.error(f"LLM request permanently failed after retries: {e}")
            raise

        if result.usage is not None:
            _usage_tracker.record(
                UsageRecord(
                    model=result.model,
                    prompt_tokens=result.usage.prompt_tokens,
                    completion_tokens=result.usage.completion_tokens,
                    total_tokens=result.usage.total_tokens,
                    call_kind="complete",
                )
            )
            logger.info(
                "LLM usage: model=%s prompt_tokens=%d completion_tokens=%d total_tokens=%d",
                result.model,
                result.usage.prompt_tokens,
                result.usage.completion_tokens,
                result.usage.total_tokens,
            )

        content = result.content
        if content:
            logger.info(f"LLM: content present ({len(content)} chars)")
        else:
            logger.info("LLM: content is empty")

        return content

    async def complete_json(
        self,
        user_prompt: str,
        system_prompt: str = "",
        temperature: float = 0.1,
        max_tokens: int = -1,  # без лимита — модель сама решает
        disable_thinking: bool | None = None,
    ) -> dict[str, Any]:
        """
        Request expecting a JSON response.
        Automatically parses and validates the JSON.
        Handles cases where the model wraps JSON in markdown code blocks.
        """
        raw = await self.complete(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            disable_thinking=disable_thinking,
        )

        # Strip markdown code fences if model added them
        raw = raw.strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        try:
            parsed: dict[str, Any] = json.loads(raw)
            return parsed
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON response: {e}\nRaw: {raw[:300]}")
            raise ValueError(f"LLM returned invalid JSON: {raw[:200]}")

    async def health_check(self) -> bool:
        """Check if the LLM API is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/models")
                return resp.status_code == 200
        except Exception:
            return False
