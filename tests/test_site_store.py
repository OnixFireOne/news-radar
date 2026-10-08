"""Offline persistence and retry contracts for site publication."""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

import analyzer.analyzer as module
from analyzer.knowledge_publisher import GitHubPublisher
from analyzer.site_store import SiteFile, attach_digest, mark_committed, review_for_message, save_files
from config.config_watcher import DEFAULT_CONFIG
from database.schema import get_db, init_db


def test_schema_and_upsert(tmp_path: Path) -> None:
    path = str(tmp_path / 'store.db')
    init_db(path)
    init_db(path)
    conn = get_db(path)
    try:
        assert {'site_url', 'site_status'} <= {row[1] for row in conn.execute('PRAGMA table_info(digests)')}
        assert conn.execute('SELECT * FROM digest_deliveries').fetchall() == []
        conn.execute("INSERT INTO sources (id, type, name) VALUES (1, 'rss', 'feed')")
        conn.execute("INSERT INTO messages (id, source_id, external_id, text) VALUES (1, 1, '1', 'Article')")
        ids = save_files(conn, [SiteFile('review.md', 'body', 'review', 'https://example.org/review', 1),
                                SiteFile('post.md', 'old', 'digest')])
        mark_committed(conn, ids, 'sha-one')
        assert review_for_message(conn, 1) is not None
        review = review_for_message(conn, 1)
        assert review is not None and review.committed and review.content == 'body'
        assert review_for_message(conn, 2) is None
        assert save_files(conn, [SiteFile('post.md', 'old', 'digest')]) == [ids[1]]
        assert conn.execute('SELECT commit_sha FROM site_files WHERE id=?', (ids[1],)).fetchone()[0] == 'sha-one'
        assert save_files(conn, [SiteFile('post.md', 'new', 'digest')]) == [ids[1]]
        post = conn.execute('SELECT * FROM site_files WHERE id=?', (ids[1],)).fetchone()
        assert post['content'] == 'new' and post['committed_at'] is None and post['commit_sha'] is None
        conn.execute("INSERT INTO digests (id, content_md, period_start, period_end) VALUES (1, 'x', '2026-10-08', '2026-10-08')")
        attach_digest(conn, ids, 1, 'https://example.org/post', 'commit_failed')
        assert {row[0] for row in conn.execute('SELECT digest_id FROM site_files')} == {1}
        assert conn.execute('SELECT site_status FROM digests').fetchone()[0] == 'commit_failed'
    finally:
        conn.close()


def analyzer_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                     enabled: bool = True) -> tuple[module.NewsAnalyzer, AsyncMock, str]:
    path = str(tmp_path / 'site.db')
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute("INSERT INTO sources (id, type, name) VALUES (1, 'rss', 'feed')")
        conn.execute("INSERT INTO messages (id, source_id, external_id, text, url, analyzed, collected_at) "
                     "VALUES (1, 1, '1', 'Article one', 'https://example.org/1', 1, ?)",
                     (datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),))
        conn.execute("INSERT INTO analysis (message_id, temperature, content_type, value_score, topic, takeaway) "
                     "VALUES (1, 5, 'tool_release', 8, 'agents', 'Вывод')")
        conn.commit()
    finally:
        conn.close()
    values: dict[str, Any] = deepcopy(DEFAULT_CONFIG)
    values['categories'] = {'articles': {'enabled': True, 'sources': ['rss'], 'analyzer': 'ai_value', 'hooks': [],
                                        'select': 'quotas', 'template': 'ai_value', 'extras': ['knowledge', 'site']}}
    values['digests'] = [{'name': 'articles', 'enabled': True, 'categories': ['articles'], 'at': ['09:10'],
                         'tz': 'Europe/Moscow'}]
    values['knowledge'].update(enabled=True, targets=[], format='brief')
    values['digest_templates']['ai_value']['telegram'] = 'announce'
    values['site'].update(enabled=enabled, live=True, branch='main')
    cfg = Mock()
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    cfg.load_topics.return_value = {}
    monkeypatch.setattr(module, 'ChromaClient', Mock())
    monkeypatch.setattr(module, 'get_embedder', Mock())
    monkeypatch.setattr(module, 'LLMLock', nullcontext)
    llm = AsyncMock()
    llm.router = Mock()
    llm.complete_json.return_value = {'items': [{'source_id': '1', 'title': 'Инструмент', 'takeaway': 'Вывод',
                                                'summary': 'Суть'}], 'lead': 'Главное', 'highlights': ['Пункт'],
                                     'title': 'Инструмент', 'idea': 'Суть', 'conclusion': 'Вывод', 'tags': ['ai', 'agents']}
    return module.NewsAnalyzer(path, llm, cfg=cfg), llm, path


