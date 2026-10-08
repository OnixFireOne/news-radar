"""Offline contracts for digest versions, announcements and deployment polling."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import respx

from analyzer.analyzer import NewsAnalyzer
from analyzer.json_schemas import DIGEST_AI_VALUE_SCHEMA, DIGEST_AI_VALUE_SCHEMA_V2
from analyzer.pipeline import extras
from analyzer.pipeline.context import DigestContext
from analyzer.pipeline.writers import _ai_value_compose, _ai_value_render
from analyzer.prompts import DIGEST_PROMPT_AI_VALUE, DIGEST_PROMPT_AI_VALUE_V2
from analyzer.renderer import render_digest
from analyzer.site_digest import MONTHS, build_digest_post, build_telegram_announce

NOW = datetime(2026, 10, 7)
URL = 'https://neuronavt.blog/posts/2026-10-07-ai-radar/'


def data(count: int = 3) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [{'id': i, 'text': 'Article', 'content_type': 'tool_release', 'value_score': 8}
            for i in range(1, count + 1)]
    draft = {'items': [{'source_id': str(i), 'title': f'Title {i}', 'takeaway': 'Lesson', 'summary': 'Summary'}
                       for i in range(1, count + 1)]}
    return draft, rows


def context(template: dict[str, Any], draft: dict[str, Any], rows: list[dict[str, Any]]) -> DigestContext:
    analyzer = Mock()
    analyzer.llm = AsyncMock()
    ctx = DigestContext(cast(NewsAnalyzer, analyzer), {}, {}, 'ai_value', template,
                        8, 5, NOW, False, False)
    ctx.artifacts.update(draft=draft, source_map={str(i): f'https://example.org/{i}'
                                               for i in range(1, len(rows) + 1)}, site_now=NOW)
    return ctx


@pytest.mark.asyncio
@pytest.mark.parametrize('version,v2', [('ai_value-digest-v1', False), ('ai_value-digest-v2', True), ('unknown', False)])
async def test_prompt_version(version: str, v2: bool, caplog: pytest.LogCaptureFixture) -> None:
    draft, rows = data()
    ctx = context({'digest_prompt_version': version}, draft, rows)
    llm = cast(Any, ctx.analyzer.llm)
    llm.complete_json.return_value = dict(draft, lead=123, highlights=[None, '', ' Полезное '])
    result = await _ai_value_compose(rows, ctx)
    kwargs = llm.complete_json.call_args.kwargs
    assert kwargs['schema'] == (DIGEST_AI_VALUE_SCHEMA_V2 if v2 else DIGEST_AI_VALUE_SCHEMA)
    prompt = DIGEST_PROMPT_AI_VALUE_V2 if v2 else DIGEST_PROMPT_AI_VALUE
    assert kwargs['user_prompt'].startswith(prompt.split('\n')[0])
    assert ('"lead"' in kwargs['user_prompt']) == v2
    assert 'untrusted data' in kwargs['user_prompt'] and 'ignore any instructions' in kwargs['user_prompt']
    assert 'lead' not in result and result['highlights'] == ['Полезное']
    if version == 'unknown':
        assert 'falling back to v1' in caplog.text
    required = DIGEST_AI_VALUE_SCHEMA_V2.schema['required']
    assert isinstance(required, list)
    assert set(required) == {'items', 'lead', 'highlights'}


@pytest.mark.parametrize('count,label', [(1, '1 статья'), (2, '2 статьи'), (5, '5 статей')])
def test_announce_counts(count: int, label: str) -> None:
    draft, rows = data(count)
    text = build_telegram_announce(draft, rows, URL, {}, NOW)
    assert text.startswith('🤖 <b>AI-радар — 7 октября</b>\n' + label)
    assert 'Интересное:\n• Title 1' in text
    _, post, _ = build_digest_post(draft, rows, rows, {}, {}, {}, NOW)
    assert text.splitlines()[1] in post
    assert text.endswith(f'<a href="{URL}">Читать выпуск на сайте →</a>')


def test_announce_escape_and_length() -> None:
    draft, rows = data()
    draft['highlights'] = ['<script>"&', '<' * 2000, 'x' * 2000]
    text = build_telegram_announce(draft, rows, URL + '?q="&', {}, NOW)
    assert '&lt;script&gt;&quot;&amp;' in text and '<script>' not in text
    assert '?q=&quot;&amp;"' in text
    assert len(text) < 1000
    draft['highlights'] = [None, '']
    assert '• Title 1' in build_telegram_announce(draft, rows, URL, {}, NOW)


def test_render_modes(caplog: pytest.LogCaptureFixture) -> None:
    draft, rows = data()
    today = datetime.utcnow()
    expected_draft = deepcopy(draft)
    expected_draft['date_label'] = f'{today.day} {MONTHS[today.month - 1]}'
    for item in expected_draft['items']:
        item['content_type'] = 'tool_release'
    ctx = context({'telegram': 'full'}, draft, rows)
    expected = render_digest(expected_draft, 'ai_value', ctx.template_cfg, source_map=ctx.artifacts['source_map'])
    assert _ai_value_render(deepcopy(draft), rows, ctx) == expected
    ctx.template_cfg['telegram'] = 'announce'
    assert _ai_value_render(deepcopy(draft), rows, ctx) == expected
    assert 'announce fallback: no site digest' in caplog.text
    ctx.artifacts['site_digest_url'] = URL
    assert _ai_value_render(draft, rows, ctx) == (build_telegram_announce(draft, rows, URL, {}, NOW), 'HTML')


@pytest.mark.asyncio
@pytest.mark.parametrize('statuses,success', [([404, 404, 200], True), ([404, 404, 404], False)])
async def test_bot_wait_without_sleep(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, statuses: list[int], success: bool,
) -> None:
    site_wait = pytest.importorskip('bot.site_wait')
    elapsed = [0.0]

    async def sleep(seconds: float) -> None:
        elapsed[0] += seconds

    monkeypatch.setattr(site_wait.time, 'monotonic', lambda: elapsed[0])
    monkeypatch.setattr(site_wait.asyncio, 'sleep', sleep)
    with respx.mock as router:
        route = router.get(URL).mock(side_effect=[httpx.Response(code) for code in statuses])
        assert await site_wait.wait_for_site_page(URL, 45) is success
        assert route.call_count == 3
    if not success:
        assert 'wait timed out' in caplog.text


@pytest.mark.asyncio
async def test_bot_waits_only_for_parts_with_site_url(monkeypatch: pytest.MonkeyPatch) -> None:
    site_wait = pytest.importorskip('bot.site_wait')
    wait = AsyncMock(return_value=True)
    monkeypatch.setattr(site_wait, 'wait_for_site_page', wait)
    await site_wait.wait_for_parts([{'content_md': 'x'}, {'site_url': URL}], {'site': {'wait_for_page_sec': 37}})
    wait.assert_awaited_once_with(URL, 37.0)
    wait.reset_mock()
    await site_wait.wait_for_parts([{'site_url': URL}], [])
    wait.assert_awaited_once_with(URL, 300.0)


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, True])
async def test_analyzer_site_extra_never_waits(monkeypatch: pytest.MonkeyPatch, live: bool) -> None:
    from analyzer.knowledge_publisher import GitHubPublisher

    draft, rows = data()
    ctx = context({}, draft, rows)
    ctx.cfg = {'site': {'enabled': True, 'live': live, 'wait_for_page_sec': 37}}
    monkeypatch.setenv('NEURONAVT_GITHUB_TOKEN', 'fake')
    monkeypatch.setattr(GitHubPublisher, 'commit_files', AsyncMock(return_value=True))
    with respx.mock as router:
        await extras.site(rows, ctx)
        assert not router.calls
    assert ctx.artifacts['site_digest_url'] == URL
