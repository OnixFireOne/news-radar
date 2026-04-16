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

    async def complete(
        self,
        user_prompt: str,
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        """
        Send a request to the LLM and return the text response.

        Args:
            user_prompt: main user message
            system_prompt: system instruction
            temperature: 0 = deterministic, 1 = creative
            max_tokens: max tokens in response

        Returns:
            Model response as plain text
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
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
                return data["choices"][0]["message"]["content"].strip()

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
