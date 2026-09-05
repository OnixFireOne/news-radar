"""Secret masking for logs — never let a full API key/token reach a log line."""

from __future__ import annotations


def mask_secret(value: str, keep: int = 4) -> str:
    """
    Mask all but the last `keep` characters of a secret.

    Used whenever llm_core logs request/error details that might otherwise
    include the API key (e.g. in a repr of headers or config).
    """
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]
