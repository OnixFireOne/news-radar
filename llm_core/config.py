"""Connection settings for an OpenAI-compatible endpoint."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LLMCoreConfig:
    """
    Everything LLMCoreClient needs to talk to a provider.

    The host application reads base_url/api_key/timeout from its own
    env vars or settings file and constructs this — llm_core itself
    never touches os.environ or any config file.
    """

    base_url: str
    api_key: str
    timeout: float = 300.0
    default_headers: dict[str, str] = field(default_factory=dict)
