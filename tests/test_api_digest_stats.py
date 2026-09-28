"""Optional digest statistics must not break successful generation."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from analyzer.pipeline.categories import DigestPart
from database.schema import get_db, init_db


@unittest.skipUnless(importlib.util.find_spec('api'), 'Run in the API image')
class ApiDigestStatsTests(unittest.IsolatedAsyncioTestCase):
    async def test_flag_failure_and_on_demand(self) -> None:
        api: Any = importlib.import_module('api.main')
        config_module: Any = importlib.import_module('config.config_watcher')
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'api.db')
            init_db(path)
            conn = get_db(path)
            conn.execute("INSERT INTO digests (id, name, category, run_id, content_md, period_start, period_end, created_at) "
                         "VALUES (1, 'articles', 'articles', 'run', 'text', '2026-09-28 00:00:00', "
                         "'2026-09-28 06:10:00', '2026-09-28 06:10:00')")
            conn.commit()
            conn.close()
            values: dict[str, Any] = {'digests': [{'name': 'articles', 'categories': ['articles']},
                                                 {'name': 'empty', 'categories': ['articles']}],
                                     'digest_stats': {'enabled': True},
                                     'categories': {'articles': {'sources': ['rss'], 'analyzer': 'ai_value',
                                         'select': 'quotas', 'template': 'ai_value', 'hooks': [], 'extras': []}}}
            cfg = Mock()
            cfg.get.side_effect = lambda key, default=None: values.get(key, default)
            analyzer = Mock(cfg=cfg, run_digest=AsyncMock(return_value=[DigestPart('articles', 'text', 1)]))
            with patch.object(api, 'DB_PATH', path), patch.object(api, 'build_llm_client', Mock()), \
                 patch.object(api, 'NewsAnalyzer', Mock(return_value=analyzer)), \
                 patch.object(config_module, 'ConfigWatcher', Mock(return_value=cfg)):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://test') as client:
                    response = await client.post('/digest/generate?name=articles')
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('stats', response.json())
                    self.assertEqual((await client.get('/digest/stats?name=articles')).json()['text'], response.json()['stats'])
                    values['digest_stats']['enabled'] = False
                    self.assertNotIn('stats', (await client.post('/digest/generate?name=articles')).json())
                    self.assertEqual((await client.get('/digest/stats?name=articles')).status_code, 200)
                    self.assertEqual((await client.get('/digest/stats?name=missing')).status_code, 404)
                    self.assertEqual((await client.get('/digest/stats?name=empty')).status_code, 404)
                    values['digest_stats']['enabled'] = True
                    with patch.object(api, 'latest_digest_stats', side_effect=RuntimeError('stats down')):
                        response = await client.post('/digest/generate?name=articles')
                    self.assertEqual(response.status_code, 200)
                    self.assertNotIn('stats', response.json())
                    analyzer.run_digest.return_value = [DigestPart('articles', 'dispatched', None)]
                    self.assertNotIn('stats', (await client.post('/digest/generate?name=articles')).json())
