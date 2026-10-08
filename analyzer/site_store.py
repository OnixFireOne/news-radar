"""Persist prepared site files independently of remote publication."""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from collections.abc import Sequence


@dataclass(frozen=True)
class SiteFile:
    path: str
    content: str
    kind: str
    url: str | None = None
    message_id: int | None = None


@dataclass(frozen=True)
class StoredReview:
    id: int
    path: str
    content: str
    url: str | None
    committed: bool


def save_files(conn: sqlite3.Connection, files: Sequence[SiteFile]) -> list[int]:
    """Upsert files; invalidate publication only when their content changes."""
    ids: list[int] = []
    for file in files:
        conn.execute("""INSERT INTO site_files (path, content, kind, url, message_id)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET
            committed_at=CASE WHEN site_files.content != excluded.content THEN NULL ELSE committed_at END,
            commit_sha=CASE WHEN site_files.content != excluded.content THEN NULL ELSE commit_sha END,
            content=excluded.content, kind=excluded.kind, url=excluded.url, message_id=excluded.message_id""",
            (file.path, file.content, file.kind, file.url, file.message_id))
        row = conn.execute("SELECT id FROM site_files WHERE path=?", (file.path,)).fetchone()
        assert row is not None
        ids.append(int(row[0]))
    return ids


def mark_committed(conn: sqlite3.Connection, ids: Sequence[int], sha: str | None) -> None:
    conn.executemany("UPDATE site_files SET committed_at=CURRENT_TIMESTAMP, commit_sha=? WHERE id=?",
                     [(sha, file_id) for file_id in ids])


def review_for_message(conn: sqlite3.Connection, message_id: int) -> StoredReview | None:
    row = conn.execute("""SELECT id, path, content, url, committed_at FROM site_files
        WHERE message_id=? AND kind='review' ORDER BY id DESC LIMIT 1""", (message_id,)).fetchone()
    if row is None:
        return None
    return StoredReview(int(row[0]), str(row[1]), str(row[2]), row[3], row[4] is not None)


def attach_digest(conn: sqlite3.Connection, ids: Sequence[int], digest_id: int,
                  site_url: str | None, site_status: str) -> None:
    conn.executemany("UPDATE site_files SET digest_id=? WHERE id=?", [(digest_id, i) for i in ids])
    conn.execute("UPDATE digests SET site_url=?, site_status=? WHERE id=?",
                 (site_url, site_status, digest_id))
