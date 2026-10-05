"""Usage persistence must never affect completion delivery."""
from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from analyzer import llm_client
from analyzer.usage_store import install_usage_sink, usage_category
from analyzer.value_classifier import CallStats, ClassifyOutcome, ValueVerdict
from database.schema import get_db, init_db
from llm_core.transport import LLMResponse
from llm_core.usage import UsageRecord, UsageTracker
from tests.test_pipeline_characterization import _message, _setup


def test_sink_idempotent_and_listener_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = str(tmp_path / 'usage.db')
    init_db(path)
    tracker = UsageTracker()
    monkeypatch.setattr(llm_client, '_usage_tracker', tracker)
    broken = Mock(side_effect=RuntimeError('sink down'))
    tracker.add_listener(broken)
    install_usage_sink(path)
    install_usage_sink(path)
    record = UsageRecord('model', 12, 3, 15, 'complete', task='knowledge', category='articles',
                         provider='provider', cost_usd=0.02, cost_source='table')
    tracker.record(record)
    assert tracker.all() == [record]
    broken.assert_called_once_with(record)
    conn = get_db(path)
    try:
        rows = conn.execute('SELECT task, category, provider, model, prompt_tokens, completion_tokens, '
                            'cost_usd, cost_source FROM llm_usage').fetchall()
        assert [tuple(row) for row in rows] == [('knowledge', 'articles', 'provider', 'model', 12, 3, 0.02, 'table')]
    finally:
        conn.close()


def test_old_database_migration_twice(tmp_path: Path) -> None:
    path = str(tmp_path / 'old.db')
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE digests (id INTEGER PRIMARY KEY, content_md TEXT, period_start TEXT, '
                 'period_end TEXT, created_at TEXT, sent_telegram INTEGER)')
    conn.commit()
    conn.close()
    init_db(path)
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute("INSERT INTO llm_usage (task, model) VALUES ('digest', 'test')")
        assert conn.execute('SELECT created_at FROM llm_usage').fetchone()[0]
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE name='add_llm_usage'").fetchone()[0] == 1
        assert 'run_id' in {row[1] for row in conn.execute('PRAGMA table_info(digests)')}
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='idx_llm_usage_created'").fetchone()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_classifier_calls_written_once_with_category(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from analyzer import analyzer as module

    analyzer, values, _, path = _setup(tmp_path, monkeypatch)
    values['categories'] = {'articles': {'sources': ['telegram'], 'analyzer': 'ai_value', 'hooks': [],
                                         'select': 'quotas', 'template': 'ai_value', 'extras': []}}
    cast(Any, analyzer.llm).router = object()
    _message(path, 1, 'Useful article', 'raw', 8, analyzed=0)
    verdict = ValueVerdict(8, 'tutorial', 9, True, 'Useful', 'agents', 'Summary', [], False)
    calls = [CallStats(1, 20, 5, .01, 'table', 'provider', 'model', 'tool', None),
             CallStats(1, 30, 7, None, 'none', 'provider', 'model', 'text', 'invalid')]
    classifier = SimpleNamespace(calls=calls, classify=AsyncMock(return_value=[
        ClassifyOutcome('1', verdict, None, 'tool')]))
    monkeypatch.setattr(module, 'LLMValueClassifier', Mock(return_value=classifier))
    monkeypatch.setattr(llm_client, '_usage_tracker', UsageTracker())
    install_usage_sink(path)
    assert await analyzer.analyze_pending() == 1
    assert await analyzer.analyze_pending() == 0
    conn = get_db(path)
    try:
        rows = conn.execute('SELECT task, category, prompt_tokens, cost_usd FROM llm_usage ORDER BY id').fetchall()
        assert [tuple(row) for row in rows] == [('classify', 'articles', 20, .01), ('classify', 'articles', 30, None)]
    finally:
        conn.close()


def test_catalog_task_override_and_category(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = UsageTracker()
    monkeypatch.setattr(llm_client, '_usage_tracker', tracker)
    client = llm_client.LLMClient()
    token = usage_category.set('articles')
    try:
        response = cast(LLMResponse, SimpleNamespace(model='m', usage=None, provider='p', cost_usd=None,
                                                      cost_source='none'))
        client._record_catalog_usage(response, 'knowledge')
    finally:
        usage_category.reset(token)
    assert tracker.all()[0].task == 'knowledge'
    assert tracker.all()[0].category == 'articles'
    assert usage_category.get() is None


@pytest.mark.asyncio
async def test_digest_and_knowledge_context_and_membership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from analyzer.pipeline.context import DigestContext, Row
    from analyzer.pipeline.registry import EXTRAS
    from tests.test_categories import categories, message, seed_sources
    from tests.test_digests_named import digests

    analyzer, values, _, path = _setup(tmp_path, monkeypatch)
    values['categories'] = categories()
    values['digests'] = digests()
    values['categories']['articles'].update(template='classic', select='tiers', extras=['usage_test'])
    values['categories']['crypto']['extras'] = ['usage_test']
    cast(Any, analyzer.llm).router = object()
    seed_sources(path)
    message(path, 1, 1, analyzed=1, age_hours=2)
    message(path, 2, 2, analyzed=1, age_hours=2)
    tracker = UsageTracker()
    monkeypatch.setattr(llm_client, '_usage_tracker', tracker)
    install_usage_sink(path)

    async def complete(**kwargs: Any) -> str:
        tracker.record(UsageRecord('model', 10, 2, 12, 'complete', task=str(kwargs['task']),
                                   category=usage_category.get(), cost_usd=.01))
        return 'Digest text'

    async def extra(rows: list[Row], ctx: DigestContext) -> None:
        await complete(task='knowledge')

    monkeypatch.setitem(EXTRAS._items, 'usage_test', extra)
    cast(Any, analyzer.llm).complete = complete
    parts = await analyzer.run_digest('morning', hours=12)
    assert len(parts) == 2
    assert usage_category.get() is None
    assert [(r.task, r.category) for r in tracker.all()] == [
        ('digest', 'articles'), ('knowledge', 'articles'), ('digest', 'crypto'), ('knowledge', 'crypto')]
    conn = get_db(path)
    try:
        assert conn.execute('SELECT COUNT(DISTINCT run_id) FROM digests').fetchone()[0] == 1
        memberships = conn.execute('SELECT d.category, dm.message_id FROM digest_messages dm '
                                   'JOIN digests d ON d.id=dm.digest_id ORDER BY d.id').fetchall()
        assert [tuple(row) for row in memberships] == [('articles', 2), ('crypto', 1)]
    finally:
        conn.close()
    cast(Any, analyzer.llm).complete = AsyncMock(side_effect=RuntimeError('LLM down'))
    assert await analyzer.run_digest('morning', hours=12, force=True) == []
    assert usage_category.get() is None
