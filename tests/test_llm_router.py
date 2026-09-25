"""Ordered selection is exposed, but calls do not fail over yet."""

import json

import httpx
import pytest
import respx

from llm_core import ActiveProvider, CatalogError, ProviderProfile, ProviderRouter


def providers():
    return [ActiveProvider(ProviderProfile(
        name, f"https://{name}.example/v1", "chat_completions", "none",
        {"default": f"{name}-default", "digest": f"{name}-digest"}, "none",
    ), "") for name in ("first", "second")]


def test_models_order_and_defensive_copy():
    active = providers()
    router = ProviderRouter(active, 5)
    assert router.candidates() == active
    assert router.primary() == active[0]
    assert router.model_for("digest") == "first-digest"
    assert router.model_for("classify") == "first-default"
    assert router.model_for("digest", active[1]) == "second-digest"
    active.reverse()
    router.candidates().clear()
    assert router.primary().profile.name == "first"
    with pytest.raises(CatalogError):
        ProviderRouter([], 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 400])
async def test_complete_uses_only_primary(status):
    router = ProviderRouter(providers(), 5)
    with respx.mock(assert_all_called=False) as mock:
        first = mock.post("https://first.example/v1/chat/completions").mock(return_value=httpx.Response(
            status, json={"choices": [{"message": {"content": "ok"}}]},
        ))
        second = mock.post("https://second.example/v1/chat/completions").mock(return_value=httpx.Response(200))
        if status == 400:
            with pytest.raises(httpx.HTTPStatusError):
                await router.complete("digest", [{"role": "user", "content": "hi"}])
        else:
            result = await router.complete("digest", [{"role": "user", "content": "hi"}])
            assert result.provider == "first"
            assert result.model == "first-digest"
        assert first.call_count == 1
        assert json.loads(first.calls[0].request.content)["model"] == "first-digest"
        assert second.call_count == 0
