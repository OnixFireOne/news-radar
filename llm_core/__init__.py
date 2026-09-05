"""
llm_core — portable LLM plugin (ported from morning-post's src/ai architecture).

No imports from analyzer/, bot/, or any other part of news-radar.
All configuration (base_url, api_key, timeouts, ...) is injected by the
host application via LLMCoreConfig — this package never reads env vars
or config files itself, so it can be dropped into another project as-is.
"""

from llm_core.client import (
    ChatMessage,
    CompletionResult,
    CompletionUsage,
    LLMCoreClient,
    LLMCoreError,
    LLMRetryExhaustedError,
)
from llm_core.config import LLMCoreConfig
from llm_core.mask import mask_secret
from llm_core.providers import ModelRegistry, ModelSpec
from llm_core.usage import UsageRecord, UsageTracker
from llm_core.usage_report import ModelUsageSummary, format_report_text, summarize_by_model
from llm_core.validator import FieldSpec, ValidationResult, validate_fields

__all__ = [
    "ChatMessage",
    "CompletionResult",
    "CompletionUsage",
    "LLMCoreClient",
    "LLMCoreError",
    "LLMRetryExhaustedError",
    "LLMCoreConfig",
    "mask_secret",
    "ModelRegistry",
    "ModelSpec",
    "UsageRecord",
    "UsageTracker",
    "ModelUsageSummary",
    "format_report_text",
    "summarize_by_model",
    "FieldSpec",
    "ValidationResult",
    "validate_fields",
]
