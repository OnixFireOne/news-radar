"""
llm_core.mask — API keys must never appear verbatim in logs (ТЗ #4, section 6:
"маскирование секретов в логах", ported from morning-post's src/ai).
"""

import logging

import httpx
import pytest
import respx

from llm_core.client import LLMCoreClient
from llm_core.config import LLMCoreConfig
from llm_core.mask import mask_secret


def test_mask_secret_keeps_only_last_chars() -> None:
    secret = "sk-supersecret-key-999"
    assert mask_secret(secret) == "*" * (len(secret) - 4) + "-999"


def test_mask_secret_short_value_fully_masked() -> None:
    assert mask_secret("abc") == "***"


def test_mask_secret_empty_value() -> None:
    assert mask_secret("") == ""


@pytest.mark.asyncio
async def test_api_key_never_appears_verbatim_in_error_logs(caplog) -> None:
    secret_key = "sk-supersecret-key-999"
    cfg = LLMCoreConfig(base_url="https://proxy.example.com/v1", api_key=secret_key, timeout=5.0)
    client = LLMCoreClient(cfg)

    with respx.mock() as router:
        route = router.post(f"{cfg.base_url}/chat/completions")
        route.side_effect = [httpx.Response(400, json={"error": "bad request"})]

        with caplog.at_level(logging.ERROR, logger="llm_core.client"):
            with pytest.raises(httpx.HTTPStatusError):
                await client.chat_completion(messages=[{"role": "user", "content": "hi"}], model="m")

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert secret_key not in log_text
    assert mask_secret(secret_key) in log_text
