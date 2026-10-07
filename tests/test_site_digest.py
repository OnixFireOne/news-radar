"""Offline contracts for Astro frontmatter and the radar HTML."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from analyzer.knowledge_publisher import (
    KnowledgeDoc, build_site_review, generate_doc, short_description, validate_site_frontmatter,
)
from analyzer.site_digest import build_digest_post, counted

NOW = datetime(2026, 10, 7, 6, 10, tzinfo=timezone.utc)


def doc(**kwargs: Any) -> KnowledgeDoc:
    return replace(KnowledgeDoc(7, 'Заголовок', 'https://example.org/article', 'rss', '2026-10-07',
                               'tutorial', 8, 'ai', ['ai', 'agents'], 'Идея', 'Вывод',
                               body='## Коротко\nСуть\n\n## Главное\n- Один\n- Два\n- Три',
                               description='Суть'), **kwargs)


def frontmatter(content: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for line in content.split('\n---\n', 1)[0][4:].splitlines():
        key, value = line.split(': ', 1)
        fields[key] = datetime.fromisoformat(value.replace('Z', '+00:00')) if key == 'pubDatetime' else json.loads(value)
    return fields


def test_site_review_yaml_date_type_body_and_description() -> None:
    content = build_site_review(doc(content_type='unknown', body='# Heading\n\n## Коротко\nСуть'), '2026-10-07-ai-radar')
    fields = frontmatter(content)
    assert isinstance(fields['pubDatetime'], datetime)
    assert '\npubDatetime: "' not in content
    assert fields['content_type'] == 'other'
    assert fields['description'] == 'Суть'
    assert '# Heading' not in content
    assert '## Коротко\nСуть' in content
    assert validate_site_frontmatter(content)
    description = short_description('слово ' * 80)
    assert len(description) <= 280 and '\n' not in description and description.endswith('слово')


@pytest.mark.parametrize('changes', [
    {'source_url': 'javascript:alert(1)'}, {'source_url': 'https://'}, {'value_score': 11},
    {'value_score': -1}, {'value_score': float('nan')}, {'title': ''}, {'source_type': ''},
])
def test_invalid_review_is_rejected(changes: dict[str, Any]) -> None:
    assert not validate_site_frontmatter(build_site_review(doc(**changes), '2026-10-07-ai-radar'))


def test_quoted_or_invalid_timestamp_is_rejected() -> None:
    content = build_site_review(doc(), '2026-10-07-ai-radar')
    stamp = next(line.split(': ', 1)[1] for line in content.splitlines() if line.startswith('pubDatetime:'))
    assert not validate_site_frontmatter(content.replace(stamp, json.dumps(stamp)))
    assert not validate_site_frontmatter(content.replace(stamp, '2026-99-07T06:10:00.000Z'))
    assert not validate_site_frontmatter('---\ntitle: "A"\n---\n')


@pytest.mark.asyncio
@pytest.mark.parametrize('full', [True, False])
async def test_description_comes_from_tldr_or_idea(full: bool) -> None:
    llm = AsyncMock()
    llm.complete_json.return_value = {
        'title': 'Название', 'tldr': 'Коротко\nв двух словах', 'key_points': ['a', 'b', 'c'],
        'idea': 'Идея\nв двух словах', 'conclusion': 'Вывод', 'tags': ['ai', 'agents'],
    }
    result = await generate_doc(llm, {'id': 1, 'text': 'Article', 'value_score': 8}, {'format': 'full' if full else 'brief'})
    assert result is not None
    assert result.description == ('Коротко в двух словах' if full else 'Идея в двух словах')


def rows_and_draft() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [{'id': i, 'content_type': kind, 'value_score': 8 if i == 2 else 7,
             'url': f'https://example.org/{i}?q="x"', 'text': f'Кандидат {i}\nMore'}
            for i, kind in enumerate(('research', 'tool_release', 'tutorial', 'opinion', 'hype_news', 'alien'), 1)]
    draft = {'items': [{'source_id': str(i), 'title': '<script>"title"</script>',
                        'summary': '<script>"summary"</script>', 'takeaway': 'A & B'} for i in range(1, 7)]}
    return rows, draft


def test_digest_contract_escaping_order_and_repeated_date() -> None:
    rows, draft = rows_and_draft()
    pool = rows + [{'id': 9, 'text': '<script>"candidate"</script>', 'url': 'javascript:alert(1)', 'value_score': 5}]
    path, content, url = build_digest_post(draft, rows, pool, {'2': '2026-10-07-tool-2'}, {}, {}, NOW)
    assert path == 'blog/src/content/posts/_digests/2026-10-07-ai-radar.md'
    assert url == 'https://neuronavt.blog/posts/2026-10-07-ai-radar/'
    fields = frontmatter(content)
    assert fields['title'] == 'AI-радар — 7 октября'
    assert fields['pubDatetime'] == datetime(2026, 10, 7, 6, 9, tzinfo=timezone.utc)
    assert 'radar-lead' not in content
    assert '<script>' not in content and '&lt;script&gt;&quot;summary&quot;' in content
    assert 'q=&quot;x&quot;' in content
    assert content.count('view-transition-name:') == 1
    assert 'href="/reviews/2026-10-07-tool-2/"' in content
    assert content.count('data-high="true"') == 2  # One card and the same candidate.
    headings = [content.index(f'class="radar-section">{heading}') for heading in
                ('Инструменты', 'Практика', 'Исследования', 'Мнения', 'Новости', 'Другое')]
    assert headings == sorted(headings)
    assert 'radar-meta">Отобрано 6 из 7' in content
    candidates = content.split('<details', 1)[1]
    assert candidates.index('Кандидат 2') < candidates.index('Кандидат 1')
    assert '&quot;candidate&quot;' not in candidates and 'javascript:' not in candidates
    assert 'target="_blank" rel="noopener"' in content
    assert build_digest_post(draft, rows, pool, {}, {}, {}, NOW.replace(hour=20))[0] == path
    draft['lead'] = '<script>Lead</script>'
    assert 'class="radar-lead"' in build_digest_post(draft, rows, pool, {}, {}, {}, NOW)[1]


@pytest.mark.parametrize('n,expected', [(1, '1 статья'), (2, '2 статьи'), (5, '5 статей'),
                                      (11, '11 статей'), (21, '21 статья'), (22, '22 статьи')])
def test_russian_declensions(n: int, expected: str) -> None:
    assert counted(n, ('статья', 'статьи', 'статей')) == expected


def test_description_breakdown_and_export_for_blog(tmp_path: Path) -> None:
    kinds = ['tool_release'] * 3 + ['tutorial'] * 3 + ['research'] * 2
    rows = [{'id': i, 'content_type': kind, 'value_score': 8, 'url': 'https://example.org', 'text': 'Кандидат'}
            for i, kind in enumerate(kinds, 1)]
    draft = {'items': [{'source_id': str(i), 'title': 'Статья', 'summary': 'Резюме', 'takeaway': 'Вывод'}
                       for i in range(1, 9)]}
    path, content, _ = build_digest_post(draft, rows, rows, {'1': '2026-10-07-review-7'}, {}, {}, NOW)
    assert frontmatter(content)['description'] == '8 статей: 3 инструмента, 3 практики, 2 исследования'
    files = [(path, content), ('blog/src/content/reviews/2026/10/2026-10-07-review-7.md',
                             build_site_review(doc(), '2026-10-07-ai-radar'))]
    for name, body in files:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding='utf-8')


def test_site_review_body_cannot_inject_html() -> None:
    from analyzer.knowledge_publisher import KnowledgeDoc, build_site_review

    doc = KnowledgeDoc(
        message_id=7, title='T', source_url='https://example.org', source_type='rss', date='2026-10-07',
        content_type='research', value_score=7.0, topic='ai', tags=['ai', 'llm'], idea='i', conclusion='c',
        body='## Коротко\n<script>alert(1)</script> and <img src=x onerror=1>\n\nCode `a<b` and\n```\nif a<b:\n```',
        description='d',
    )
    body = build_site_review(doc, '2026-10-07-ai-radar').split('\n---\n', 1)[1]
    assert '<script>' not in body and '<img' not in body
    assert '&lt;script>' in body
    assert '`a<b`' in body and 'if a<b:' in body
