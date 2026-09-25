"""Application catalog wiring, legacy compatibility, and billed empty responses."""

import json
import logging
from pathlib import Path

import httpx
import pytest
import respx

from analyzer import llm_client
from llm_core.catalog import CatalogError, find_todo_field, load_catalog, resolve_active


@pytest.fixture(autouse=True)
def restore_state():
    original = llm_client.is_local_mode()
    llm_client.get_usage_tracker().clear()
    yield
    llm_client.set_local_mode(original)
    llm_client.get_usage_tracker().clear()


@pytest.fixture
def catalog_env(tmp_path):
    cloud = {
        "name": "cloud.messages", "base_url": "https://cloud.test/v1",
        "protocol": "messages", "auth_style": "x-api-key",
        "api_version": "2023-06-01", "max_tokens": 8192,
        "models_path": "/models", "models": {"default": "sonnet", "digest": "opus"},
        "cost_source": "table", "price_table": {
            "sonnet": {"in_per_million": 3, "out_per_million": 15},
            "opus": {"in_per_million": 5, "out_per_million": 25},
        },
    }
    local = {
        "name": "local", "base_url": "http://local.test/v1",
        "protocol": "chat_completions", "auth_style": "none",
        "models": {"default": "qwen"}, "cost_source": "none",
        "gpu_lock": True, "chat_template_kwargs": True, "max_concurrency": 1,
    }
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({cloud["name"]: cloud, local["name"]: local}))
    return {"LLM_PROVIDERS": "cloud.messages", "LLM_PROVIDERS_FILE": str(path),
            "LLM_KEY_CLOUD": "test-secret-never-in-logs"}


def messages_response(content="ok"):
    return httpx.Response(200, json={
        "model": "sonnet", "content": [{"type": "text", "text": content}],
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "stop_reason": "end_turn" if content else "max_tokens",
    })


