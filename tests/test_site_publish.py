"""Offline site publication and fallback behavior across the pipeline extras."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from analyzer.analyzer import NewsAnalyzer
from analyzer.knowledge_publisher import GitHubPublisher, SiteReview, build_site_review, publish_selected
from analyzer.pipeline.context import DigestContext
from analyzer.pipeline.extras import candidates, site
from analyzer.pipeline.writers import _ai_value_render
from database.schema import get_db, init_db
from tests.test_site_digest import doc


def seed(path: str) -> list[dict[str, Any]]:
    init_db(path)
    conn = get_db(path)
    conn.execute("INSERT INTO sources (id, type, name) VALUES (1, 'rss', 'feed')")
    for mid in (1, 2):
        conn.execute("INSERT INTO messages (id, source_id, external_id, text) VALUES (?, 1, ?, 'Article')", (mid, str(mid)))
        conn.execute("INSERT INTO analysis (message_id, value_score) VALUES (?, 8)", (mid,))
    conn.commit()
    conn.close()
    return [{'id': mid, 'text': 'Article', 'value_score': 8, 'content_type': 'tutorial',
             'source_type': 'rss', 'url': f'https://example.org/{mid}', 'collected_at': '2026-10-07'} for mid in (1, 2)]


def context(path: str, rows: list[dict[str, Any]], cfg: dict[str, Any]) -> DigestContext:
    analyzer = Mock()
    analyzer.db_path = path
    ctx = DigestContext(cast(NewsAnalyzer, analyzer), cfg, {}, 'ai_value',
                        {'candidates_list': {'enabled': True, 'min_score': 6}},
                        8, 5, datetime.now(timezone.utc), False, False)
    ctx.artifacts.update({'pool': rows, 'source_map': {str(i): row['url'] for i, row in enumerate(rows, 1)},
                          'draft': {'items': [{'source_id': str(i), 'title': 'Title', 'takeaway': 'Lesson', 'summary': 'Summary'}
                                              for i in range(1, len(rows) + 1)]}})
    return ctx


def stored(path: str) -> list[str | None]:
    conn = get_db(path)
    try:
        return [row['md_path'] for row in conn.execute('SELECT md_path FROM analysis ORDER BY message_id')]
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('live,ok', [(False, True), (True, True), (False, False), (True, False)])
async def test_one_site_commit_and_telegram_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live: bool, ok: bool,
) -> None:
    monkeypatch.setenv('GITHUB_TOKEN', 'fake-knowledge')
    monkeypatch.setenv('NEURONAVT_GITHUB_TOKEN', 'fake-site')
    path = str(tmp_path / 'db.sqlite')
    rows = seed(path)
    cfg: dict[str, Any] = {'knowledge': {'enabled': True, 'targets': ['github'], 'batch_commit': True},
                           'site': {'enabled': True, 'live': live}}
    ctx = context(path, rows, cfg)
    llm = AsyncMock()
    llm.complete_json.return_value = {'title': 'Статья', 'idea': 'Суть', 'conclusion': 'Вывод', 'tags': ['ai', 'agents']}
    legacy = AsyncMock(return_value=True)
    monkeypatch.setattr(GitHubPublisher, 'commit_files', legacy)
    prepared: list[SiteReview] = []
    github_published: set[str] = set()
    old = await publish_selected(llm, rows, cfg, None, path, site_reviews=prepared, github_published=github_published)
    assert github_published == {"1", "2"}
    assert llm.complete_json.await_count == 2 and len(prepared) == 2
    legacy.assert_awaited_once()
    previous_paths = stored(path)
    ctx.artifacts.update({'site_reviews': prepared, 'md_map': dict(old), 'site_github_published': github_published})
    commit = AsyncMock(return_value=ok)
    monkeypatch.setattr(GitHubPublisher, 'commit_files', commit)
    await site(rows, ctx)
    commit.assert_awaited_once()
    files, message = commit.call_args.args
    assert len(files) == 3 and files[-1][0].startswith('blog/src/content/posts/_digests/')
    assert message.startswith('feat(radar): AI radar digest ') and message.endswith('(+2 reviews)')
    assert all(review.path in dict(files) for review in prepared)
    if ok:
        assert ctx.artifacts['site_digest_url'].startswith('https://neuronavt.blog/posts/')
        if live:
            assert ctx.artifacts['md_map'] == {review.source_id: f'https://neuronavt.blog/reviews/{review.slug}/' for review in prepared}
            assert set(stored(path)) == {review.path for review in prepared}
        else:
            assert ctx.artifacts['md_map'] == old and stored(path) == previous_paths
    else:
        # I4.4: the post URL is deterministic and kept on failure; delivery is gated by site_status.
        assert ctx.artifacts['site_digest_url'].startswith('https://neuronavt.blog/posts/')
        assert ctx.artifacts['site_status'] == 'commit_failed'
        assert ctx.artifacts['md_map'] == old and stored(path) == previous_paths
    rendered, mode = _ai_value_render(ctx.artifacts['draft'], rows, ctx)
    assert rendered and mode == 'HTML'


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled,token', [(False, 'fake'), (True, '')])
async def test_disabled_or_missing_token_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool, token: str,
) -> None:
    monkeypatch.setenv('NEURONAVT_GITHUB_TOKEN', token)
    ctx = context(str(tmp_path / 'unused.sqlite'), [], {'site': {'enabled': enabled}})
    commit = AsyncMock()
    monkeypatch.setattr(GitHubPublisher, 'commit_files', commit)
    await site([], ctx)
    commit.assert_not_awaited()
    assert 'site_digest_url' not in ctx.artifacts


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, True])
async def test_site_only_stages_without_commit_then_persists_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live: bool,
) -> None:
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    monkeypatch.setenv('NEURONAVT_GITHUB_TOKEN', 'fake')
    path = str(tmp_path / 'db.sqlite')
    rows = seed(path)
    cfg: dict[str, Any] = {'knowledge': {'enabled': True, 'targets': []}, 'site': {'enabled': True, 'live': live}}
    llm = AsyncMock()
    llm.complete_json.return_value = {'title': 'Статья', 'idea': 'Суть', 'conclusion': 'Вывод', 'tags': ['ai', 'agents']}
    commit = AsyncMock(return_value=True)
    monkeypatch.setattr(GitHubPublisher, 'commit_files', commit)
    prepared: list[SiteReview] = []
    links = await publish_selected(llm, rows, cfg, None, path, site_reviews=prepared)
    assert not links and stored(path) == [None, None]
    commit.assert_not_awaited()
    ctx = context(path, rows, cfg)
    ctx.artifacts['site_reviews'] = prepared
    await site(rows, ctx)
    assert all(stored(path))
    assert len(ctx.artifacts['md_map']) == (2 if live else 0)
    # Stored site paths are reused even when preview did not change this run's Telegram links.
    next_links = await publish_selected(llm, rows, cfg, None, path, site_reviews=[])
    assert all(url.startswith('https://neuronavt.blog/reviews/') for url in next_links.values())
    assert llm.complete_json.await_count == 2


@pytest.mark.asyncio
async def test_reused_paths_are_not_regenerated_or_migrated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('NEURONAVT_GITHUB_TOKEN', 'fake')
    path = str(tmp_path / 'db.sqlite')
    rows = seed(path)
    old_path = 'knowledge/2026/10/old-1.md'
    site_path = 'blog/src/content/reviews/2026/10/site-2.md'
    conn = get_db(path)
    conn.executemany('UPDATE analysis SET md_path=? WHERE message_id=?', [(old_path, 1), (site_path, 2)])
    conn.commit()
    conn.close()
    llm = AsyncMock()
    prepared: list[SiteReview] = []
    existing: dict[str, str] = {}
    cfg = {'knowledge': {'enabled': True, 'targets': []}, 'site': {'enabled': True}}
    links = await publish_selected(llm, rows, cfg, None, path, site_reviews=prepared, site_existing=existing)
    assert not prepared and existing == {'2': 'site-2'}
    assert '/blob/main/knowledge/' in links['1']
    assert links['2'] == 'https://neuronavt.blog/reviews/site-2/'
    llm.complete_json.assert_not_awaited()
    ctx = context(path, rows, cfg)
    ctx.artifacts.update({'site_existing_reviews': existing, 'md_map': links})
    commit = AsyncMock(return_value=True)
    monkeypatch.setattr(GitHubPublisher, 'commit_files', commit)
    await site(rows, ctx)
    content = commit.call_args.args[0][0][1]
    assert 'review-site-2' in content and '/reviews/old-1/' not in content


@pytest.mark.asyncio
async def test_invalid_review_and_commit_exception_do_not_block_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('NEURONAVT_GITHUB_TOKEN', 'fake')
    path = str(tmp_path / 'db.sqlite')
    rows = seed(path)
    ctx = context(path, rows, {'site': {'enabled': True, 'live': True}})
    invalid = SiteReview('blog/src/content/reviews/bad.md', build_site_review(doc(source_url='file:///tmp/a'), 'day'), 'bad', 1, '1')
    ctx.artifacts.update({'site_reviews': [invalid], 'md_map': {'1': 'https://github.com/fallback'}})
    commit = AsyncMock(return_value=True)
    monkeypatch.setattr(GitHubPublisher, 'commit_files', commit)
    await site(rows, ctx)
    files = commit.call_args.args[0]
    assert len(files) == 1 and 'review-bad' not in files[0][1]
    assert ctx.artifacts['md_map']['1'] == 'https://github.com/fallback'
    commit.side_effect = RuntimeError('failure')
    await site(rows, ctx)
    assert _ai_value_render(ctx.artifacts['draft'], rows, ctx)[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(('enabled', 'live'), [(False, False), (True, False), (True, True)])
async def test_candidate_file_dropped_only_when_site_live(tmp_path: Path, enabled: bool, live: bool) -> None:
    rows = [{'id': 1, 'text': 'Title', 'value_score': 8, 'content_type': 'tutorial', 'url': 'https://example.org'}]
    ctx = context(str(tmp_path / 'unused.sqlite'), rows,
                  {'site': {'enabled': enabled, 'live': live}, 'knowledge': {'enabled': True}})
    await candidates(rows, ctx)
    assert ('extra_files' in ctx.artifacts) is not (enabled and live)
