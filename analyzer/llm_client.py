"""
LLM Client — wrapper for Oobabooga (or any OpenAI-compatible API).

Thin wrapper over llm_core.client.LLMCoreClient: this module owns everything
app-specific (env vars, the local-vs-cloud toggle, the local-GPU lock file,
llama.cpp/Qwen3 thinking-mode quirks); llm_core owns the actual HTTP call,
retry policy, and usage/mask/validation primitives, and knows nothing about
this app.

In legacy mode, local_mode (see set_local_mode/is_local_mode) controls two
local-GPU-only behaviors:
  - True  (default — preserves the original behavior of this module):
        LLMLock / is_llm_locked() work as before, and chat_template_kwargs
        (llama.cpp/Qwen3 "disable thinking" flag) is sent when requested.
  - False (cloud proxy mode, ТЗ #4 И1):
        LLMLock/is_llm_locked() become no-ops (no single-GPU to protect),
        and chat_template_kwargs is never sent (cloud models don't understand it).

Catalog mode uses the primary profile for thinking options; build_llm_client
sets the shared lock toggle from that profile's gpu_lock flag.
"""

import json
import logging
import os
import time
from dataclasses import replace
from collections.abc import Mapping
from typing import Any

import httpx

from llm_core.client import (
    ChatMessage,
    LLMCoreClient,
    LLMRetryExhaustedError,
)
from analyzer.usage_store import usage_category
from analyzer.llm_local import thinking_payload
from llm_core.catalog import load_catalog, resolve_active
from llm_core.client_messages import LLMEmptyResponseError
from llm_core.router import ProviderRouter
from llm_core.transport import JsonSchemaTool, LLMResponse
from llm_core.config import LLMCoreConfig, auth_headers
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


