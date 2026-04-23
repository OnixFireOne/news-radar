"""
LLM Client — wrapper for Oobabooga (or any OpenAI-compatible API).

Oobabooga exposes an OpenAI-compatible endpoint at /v1,
so we use standard HTTP calls instead of the openai SDK for simplicity.
Works with Oobabooga, Ollama, vLLM, OpenAI — same interface.
"""

import json
import logging
import os
from typing import Any
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import time
import httpx

logger = logging.getLogger(__name__)

LLM_LOCK_FILE = "/app/data/llm.lock"

def is_llm_locked() -> bool:
    """Check if the LLM is currently locked by another process (e.g. digest generation)."""
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
    """Context manager for locking the LLM across processes."""
    def __enter__(self):
        try:
            with open(LLM_LOCK_FILE, "w") as f:
                f.write("1")
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
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
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "http://localhost:5000/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "not-needed")
        self.model = model or os.getenv("LLM_MODEL", "")
        self.timeout = timeout
        # Default max_tokens headroom: Qwen3 with --jinja uses ~300-500 tokens for thinking
        # before writing the actual answer. Callers can override per-request.
        self._thinking_overhead = 700  # extra tokens reserved for <think> block
        # Instance-level default for thinking (from env). Per-call override is preferred —
        # pass disable_thinking=True/False to complete()/complete_json() directly.
        # LLM_DISABLE_THINKING=true: fast but lower quality (see settings.json llm_thinking_mode)
        _dt = os.getenv("LLM_DISABLE_THINKING", "false").lower()
        self.disable_thinking: bool = _dt in ("1", "true", "yes")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.TimeoutException)
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
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        payload = {
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens != -1:
            payload["max_tokens"] = max_tokens
        if self.model:
            payload["model"] = self.model
        # Disable reasoning via Jinja chat template (llama.cpp / Qwen3).
        # Tested: ~8-16x speedup, but lower quality (temp underestimated, topic less precise).
        # Per-call override takes priority; falls back to instance default from env.
        _no_think = self.disable_thinking if disable_thinking is None else disable_thinking
        if _no_think:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                message = data["choices"][0]["message"]

                # Primary answer is always in content
                content = (message.get("content") or "").strip()



                if content:
                    logger.info(f"LLM: content present ({len(content)} chars)")
                else:
                    logger.info("LLM: content is empty")

                return content

        except httpx.TimeoutException:
            logger.error(f"LLM request timed out after {self.timeout}s")
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"LLM HTTP error {e.response.status_code}: {e.response.text[:200]}")
            raise
        except Exception as e:
            logger.error(f"LLM error: {e}")
            raise

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
            return json.loads(raw)
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
