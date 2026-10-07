"""Astro digest posts built from the same draft as the Telegram digest."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from html import escape
import json
import re
from typing import Any

from analyzer.knowledge_publisher import http_url, site_digest_slug

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря")
# Section labels are plural and deliberately independent of singular Telegram badges.
SECTIONS = (
    ("Инструменты", ("tool_release",), ("инструмент", "инструмента", "инструментов")),
    ("Практика", ("practical_case", "tutorial"), ("практика", "практики", "практик")),
    ("Исследования", ("research",), ("исследование", "исследования", "исследований")),
    ("Мнения", ("opinion",), ("мнение", "мнения", "мнений")),
    ("Новости", ("hype_news",), ("новость", "новости", "новостей")),
    ("Другое", ("other",), ("прочая статья", "прочие статьи", "прочих статей")),
)


def counted(number: int, forms: tuple[str, str, str]) -> str:
    form = 2 if 11 <= number % 100 <= 14 else (0 if number % 10 == 1 else 1 if 2 <= number % 10 <= 4 else 2)
    return f"{number} {forms[form]}"


def _escaped(value: object) -> str:
    return escape(str(value or ""), quote=True)


def _score(row: Mapping[str, Any]) -> float:
    return float(row.get("value_score") or 0)


def _source_link(url: object, title: str, css: str = "") -> str:
    safe_url = http_url(url)
    if not safe_url:
        return _escaped(title)
    attr = f' class="{css}"' if css else ""
    return f'<a{attr} href="{_escaped(safe_url)}" target="_blank" rel="noopener">{_escaped(title)}</a>'


def build_digest_post(
    draft: Mapping[str, Any], selected: Sequence[Mapping[str, Any]], pool: Sequence[Mapping[str, Any]],
    reviews: Mapping[str, str], template_cfg: Mapping[str, Any], site_cfg: Mapping[str, Any], now: datetime,
) -> tuple[str, str, str]:
    """Review keys are draft source IDs (1-based selected indexes), values are committed/staged slugs."""
    # Import lazily to keep candidate-title ownership in extras without a module cycle.
    from analyzer.pipeline.extras import candidate_title

    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    slug = site_digest_slug(site_cfg, now)
    by_source = {str(i): row for i, row in enumerate(selected, 1)}
    items: list[tuple[Mapping[str, Any], Mapping[str, Any], str]] = []
    seen: set[str] = set()
    for item in draft.get("items", []):
        if not isinstance(item, Mapping):
            continue
        source_id = str(item.get("source_id", ""))
        if source_id in by_source and source_id not in seen:
            seen.add(source_id)
            items.append((item, by_source[source_id], source_id))
    known = {kind for _, kinds, _ in SECTIONS for kind in kinds}

    def kind(row: Mapping[str, Any]) -> str:
        value = str(row.get("content_type") or "other")
        return value if value in known else "other"

    counts = [sum(kind(row) in kinds for _, row, _ in items) for _, kinds, _ in SECTIONS]
    breakdown = [counted(n, forms) for n, (_, _, forms) in zip(counts, SECTIONS) if n]
    count_text = counted(len(items), ("статья", "статьи", "статей"))
    description = count_text + (": " + ", ".join(breakdown) if breakdown else "")
    fields = {"title": f"AI-радар — {now.day} {MONTHS[now.month - 1]}",
              "description": description, "tags": ["дайджест", "ai-радар"]}
    header = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items())
    stamp = (now - timedelta(minutes=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    threshold = float(template_cfg.get("min_value_score", 5))
    total = sum(row.get("value_score") is not None and _score(row) >= threshold for row in pool)
    meta = f"Отобрано {len(selected)} из {total}" + (" · " + " · ".join(breakdown) if breakdown else "")
    blocks = [f'<div class="radar-meta">{_escaped(meta)}</div>']
    lead = draft.get("lead")
    if isinstance(lead, str) and lead.strip():
        blocks.append('<div class="radar-lead">\n<p class="radar-lead-title">Главное за день</p>\n'
                      f'<p>{_escaped(lead.strip())}</p>\n</div>')
    for heading, kinds, _ in SECTIONS:
        group = [(item, row, sid) for item, row, sid in items if kind(row) in kinds]
        if not group:
            continue
        blocks.append(f'<h2 class="radar-section">{heading}</h2>')
        for item, row, source_id in group:
            review = reviews.get(source_id, "")
            if not re.fullmatch(r"[a-z0-9-]+", review):
                review = ""
            style = f' style="view-transition-name: review-{_escaped(review)}"' if review else ""
            score = _score(row)
            high = ' data-high="true"' if score >= 8 else ""
            links = []
            if review:
                links.append(f'<a class="radar-btn" href="/reviews/{_escaped(review)}/">Разбор</a>')
            if http_url(row.get("url")):
                links.append(_source_link(row.get("url"), "Источник", "radar-btn radar-btn-ext"))
            blocks.append(
                '<article class="radar-card">\n<div class="radar-card-head">\n'
                f'<h3 class="radar-card-title"{style}>{_escaped(item.get("title"))}</h3>\n'
                f'<span class="radar-score"{high}>{score:g}/10</span>\n</div>\n'
                f'<p class="radar-takeaway">{_escaped(item.get("takeaway"))}</p>\n'
                f'<p class="radar-summary">{_escaped(item.get("summary"))}</p>\n'
                '<div class="radar-links">\n' + "\n".join(links) + '\n</div>\n</article>')
    options = template_cfg.get("candidates_list", {})
    min_score = float(options.get("min_score", 6))
    candidates = sorted((row for row in pool if _score(row) >= min_score), key=_score, reverse=True)
    lines = ['<details class="radar-candidates">', f'<summary>Все кандидаты выпуска ({len(candidates)})</summary>', '<ol>']
    for row in candidates:
        score = _score(row)
        high = ' data-high="true"' if score >= 8 else ""
        lines.append(f'<li>{_source_link(row.get("url"), candidate_title(row))} '
                     f'<span class="radar-score"{high}>{score:g}/10</span></li>')
    blocks.append("\n".join([*lines, '</ol>', '</details>']))
    path = str(site_cfg.get("posts_dir", "blog/src/content/posts/_digests")).strip("/") + f"/{slug}.md"
    url = str(site_cfg.get("base_url", "https://neuronavt.blog")).rstrip("/") + f"/posts/{slug}/"
    return path, f"---\n{header}\npubDatetime: {stamp}\n---\n\n" + "\n\n".join(blocks) + "\n", url
