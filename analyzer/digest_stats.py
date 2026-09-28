"""Digest accounting over UTC windows, with explicit membership of stored parts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from analyzer.pipeline.categories import load_categories, resolve_digest
from analyzer.value_funnel import _number


@dataclass(frozen=True)
class TaskUsage:
    task: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    unknown_cost: bool


@dataclass(frozen=True)
class DigestStats:
    name: str
    since: datetime
    until: datetime
    collected: dict[str, int]
    analyzed: int
    ads: int
    pending: int
    passed: int | None
    selected: int | None
    knowledge: int | None
    usage: tuple[TaskUsage, ...]


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def compute_digest_stats(
    conn: sqlite3.Connection, name: str, since: datetime, until: datetime,
    categories: Sequence[str], sources: Sequence[str], *,
    part_ids: Sequence[int] = (), thresholds: Mapping[str, float] | None = None,
    membership_known: bool = True,
) -> DigestStats:
    """Count the collected cohort in [since, until); selection uses exact part IDs.

    Analysis/ad/pending fields describe the cohort's current state, not transitions.
    NULL-category usage is deliberately excluded from named-category totals.
    """
    source_slots = ','.join('?' for _ in sources)
    rows = conn.execute(
        "SELECT m.id, s.type, m.analyzed, m.is_ad, "
        "(SELECT MAX(value_score) FROM analysis WHERE message_id=m.id) "
        "FROM messages m JOIN sources s ON s.id=m.source_id "
        f"WHERE s.type IN ({source_slots}) "
        "AND julianday(m.collected_at)>=julianday(?) AND julianday(m.collected_at)<julianday(?)",
        (*sources, since.isoformat(), until.isoformat()),
    ).fetchall()
    collected: dict[str, int] = {}
    analyzed = ads = pending = passed = 0
    for row in rows:
        source = str(row[1])
        collected[source] = collected.get(source, 0) + 1
        analyzed += int(row[2] == 1)
        ads += int(row[3] == 1)
        pending += int(row[2] == 0)
        if thresholds and source in thresholds and row[4] is not None:
            passed += int(float(row[4]) >= thresholds[source] and row[3] != 1)
    selected = knowledge = 0
    if part_ids:
        slots = ','.join('?' for _ in part_ids)
        selected, knowledge = conn.execute(
            "SELECT COUNT(DISTINCT m.id), COUNT(DISTINCT CASE WHEN EXISTS "
            "(SELECT 1 FROM analysis a WHERE a.message_id=m.id AND a.md_path IS NOT NULL) "
            "THEN m.id END) FROM digest_messages dm JOIN messages m ON m.id=dm.message_id "
            f"WHERE dm.digest_id IN ({slots}) AND m.in_digest=1", tuple(part_ids),
        ).fetchone()
    category_slots = ','.join('?' for _ in categories)
    usage_rows = conn.execute(
        "SELECT COALESCE(task, 'default'), COUNT(*), COALESCE(SUM(prompt_tokens), 0), "
        "COALESCE(SUM(completion_tokens), 0), COALESCE(SUM(cost_usd), 0), "
        "MAX(cost_usd IS NULL) FROM llm_usage "
        f"WHERE category IN ({category_slots}) AND julianday(created_at)>=julianday(?) "
        "AND julianday(created_at)<julianday(?) GROUP BY COALESCE(task, 'default') ORDER BY 1",
        (*categories, since.isoformat(), until.isoformat()),
    ).fetchall()
    usage = tuple(TaskUsage(str(r[0]), int(r[1]), int(r[2]), int(r[3]), float(r[4]), bool(r[5]))
                  for r in usage_rows)
    return DigestStats(name, since, until, collected, analyzed, ads, pending,
                       passed if thresholds else None, selected if membership_known else None,
                       knowledge if membership_known else None, usage)


def latest_digest_stats(conn: sqlite3.Connection, name: str | None, cfg: Mapping[str, Any],
                        *, digest_id: int | None = None) -> DigestStats:
    """Resolve the last run, keeping multi-category parts out of the previous-run window."""
    spec = resolve_digest(cfg, name)
    if spec is None:
        raise LookupError("No configured digest")
    latest = conn.execute(
        "SELECT * FROM digests WHERE name=?" + (" AND id=?" if digest_id is not None else "")
        + " ORDER BY id DESC LIMIT 1", (spec.name, digest_id) if digest_id is not None else (spec.name,),
    ).fetchone()
    if latest is None:
        raise LookupError("No digests generated yet")
    if latest['run_id']:
        parts = conn.execute("SELECT * FROM digests WHERE name=? AND run_id=? ORDER BY id",
                             (spec.name, latest['run_id'])).fetchall()
    else:
        # Historical rows have no reliable run/membership attribution.
        parts = [latest]
    previous = conn.execute(
        "SELECT created_at FROM digests WHERE name=? AND id<? ORDER BY id DESC LIMIT 1",
        (spec.name, parts[0]['id']),
    ).fetchone()
    since = _utc(previous[0] if previous else parts[0]['period_start'])
    until = max(_utc(p['created_at']) for p in parts)
    categories = [cat for cat in load_categories(cfg) if cat.name in spec.categories]
    sources = tuple(dict.fromkeys(source for cat in categories for source in cat.sources))
    thresholds: dict[str, float] = {}
    for cat in categories:
        if cat.select == 'quotas':
            template = {**cfg.get('digest_templates', {}).get(cat.template, {}), **cat.params}
            for source in cat.sources:
                thresholds[source] = _number(template.get('min_value_score'), 5)
    return compute_digest_stats(conn, spec.name, since, until, [cat.name for cat in categories], sources,
                                part_ids=[int(p['id']) for p in parts], thresholds=thresholds,
                                membership_known=bool(latest['run_id']))


def format_digest_stats(stats: DigestStats) -> str:
    def count(value: int | None) -> str:
        return str(value) if value is not None else 'н/д'

    def tokens(value: int) -> str:
        return f'{value / 1000:.1f}k' if value >= 1000 else str(value)

    def cost(value: float, unknown: bool) -> str:
        return ('≈' if unknown else '') + f'${value:.4f}' + (' (цена неизвестна)' if unknown else '')

    stamp = stats.until.astimezone(ZoneInfo('Europe/Moscow'))
    hours = max(0, (stats.until - stats.since).total_seconds() / 3600)
    sources = ', '.join(f'{key[:25]} {value}' for key, value in sorted(stats.collected.items()))
    tasks = ' · '.join(
        f'{u.task[:24]} {u.calls} выз. {tokens(u.prompt_tokens)}/{tokens(u.completion_tokens)} ток. '
        f'{cost(u.cost_usd, u.unknown_cost)}' for u in stats.usage
    ) or 'нет вызовов'
    total = cost(sum(u.cost_usd for u in stats.usage), any(u.unknown_cost for u in stats.usage))
    lines = [
        f'📊 Дайджест «{stats.name[:80]}» · {stamp:%d.%m %H:%M} МСК (за {hours:.1f} ч)',
        f'Собрано: {sum(stats.collected.values())} ({sources[:200]}) · разобрано {stats.analyzed} · '
        f'реклама {stats.ads} · в очереди {stats.pending}',
        f'Прошло порог: {count(stats.passed)} · в выпуске {count(stats.selected)} · md-разборов {count(stats.knowledge)}',
        f'LLM: {tasks[:800]}', f'Итого: {total}',
    ]
    return '\n'.join(lines)[:1500]
