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


def delivered_to(conn: sqlite3.Connection, digest_id: int) -> list[int]:
    return [int(row[0]) for row in conn.execute(
        "SELECT chat_id FROM digest_deliveries WHERE digest_id=? ORDER BY chat_id", (digest_id,))]


def latest_parts(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    row = conn.execute("SELECT * FROM digests WHERE name=? ORDER BY created_at DESC, id DESC LIMIT 1",
                       (name,)).fetchone()
    if row is None:
        return []
    if row["run_id"] is None:
        return [row]
    return list(conn.execute("SELECT * FROM digests WHERE name=? AND run_id=? ORDER BY id",
                             (name, row["run_id"])))


async def republish(conn: sqlite3.Connection, name: str, cfg: dict[str, object], token: str) -> list[sqlite3.Row]:
    """Commit stored bytes for the entire latest run, including previously committed files."""
    import logging
    from analyzer.knowledge_publisher import GitHubPublisher

    parts = latest_parts(conn, name)
    ids = [int(row["id"]) for row in parts]
    placeholders = ",".join("?" for _ in ids)
    files = list(conn.execute(f"SELECT id, path, content FROM site_files WHERE digest_id IN ({placeholders}) ORDER BY id", ids))
    if not parts or not files:
        raise LookupError("No digest or site files found")
    publisher = GitHubPublisher(str(cfg.get("repo", "OnixFireOne/neuronavt")),
                                str(cfg.get("branch", "radar-preview")), token, batch=True)
    try:
        ok = await publisher.commit_files([(str(row["path"]), str(row["content"])) for row in files],
            f"feat(radar): republish AI radar digest {str(parts[-1]['period_end'])[:10]}")
    except Exception:
        logging.getLogger(__name__).warning("Site republish failed", exc_info=True)
        ok = False
    if ok:
        mark_committed(conn, [int(row["id"]) for row in files], publisher.last_commit_sha)
    conn.executemany("UPDATE digests SET site_status=? WHERE id=?",
                     [("ok" if ok else "commit_failed", digest_id) for digest_id in ids])
    conn.commit()
    return list(conn.execute(f"SELECT * FROM digests WHERE id IN ({placeholders}) ORDER BY id", ids))
