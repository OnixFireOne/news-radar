"""Statistics windows, category isolation and exact multi-part membership."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analyzer.digest_stats import TaskUsage, compute_digest_stats, format_digest_stats, latest_digest_stats
from database.schema import get_db, init_db


def test_named_window_counts_costs_and_parts(tmp_path: Path) -> None:
    path = str(tmp_path / 'stats.db')
    init_db(path)
    conn = get_db(path)
    cfg: dict[str, Any] = {
        'digests': [{'name': 'articles', 'categories': ['articles', 'crypto']}],
        'categories': {
            'articles': {'sources': ['rss', 'hackernews'], 'analyzer': 'ai_value', 'hooks': [],
                         'select': 'quotas', 'template': 'ai_value', 'extras': [],
                         'params': {'min_value_score': 7}},
            'crypto': {'sources': ['telegram'], 'analyzer': 'crypto', 'hooks': [],
                       'select': 'tiers', 'template': 'classic', 'extras': []},
        }, 'digest_templates': {'ai_value': {'min_value_score': 5}},
    }
    try:
        conn.executemany('INSERT INTO sources (id, type, name) VALUES (?, ?, ?)',
                         [(1, 'rss', 'r'), (2, 'hackernews', 'h'), (3, 'telegram', 't'), (4, 'github', 'g')])
        conn.executemany('INSERT INTO digests (id, name, category, run_id, content_md, period_start, period_end, created_at) '
                         'VALUES (?, ?, ?, ?, ?, ?, ?, ?)', [
            (1, 'articles', 'articles', 'old', 'old', '2026-09-27 00:00:00', '2026-09-28 00:00:00', '2026-09-28 00:00:00'),
            (2, 'other', 'articles', 'other', 'other', '2026-09-28 00:00:00', '2026-09-28 05:00:00', '2026-09-28 05:00:00'),
            (3, 'articles', 'articles', 'new', 'new', '2026-09-28 01:00:00', '2026-09-28 06:09:00', '2026-09-28 06:09:00'),
            (4, 'articles', 'crypto', 'new', 'new', '2026-09-28 01:00:00', '2026-09-28 06:10:00', '2026-09-28 06:10:00'),
        ])
        # Mix SQLite timestamps and ISO timestamps, with an exact upper boundary.
        conn.executemany('INSERT INTO messages (id, source_id, external_id, text, collected_at, analyzed, is_ad, in_digest) '
                         'VALUES (?, ?, ?, ?, ?, ?, ?, ?)', [
            (1, 1, '1', 'selected', '2026-09-28 00:00:00', 1, 0, 1),
            (2, 1, '2', 'ad', '2026-09-28T02:00:00+00:00', 1, 1, 0),
            (3, 2, '3', 'pending', '2026-09-28 03:00:00', 0, 0, 0),
            (4, 3, '4', 'crypto', '2026-09-28 04:00:00', 1, 0, 1),
            (5, 1, '5', 'old selected', '2026-09-27 23:00:00', 1, 0, 1),
            (6, 4, '6', 'unrelated', '2026-09-28 02:00:00', 1, 0, 1),
            (7, 1, '7', 'upper boundary', '2026-09-28 06:10:00', 0, 0, 0),
            (8, 1, '8', 'below category threshold', '2026-09-28 02:00:00', 1, 0, 0),
        ])
        conn.executemany('INSERT INTO analysis (message_id, value_score, md_path) VALUES (?, ?, ?)',
                         [(1, 8, 'one.md'), (1, 8, 'one.md'), (2, 9, None), (4, None, None), (5, 9, 'old.md'), (8, 6, None)])
        conn.executemany('INSERT INTO digest_messages (digest_id, message_id) VALUES (?, ?)', [(1, 5), (3, 1), (4, 4)])
        conn.executemany('INSERT INTO llm_usage (created_at, task, category, model, prompt_tokens, completion_tokens, cost_usd) '
                         'VALUES (?, ?, ?, ?, ?, ?, ?)', [
            ('2026-09-28 01:00:00', 'classify', 'articles', 'm', 1000, 100, .01),
            ('2026-09-28T02:00:00+00:00', 'classify', 'articles', 'm', 2000, 200, None),
            ('2026-09-28 06:08:00', 'digest', 'articles', 'm', 500, 50, .02),
            ('2026-09-28 06:09:30', 'digest', 'crypto', 'm', 500, 50, .03),
            ('2026-09-28 03:00:00', 'knowledge', 'unrelated', 'm', 99, 99, 9),
            ('2026-09-27 23:00:00', 'digest', 'articles', 'm', 99, 99, 9),
        ])
        stats = latest_digest_stats(conn, 'articles', cfg)
        assert stats.since == datetime(2026, 9, 28, tzinfo=timezone.utc)
        assert stats.collected == {'rss': 3, 'hackernews': 1, 'telegram': 1}
        assert (stats.analyzed, stats.ads, stats.pending, stats.passed, stats.selected, stats.knowledge) == (4, 1, 1, 1, 2, 1)
        assert stats.usage == (TaskUsage('classify', 2, 3000, 300, .01, True),
                               TaskUsage('digest', 2, 1000, 100, .05, False))
        text = format_digest_stats(stats)
        for fragment in ('09:10 МСК', 'Собрано: 5', 'rss 3', 'в выпуске 2', 'md-разборов 1',
                         '3.0k/300', 'цена неизвестна', 'Итого: ≈$0.0600'):
            assert fragment in text
        assert len(text) <= 1500
        huge = replace(stats, name='n' * 500, usage=stats.usage * 100,
                       collected={str(n) * 100: 1 for n in range(100)})
        assert len(format_digest_stats(huge)) <= 1500
        assert 'Итого:' in format_digest_stats(huge)
        # The first run starts at its own period_start; historical membership is unknown.
        conn.execute('DELETE FROM digest_messages WHERE digest_id=1')
        conn.execute('DELETE FROM digests WHERE id IN (1, 2)')
        assert latest_digest_stats(conn, 'articles', cfg).since == datetime(2026, 9, 28, 1, tzinfo=timezone.utc)
        conn.execute('UPDATE digests SET run_id=NULL WHERE id=4')
        assert latest_digest_stats(conn, 'articles', cfg).selected is None
    finally:
        conn.close()


def test_total_spend_covers_all_recorded_usage(tmp_path: Path) -> None:
    path = str(tmp_path / 'total.db')
    init_db(path)
    conn = get_db(path)
    try:
        conn.executemany('INSERT INTO llm_usage (created_at, task, category, model, prompt_tokens, completion_tokens, cost_usd) '
                         'VALUES (?, ?, ?, ?, ?, ?, ?)', [
            ('2026-09-01 10:00:00', 'classify', 'articles', 'm', 1, 1, .5),
            ('2026-09-28T02:00:00+00:00', 'digest', 'crypto', 'm', 1, 1, .25),
            ('2026-09-28 03:00:00', 'knowledge', None, 'm', 1, 1, .125),
        ])
        stats = compute_digest_stats(conn, 'articles', datetime(2026, 9, 28, tzinfo=timezone.utc),
                                     datetime(2026, 9, 29, tzinfo=timezone.utc), ['articles'], ['rss'])
        assert stats.usage == ()  # the window has no articles usage
        assert (stats.total_cost_usd, stats.total_unknown_cost) == (.875, False)
        assert 'Всего с 01.09.2026: $0.8750' in format_digest_stats(stats)
    finally:
        conn.close()


def test_usage_lists_models_per_task(tmp_path: Path) -> None:
    path = str(tmp_path / 'models.db')
    init_db(path)
    conn = get_db(path)
    try:
        conn.executemany('INSERT INTO llm_usage (created_at, task, category, model, prompt_tokens, completion_tokens, cost_usd) '
                         'VALUES (?, ?, ?, ?, ?, ?, ?)', [
            ('2026-09-28T02:00:00+00:00', 'classify', 'articles', 'gpt-6-luna', 1000, 100, .001),
            ('2026-09-28T03:00:00+00:00', 'classify', 'articles', 'gpt-6-luna', 1000, 100, .001),
            ('2026-09-28T04:00:00+00:00', 'digest', 'articles', 'gpt-6-sol', 3000, 900, .02),
            ('2026-09-28T05:00:00+00:00', 'digest', 'articles', 'claude-opus-5-5', 3000, 900, .05),
        ])
        stats = compute_digest_stats(conn, 'articles', datetime(2026, 9, 28, tzinfo=timezone.utc),
                                     datetime(2026, 9, 29, tzinfo=timezone.utc), ['articles'], ['rss'])
        assert [u.models for u in stats.usage] == [('gpt-6-luna',), ('claude-opus-5-5', 'gpt-6-sol')]
        text = format_digest_stats(stats)
        assert '• classify (gpt-6-luna): 2 выз.' in text
        assert '• digest (claude-opus-5-5, gpt-6-sol): 2 выз.' in text
    finally:
        conn.close()