def chat_response():
    return httpx.Response(200, json={
        "model": "qwen", "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    })


@pytest.mark.asyncio
@respx.mock
async def test_cloud_wire_lock_key_and_usage(catalog_env, tmp_path, monkeypatch, caplog):
    lock = tmp_path / "llm.lock"
    monkeypatch.setattr(llm_client, "LLM_LOCK_FILE", str(lock))
    route = respx.post("https://cloud.test/v1/messages").mock(return_value=messages_response())
    with caplog.at_level(logging.INFO):
        client = llm_client.build_llm_client(env=catalog_env)
        assert not client.is_legacy
        with llm_client.LLMLock():
            assert not lock.exists()
            assert not llm_client.is_llm_locked()
        assert await client.complete("hello", "system", disable_thinking=True) == "ok"
    request = route.calls.last.request
    assert request.headers["x-api-key"] == catalog_env["LLM_KEY_CLOUD"]
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert json.loads(request.content) == {
        "model": "sonnet", "messages": [{"role": "user", "content": "hello"}],
        "system": "system", "temperature": 0.3, "max_tokens": 8192,
    }
    record, = llm_client.get_usage_tracker().all()
    assert record.provider == "cloud.messages"
    assert record.cost_source == "table"
    assert record.cost_usd == pytest.approx(0.00006)
    assert "provider=cloud.messages" in caplog.text
    assert catalog_env["LLM_KEY_CLOUD"] not in caplog.text


@pytest.mark.asyncio
@respx.mock
async def test_local_wire_and_lock(catalog_env, tmp_path, monkeypatch, caplog):
    catalog_env["LLM_PROVIDERS"] = "local"
    lock = tmp_path / "llm.lock"
    monkeypatch.setattr(llm_client, "LLM_LOCK_FILE", str(lock))
    route = respx.post("http://local.test/v1/chat/completions").mock(return_value=chat_response())
    client = llm_client.build_llm_client(env=catalog_env)
    with llm_client.LLMLock():
        assert lock.exists()
        assert llm_client.is_llm_locked()
    assert not lock.exists()
    with caplog.at_level(logging.INFO):
        await client.complete("hi", disable_thinking=True)
    assert "cost_usd=n/a" in caplog.text
    assert json.loads(route.calls.last.request.content)["chat_template_kwargs"] == {"enable_thinking": False}
    assert "authorization" not in route.calls.last.request.headers
    await client.complete("hi", disable_thinking=False)
    assert "chat_template_kwargs" not in json.loads(route.calls.last.request.content)


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("selection", [None, ""])
async def test_legacy_env_key_and_request(monkeypatch, selection):
    monkeypatch.delenv("LLM_PROVIDERS", raising=False)
    if selection is not None:
        monkeypatch.setenv("LLM_PROVIDERS", selection)
    monkeypatch.setenv("LLM_BASE_URL", "http://legacy.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "legacy-test-secret")
    monkeypatch.setenv("LLM_MODEL", "qwen")
    llm_client.set_local_mode(True)
    route = respx.post("http://legacy.test/v1/chat/completions").mock(return_value=chat_response())
    client = llm_client.build_llm_client()
    assert client.is_legacy
    assert client.api_key == "legacy-test-secret"
    await client.complete("hi", "system", disable_thinking=True)
    assert route.calls.last.request.headers["authorization"] == "Bearer legacy-test-secret"
    payload = json.loads(route.calls.last.request.content)
    assert payload["messages"] == [{"role": "system", "content": "system"}, {"role": "user", "content": "hi"}]
    assert payload["model"] == "qwen"
    assert "max_tokens" not in payload
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_missing_key_fails(catalog_env, caplog):
    del catalog_env["LLM_KEY_CLOUD"]
    with pytest.raises(CatalogError, match="LLM_KEY_CLOUD"):
        llm_client.build_llm_client(env=catalog_env)
    assert "test-secret-never-in-logs" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["cloud.messages, local", "local, cloud.messages"])
async def test_priority_changes_endpoint(catalog_env, order):
    catalog_env["LLM_PROVIDERS"] = order
    # A configured respx.mock(...) is its own router: routes must be added to it, not to the global one.
    with respx.mock(assert_all_called=False) as router:
        cloud = router.post("https://cloud.test/v1/messages").mock(return_value=messages_response())
        local = router.post("http://local.test/v1/chat/completions").mock(return_value=chat_response())
        await llm_client.build_llm_client(env=catalog_env).complete("hi")
    assert cloud.called == order.startswith("cloud")
    assert local.called == order.startswith("local")


@pytest.mark.asyncio
@respx.mock
async def test_empty_response_is_accounted(catalog_env, caplog):
    respx.post("https://cloud.test/v1/messages").mock(return_value=messages_response(""))
    client = llm_client.build_llm_client(env=catalog_env)
    with caplog.at_level(logging.INFO):
        assert await client.complete("hi") == ""
    record, = llm_client.get_usage_tracker().all()
    assert record.total_tokens == 12
    assert record.cost_usd == pytest.approx(0.00006)
    assert record.cost_source == "table"
    assert record.provider == "cloud.messages"
    assert any(r.levelno == logging.WARNING and "stop_reason=max_tokens" in r.message for r in caplog.records)
    assert catalog_env["LLM_KEY_CLOUD"] not in caplog.text


def test_real_catalog_todo_and_local():
    catalog = load_catalog(Path(__file__).resolve().parents[1] / "config/providers.json")
    for name in ("aiprime.messages", "aiprime.chat_completions"):
        assert find_todo_field(catalog[name]) == "price_table"
        with pytest.raises(CatalogError, match=r"price_table.*TODO\(unverified\)"):
            resolve_active(catalog, [name], {"LLM_KEY_AIPRIME": "test-key"})
    assert resolve_active(catalog, ["local"], {})[0].profile.gpu_lock


@pytest.mark.parametrize("contents", [None, "{", "{}"])
def test_custom_catalog_error_has_no_fallback(catalog_env, tmp_path, contents):
    path = tmp_path / "custom.json"
    if contents is not None:
        path.write_text(contents)
    catalog_env["LLM_PROVIDERS_FILE"] = str(path)
    with pytest.raises(CatalogError):
        llm_client.build_llm_client(env=catalog_env)


@pytest.mark.asyncio
@respx.mock
async def test_health_auth_and_task_model(catalog_env):
    route = respx.get("https://cloud.test/v1/models").mock(return_value=httpx.Response(200))
    client = llm_client.build_llm_client(env=catalog_env, task="digest")
    assert client.model == "opus"
    assert await client.health_check()
    assert route.calls.last.request.headers["x-api-key"] == catalog_env["LLM_KEY_CLOUD"]
    completion = respx.post("https://cloud.test/v1/messages").mock(return_value=messages_response())
    await client.complete("hi", max_tokens=42)
    assert json.loads(completion.calls.last.request.content)["model"] == "opus"
    assert json.loads(completion.calls.last.request.content)["max_tokens"] == 42
    catalog_env["LLM_PROVIDERS"] = "local"
    assert await llm_client.build_llm_client(env=catalog_env).health_check()


@pytest.mark.parametrize("price_table", ["unexpected", 12, {"sonnet": {"in_per_million": "TODO(unverified)", "out_per_million": 15}}])
def test_price_validation_stays_strict(catalog_env, price_table):
    path = Path(catalog_env["LLM_PROVIDERS_FILE"])
    catalog = json.loads(path.read_text())
    catalog["cloud.messages"]["price_table"] = price_table
    path.write_text(json.dumps(catalog))
    with pytest.raises(CatalogError, match="price_table"):
        load_catalog(path)
