"""Unknown enums remain usable; parallel batches preserve outcome order."""

import asyncio
import re
from unittest.mock import AsyncMock

import pytest

from analyzer.value_classifier import LLMValueClassifier, ValueItem
from llm_core import ActiveProvider, LLMResponse, ProviderProfile, ProviderRouter


def make_router():
    active = ActiveProvider(ProviderProfile("test", "https://test.example/v1", "messages",
                                            "none", {"default": "m", "classify": "m"}, "none",
                                            max_tokens=100), "")
    return ProviderRouter([active], 5)


def raw_verdict(gid, **extra):
    return dict(id=gid, temperature=5, content_type="tutorial", value_score=8,
                has_outcome=True, takeaway="Вывод", topic="agents", summary="Кратко",
                keywords=["AI"], is_ad=False) | extra


def response(items):
    return LLMResponse("", "m", None, None, None, None, "none", "test", {"items": items})


@pytest.mark.asyncio
@pytest.mark.parametrize("field, unknown, expected", [
    ("content_type", "other", "opinion"), ("topic", "unknown", "other"),
])
async def test_unknown_enum_is_coerced(field, unknown, expected, caplog):
    router = make_router()
    router.complete = AsyncMock(return_value=response([raw_verdict("a", **{field: unknown})]))
    outcome = (await LLMValueClassifier(router).classify([ValueItem("a", "body")]))[0]
    assert outcome.error is None
    assert getattr(outcome.verdict, field) == expected
    assert outcome.verdict.value_score == 8
    assert any(record.levelname == "WARNING" and field in record.message for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 3])
async def test_parallel_batches_keep_input_order_and_call_stats(concurrency):
    router = make_router()
    active = peak = 0
    completed = []

    async def complete(task, messages, **kwargs):
        nonlocal active, peak
        ids = re.findall(r'<<<ARTICLE id="([^"]+)"', messages[1]["content"])
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.04 if ids[0] == "0" else 0.001)
        active -= 1
        completed.append(ids[0])
        return response([raw_verdict(gid) for gid in reversed(ids)])

    router.complete = AsyncMock(side_effect=complete)
    classifier = LLMValueClassifier(router, concurrency=concurrency)
    outcomes = await classifier.classify([ValueItem(str(i), "body") for i in range(17)])
    assert [o.item_id for o in outcomes] == [str(i) for i in range(17)]
    assert all(o.verdict is not None and o.error is None for o in outcomes)
    assert len(classifier.calls) == 4
    assert peak == concurrency
    assert all(c.model == "m" and c.path == "tool" for c in classifier.calls)
    if concurrency == 3:
        assert completed[0] != "0"


@pytest.mark.asyncio
async def test_default_concurrency_is_sequential():
    assert LLMValueClassifier(make_router()).concurrency == 1
    with pytest.raises(ValueError):
        LLMValueClassifier(make_router(), concurrency=0)