class LLMJSONError(ValueError):
    """The model answered, but not with parseable JSON; a fresh call usually succeeds."""


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
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = 300,
        *,
        router: ProviderRouter | None = None,
        task: str = "default",
    ) -> None:
        default_base_url = os.getenv("LLM_BASE_URL", "http://localhost:5000/v1")
        self.base_url = (base_url or default_base_url).rstrip("/")
        env_key: str = os.getenv("LLM_API_KEY", "not-needed")
        self.api_key: str = api_key if api_key else env_key
        default_model = os.getenv("LLM_MODEL", "")
        self.model: str = model or default_model
        self._router = router
        self._task = task
        self.strict_json_tasks: frozenset[str] = frozenset()
        self.is_legacy = router is None
        if router is not None:
            self.base_url = router.primary().profile.base_url
            self.api_key = router.primary().api_key
            self.model = router.model_for(task)
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

    @property
    def router(self) -> ProviderRouter | None:
        """Expose the catalog router without allowing replacement."""
        return self._router

    async def complete(
        self,
        user_prompt: str,
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = -1,
        disable_thinking: bool | None = None,
        task: str | None = None,
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
            task: catalog task for this call (e.g. "digest"); None = the client's task.
                Ignored in legacy single-endpoint mode.

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
        template_enabled = (self._router.primary().profile.chat_template_kwargs
                            if self._router is not None else _local_mode)
        extra_payload = thinking_payload(template_enabled, _no_think)

        resolved_max_tokens = None if max_tokens == -1 else max_tokens

        if self._router is not None:
            try:
                response = await self._router.complete(
                    task or self._task, messages, temperature=temperature,
                    max_tokens=resolved_max_tokens, extra_payload=extra_payload or None,
                )
            except LLMEmptyResponseError as exc:
                self._record_catalog_usage(exc.response, task)
                logger.warning(
                    "LLM empty response: provider=%s stop_reason=%s tokens=%s",
                    exc.response.provider, exc.stop_reason,
                    exc.usage.total_tokens if exc.usage is not None else "n/a",
                )
                return ""
            self._record_catalog_usage(response, task)
            return response.content

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
                    call_kind="complete", task=task or self._task, category=usage_category.get(),
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

    def _record_catalog_usage(self, response: LLMResponse, task: str | None = None) -> None:
        usage = response.usage
        _usage_tracker.record(UsageRecord(
            model=response.model,
            prompt_tokens=usage.prompt_tokens if usage is not None else 0,
            completion_tokens=usage.completion_tokens if usage is not None else 0,
            total_tokens=usage.total_tokens if usage is not None else 0,
            call_kind="complete", provider=response.provider,
            task=task or self._task, category=usage_category.get(),
            cost_usd=response.cost_usd, cost_source=response.cost_source,
        ))
        logger.info(
            "LLM usage: provider=%s model=%s prompt_tokens=%s completion_tokens=%s "
            "total_tokens=%s cost_usd=%s cost_source=%s",
            response.provider, response.model,
            usage.prompt_tokens if usage is not None else "n/a",
            usage.completion_tokens if usage is not None else "n/a",
            usage.total_tokens if usage is not None else "n/a",
            response.cost_usd if response.cost_usd is not None else "n/a",
            response.cost_source,
        )

    async def complete_json(
        self,
        user_prompt: str,
        system_prompt: str = "",
        temperature: float = 0.1,
        max_tokens: int = -1,  # без лимита — модель сама решает
        disable_thinking: bool | None = None,
        task: str | None = None,
        schema: JsonSchemaTool | None = None,
    ) -> dict[str, Any]:
        """
        Request expecting a JSON response.
        Automatically parses and validates the JSON.
        Handles cases where the model wraps JSON in markdown code blocks.
        """
        resolved_task = task or self._task
        use_tool = schema is not None and self._router is not None and resolved_task in self.strict_json_tasks
        logger.info("json path=%s task=%s", "tool" if use_tool else "text", resolved_task)
        if use_tool:
            assert schema is not None and self._router is not None
            messages: list[ChatMessage] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": user_prompt})
            no_think = self.disable_thinking if disable_thinking is None else disable_thinking
            extra = thinking_payload(self._router.primary().profile.chat_template_kwargs, no_think)
            try:
                response = await self._router.complete(
                    resolved_task, messages, temperature=temperature,
                    max_tokens=None if max_tokens == -1 else max_tokens,
                    extra_payload=extra or None, tool=replace(schema, strict=True),
                )
            except LLMEmptyResponseError as exc:
                self._record_catalog_usage(exc.response, task)
                logger.warning("LLM empty response: provider=%s stop_reason=%s tokens=%s",
                               exc.response.provider, exc.stop_reason,
                               exc.usage.total_tokens if exc.usage is not None else "n/a")
                raw = ""
            else:
                self._record_catalog_usage(response, task)
                if isinstance(response.structured, dict):
                    return response.structured
                raw = response.content
        else:
            raw = await self.complete(
                user_prompt=user_prompt, system_prompt=system_prompt,
                temperature=temperature, max_tokens=max_tokens,
                disable_thinking=disable_thinking, task=task,
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
            # Log the text around the error too: the break is often thousands of chars in.
            logger.error(f"Failed to parse LLM JSON response: {e}\nRaw: {raw[:300]}"
                         f"\nNear error: {raw[max(0, e.pos - 300):e.pos + 100]!r}")
            raise LLMJSONError(f"LLM returned invalid JSON: {raw[:200]}") from e

    async def health_check(self) -> bool:
        """Check if the LLM API is reachable."""
        if self._router is not None:
            active = self._router.primary()
            profile = active.profile
            if profile.models_path is None:
                return True
            headers = auth_headers(profile.auth_style, active.api_key)
            if profile.api_version is not None:
                headers["anthropic-version"] = profile.api_version
            headers.update(profile.extra_headers)
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{self.base_url}{profile.models_path}", headers=headers)
                    return resp.status_code == 200
            except Exception:
                return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/models")
                return resp.status_code == 200
        except Exception:
            return False


def build_llm_client(timeout: int = 300, task: str = "default",
                     env: Mapping[str, str] | None = None) -> LLMClient:
    """Select legacy defaults or the explicitly configured provider catalog."""
    environment = os.environ if env is None else env
    selection = environment.get("LLM_PROVIDERS", "").strip()
    if not selection:
        logger.info("LLM: legacy single-endpoint mode")
        return LLMClient(timeout=timeout)
    catalog = load_catalog(environment.get("LLM_PROVIDERS_FILE") or "/app/config/providers.json")
    names = [name.strip() for name in selection.split(",")]
    active = resolve_active(catalog, names, environment)
    router = ProviderRouter(active, timeout=float(timeout))
    set_local_mode(router.primary().profile.gpu_lock)
    logger.info("LLM: providers=%s model=%s", ", ".join(names), router.model_for(task))
    return LLMClient(timeout=timeout, router=router, task=task)
