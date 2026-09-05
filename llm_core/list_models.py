"""Query the proxy's /models endpoint for the exact model ids it serves."""

from __future__ import annotations

from typing import TypedDict

import httpx

from llm_core.config import LLMCoreConfig
from llm_core.providers import ModelSpec


class _ModelEntry(TypedDict, total=False):
    id: str
    object: str


class _ModelsResponse(TypedDict, total=False):
    data: list[_ModelEntry]
    object: str


async def list_models(cfg: LLMCoreConfig) -> list[ModelSpec]:
    """
    GET {base_url}/models and return the model ids the proxy currently serves.

    Use this instead of hardcoding model ids in config — proxies rename or
    version their models independently of this codebase.
    """
    async with httpx.AsyncClient(timeout=cfg.timeout) as client:
        resp = await client.get(
            f"{cfg.base_url}/models",
            headers={"Authorization": f"Bearer {cfg.api_key}", **cfg.default_headers},
        )
        resp.raise_for_status()
        payload: _ModelsResponse = resp.json()

    return [ModelSpec(id=entry["id"]) for entry in payload.get("data", []) if "id" in entry]
