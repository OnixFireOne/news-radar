"""Move legacy knowledge/ reviews to the Neuronavt site without calling the LLM (TZ4 I4.4 step 3).

Source of truth is ``analysis.md_path`` pointing into ``knowledge/``; the md file is read from ``--root``.
Each review is converted to the site frontmatter (no ``digest`` field: old issues have no site page),
stored in ``site_files`` and committed to the site repo in one commit. ``analysis.md_path`` switches
to the site path only after a successful commit. Re-running is safe: committed reviews are skipped,
uncommitted ones are retried with their stored content.

    python scripts/migrate_knowledge_to_site.py --dry-run
    python scripts/migrate_knowledge_to_site.py --commit
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer.knowledge_publisher import (  # noqa: E402
    GitHubPublisher, KnowledgeDoc, build_path, build_site_review, site_review_url, validate_site_frontmatter,
)
from analyzer.site_store import SiteFile, mark_committed, review_for_message, save_files  # noqa: E402
from database.schema import get_db, init_db  # noqa: E402

LEGACY_DIR = "knowledge/"


@dataclass(frozen=True)
class Planned:
    message_id: int
    old_path: str
    site_path: str
    content: str


def parse_legacy(content: str) -> tuple[dict[str, Any], str]:
    """Split the build_markdown output: one JSON value per frontmatter line, then the body."""
    if not content.startswith("---\n") or "\n---\n" not in content[4:]:
        raise ValueError("no frontmatter")
    header, body = content[4:].split("\n---\n", 1)
    fields: dict[str, Any] = {}
    for line in header.splitlines():
        key, value = line.split(": ", 1)
        fields[key] = json.loads(value)
    return fields, body.strip()


def _section(body: str, title: str) -> str:
    match = re.search(rf"(?ms)^## {title}\n(.*?)(?=^## |\Z)", body)
    return match.group(1).strip() if match else ""


def legacy_doc(message_id: int, content: str) -> KnowledgeDoc:
    fields, body = parse_legacy(content)
    # Brief reviews have "Идея"; full ones usually open with "Коротко", otherwise with a plain paragraph.
    idea = _section(body, "Идея") or _section(body, "Коротко")
    if not idea:
        plain = re.sub(r"(?m)^#.*\n?", "", body)
        idea = next((part.strip() for part in plain.split("\n\n")
                     if part.strip() and not part.lstrip().startswith(("-", "*", "|", ">", "```"))), "")
    tags = fields.get("tags")
    return KnowledgeDoc(
        message_id=message_id, title=str(fields["title"]), source_url=str(fields["source_url"]),
        source_type=str(fields.get("source_type") or "rss"), date=str(fields["date"]),
        content_type=str(fields.get("content_type") or "other"),
        value_score=float(fields.get("value_score") or 0), topic=str(fields.get("topic") or ""),
        tags=[str(tag) for tag in tags] if isinstance(tags, list) else [],
        idea=idea, conclusion=_section(body, "Вывод"), body=body,
    )


def plan(conn: sqlite3.Connection, root: Path, site: dict[str, Any]) -> tuple[list[Planned], list[str]]:
    planned: list[Planned] = []
    problems: list[str] = []
    rows = conn.execute(
        "SELECT message_id, md_path FROM analysis WHERE md_path LIKE ? AND md_path NOT LIKE ? ORDER BY message_id",
        (LEGACY_DIR + "%", LEGACY_DIR + "candidates/%"),
    ).fetchall()
    for row in rows:
        message_id, old_path = int(row[0]), str(row[1])
        stored = review_for_message(conn, message_id)
        if stored is not None and stored.committed:
            continue
        if stored is not None:
            planned.append(Planned(message_id, old_path, stored.path, stored.content))
            continue
        file = root / old_path
        if not file.is_file():
            problems.append(f"missing file: {old_path} (message {message_id})")
            continue
        try:
            doc = legacy_doc(message_id, file.read_text(encoding="utf-8"))
            published = datetime.strptime(doc.date, "%Y-%m-%d").replace(hour=6, minute=10, tzinfo=timezone.utc)
            content = build_site_review(doc, None, published)
        except (ValueError, KeyError) as exc:
            problems.append(f"unreadable: {old_path} ({exc})")
            continue
        if not validate_site_frontmatter(content, require_digest=False):
            problems.append(f"invalid site frontmatter: {old_path}")
            continue
        planned.append(Planned(message_id, old_path,
                               build_path(doc, str(site.get("reviews_dir", "blog/src/content/reviews"))), content))
    return planned, problems


async def commit(conn: sqlite3.Connection, planned: list[Planned], site: dict[str, Any], token: str) -> bool:
    ids = save_files(conn, [SiteFile(item.site_path, item.content, "review",
                                     site_review_url(Path(item.site_path).stem, site), item.message_id)
                            for item in planned])
    conn.commit()
    publisher = GitHubPublisher(str(site.get("repo", "OnixFireOne/neuronavt")),
                                str(site.get("branch", "radar-preview")), token, batch=True)
    ok = await publisher.commit_files([(item.site_path, item.content) for item in planned],
                                      f"feat(radar): migrate {len(planned)} legacy reviews")
    if ok:
        mark_committed(conn, ids, publisher.last_commit_sha)
        conn.executemany("UPDATE analysis SET md_path=? WHERE message_id=?",
                         [(item.site_path, item.message_id) for item in planned])
        conn.commit()
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="list what would move, write nothing")
    mode.add_argument("--commit", action="store_true", help="store in site_files and commit to the site")
    parser.add_argument("--db", default=os.getenv("DATABASE_PATH", "/app/data/news.db"))
    parser.add_argument("--root", default=".", help="directory that contains knowledge/")
    parser.add_argument("--config", default=os.getenv("CONFIG_PATH", "config/settings.json"))
    args = parser.parse_args(argv)

    site = json.loads(Path(args.config).read_text(encoding="utf-8")).get("site", {})
    if not isinstance(site, dict):
        site = {}
    init_db(args.db)
    conn = get_db(args.db)
    try:
        planned, problems = plan(conn, Path(args.root), site)
        for item in planned:
            print(f"{item.message_id}: {item.old_path} -> {item.site_path}")
        for problem in problems:
            print(f"SKIP {problem}")
        print(f"Planned {len(planned)}, skipped {len(problems)}")
        if args.dry_run or not planned:
            return 0
        token = os.getenv("NEURONAVT_GITHUB_TOKEN", "").strip()
        if not site.get("enabled", False) or not token:
            print("Site is disabled or NEURONAVT_GITHUB_TOKEN is missing", file=sys.stderr)
            return 2
        if not asyncio.run(commit(conn, planned, site, token)):
            print("Site commit failed; files stay in site_files uncommitted, md_path unchanged", file=sys.stderr)
            return 1
        print(f"Committed {len(planned)} reviews")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
