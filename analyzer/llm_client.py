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

import httpx

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Simple client for any OpenAI-compatible chat completions API.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str = "not-needed",
        model: str | None = None,
        timeout: int = 60,
    ):
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "http://localhost:5000/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "not-needed")
        self.model = model or os.getenv("LLM_MODEL", "")
        self.timeout = timeout
        # Default max_tokens headroom: Qwen3 with --jinja uses ~300-500 tokens for thinking
        # before writing the actual answer. Callers can override per-request.
        self._thinking_overhead = 700  # extra tokens reserved for <think> block

    async def complete(
        self,
        user_prompt: str,
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 1024,
        enable_thinking: bool = False,
    ) -> str:
        """
        Send a request to the LLM and return the text response.

        Args:
            user_prompt: main user message
            system_prompt: system instruction
            temperature: 0 = deterministic, 1 = creative
            max_tokens: max tokens in response
            enable_thinking: strictly toggle Jinja-based reasoning process for Qwen3/Llama servers

        Returns:
            Model response as plain text
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        overhead = self._thinking_overhead if enable_thinking else 0
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens + overhead,
            "enable_thinking": enable_thinking,
            # llama.cpp server and vLLM parse Jinja template kwargs here:
            "chat_template_kwargs": {
                "enable_thinking": enable_thinking
            }
        }
        if self.model:
            payload["model"] = self.model

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

                content = (message.get("content") or "").strip()

                # Strip <think>...</think> blocks (some servers wrap thinking in tags)
                import re as _re
                content = _re.sub(r"<think>[\s\S]*?</think>", "", content).strip()

                # llama.cpp with --jinja + Qwen3: when enable_thinking is not honored,
                # the model puts everything (thinking + answer) into reasoning_content
                # and leaves content empty. Extract the real answer from reasoning_content.
                if not content:
                    raw_reasoning = (message.get("reasoning_content") or "").strip()
                    if raw_reasoning:
                        import json as _json
                        # Find all JSON-like blocks and validate them
                        # Scan from the END — the actual answer is always last
                        extracted = None
                        for m in reversed(list(_re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\}", raw_reasoning))):
                            candidate = m.group(0).strip()
                            try:
                                _json.loads(candidate)
                                extracted = candidate
                                break
                            except (ValueError, _json.JSONDecodeError):
                                continue

                        if extracted:
                            content = extracted
                            logger.debug("LLM: extracted valid JSON from reasoning_content")
                        else:
                            # Plain-text response: last paragraph that isn't thinking/list
                            skip_markers = ("Thinking Process:", "**Analyze", "*   Role:", "Wait,", "Let me")
                            parts = [p.strip() for p in _re.split(r"\n{2,}", raw_reasoning) if p.strip()]
                            for part in reversed(parts):
                                if not any(m in part for m in skip_markers) \
                                        and not _re.match(r"^\s*(\d+\.|-|\*|\[)", part):
                                    content = part
                                    logger.debug("LLM: extracted plain text from reasoning_content")
                                    break
                            if not content and parts:
                                content = parts[-1]

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
        max_tokens: int = 1024,
        enable_thinking: bool = False,
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
            enable_thinking=enable_thinking,
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
