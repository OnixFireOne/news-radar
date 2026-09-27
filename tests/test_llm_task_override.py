"""Per-call catalog task: the digest must use the catalog's digest model, not the client's default."""

import json

import httpx
import pytest
import respx

from analyzer import llm_client
from tests.test_llm_client_catalog import catalog_env, messages_response, restore_state  # noqa: F401


@pytest.mark.asyncio
@respx.mock
async def test_per_call_task_picks_task_model(catalog_env):  # noqa: F811
    client = llm_client.build_llm_client(env=catalog_env)
    completion = respx.post("https://cloud.test/v1/messages").mock(return_value=messages_response())
    await client.complete("hi")
    assert json.loads(completion.calls.last.request.content)["model"] == "sonnet"
    await client.complete("hi", task="digest")
    assert json.loads(completion.calls.last.request.content)["model"] == "opus"
    completion.mock(return_value=messages_response('{"a": 1}'))
    assert await client.complete_json("hi", task="digest") == {"a": 1}
    assert json.loads(completion.calls.last.request.content)["model"] == "opus"