@pytest.mark.asyncio
async def test_failed_commit_reuses_review_then_committed_review_is_not_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('NEURONAVT_GITHUB_TOKEN', 'fake')
    analyzer, llm, path = analyzer_fixture(tmp_path, monkeypatch)
    calls: list[list[tuple[str, str]]] = []
    succeed = False

    async def commit(self: GitHubPublisher, files: list[tuple[str, str]], message: str) -> bool:
        calls.append(files)
        if succeed:
            self.last_commit_sha = 'created-sha'
        return succeed

    monkeypatch.setattr(GitHubPublisher, 'commit_files', commit)
    parts = await analyzer.run_digest('articles', 24)
    assert len(parts) == 1 and parts[0].site_status == 'commit_failed' and parts[0].site_url
    assert 'Читать выпуск на сайте' in parts[0].result
    assert llm.complete_json.await_count == 2
    conn = get_db(path)
    try:
        files = conn.execute('SELECT * FROM site_files').fetchall()
        assert len(files) == 2 and {row['kind'] for row in files} == {'review', 'digest'}
        assert all(row['committed_at'] is None and row['digest_id'] == parts[0].digest_id for row in files)
        digest = conn.execute('SELECT * FROM digests WHERE id=?', (parts[0].digest_id,)).fetchone()
        assert digest['site_status'] == 'commit_failed' and digest['site_url'] == parts[0].site_url
    finally:
        conn.close()
    succeed = True
    parts = await analyzer.run_digest('articles', 24, force=True)
    assert parts[0].site_status == 'ok' and llm.complete_json.await_count == 3
    assert len(calls[-1]) == 2 and calls[-1][0] == calls[0][0]
    conn = get_db(path)
    try:
        files = conn.execute('SELECT * FROM site_files').fetchall()
        assert len(files) == 2
        assert all(row['committed_at'] and row['commit_sha'] == 'created-sha' for row in files)
    finally:
        conn.close()
    await analyzer.run_digest('articles', 24, force=True)
    assert llm.complete_json.await_count == 4 and len(calls[-1]) == 1
    assert '/reviews/' in calls[-1][0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled,status', [(True, 'skipped'), (False, None)])
async def test_without_token_or_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                     enabled: bool, status: str | None) -> None:
    monkeypatch.delenv('NEURONAVT_GITHUB_TOKEN', raising=False)
    analyzer, llm, path = analyzer_fixture(tmp_path, monkeypatch, enabled)
    commit = AsyncMock()
    monkeypatch.setattr(GitHubPublisher, 'commit_files', commit)
    parts = await analyzer.run_digest('articles', 24)
    assert parts[0].site_status == status and parts[0].site_url is None
    assert llm.complete_json.await_count == 1
    commit.assert_not_awaited()
    conn = get_db(path)
    try:
        assert conn.execute('SELECT site_status FROM digests').fetchone()[0] == status
        assert conn.execute('SELECT count(*) FROM site_files').fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_commit_sha_is_exposed_only_after_ref_update() -> None:
    import httpx
    from tests.test_knowledge_batch import github

    transport, _ = github(moves=1)
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = GitHubPublisher('owner/repo', 'main', 'fake-token', client=client)
        assert await publisher.commit_files([('post.md', 'content')], 'msg')
        assert publisher.last_commit_sha == 'new-commit'
        assert await publisher.commit_files([], 'empty')
        assert publisher.last_commit_sha is None
    transport, _ = github(fail_tree=True)
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = GitHubPublisher('owner/repo', 'main', 'fake-token', client=client)
        assert not await publisher.commit_files([('post.md', 'content')], 'msg')
        assert publisher.last_commit_sha is None


@pytest.mark.asyncio
async def test_commit_exception_preserves_files_and_announcement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('NEURONAVT_GITHUB_TOKEN', 'fake')
    analyzer, _, path = analyzer_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(GitHubPublisher, 'commit_files', AsyncMock(side_effect=RuntimeError('offline')))
    parts = await analyzer.run_digest('articles', 24)
    assert parts[0].site_status == 'commit_failed' and parts[0].site_url
    assert 'Читать выпуск на сайте' in parts[0].result
    conn = get_db(path)
    try:
        files = conn.execute('SELECT * FROM site_files').fetchall()
        assert len(files) == 2 and all(row['committed_at'] is None for row in files)
    finally:
        conn.close()
