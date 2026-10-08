"""Legacy knowledge/ reviews move to the site without the LLM (TZ4 I4.4 step 3)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock

import pytest

from analyzer.knowledge_publisher import GitHubPublisher, validate_site_frontmatter
from database.schema import get_db, init_db

BRIEF = """---
title: "Пакеты знаний для MCP"
source_url: "https://example.org/a"
source_type: "rss"
date: "2026-09-28"
content_type: "tool_release"
value_score: 7.0
topic: "integrations"
tags: ["mcp", "knowledge"]
---

## Идея
Команда подключает пакет знаний к MCP-хосту.

## Вывод
Сравнения стоит повторить.
"""

FULL = """---
title: "Агенты в Claude Code"
source_url: "https://example.org/b"
source_type: "hackernews"
date: "2026-09-30"
content_type: "practical_case"
value_score: 8
topic: "agents"
tags: ["agents"]
---

# Агенты в Claude Code

## Коротко
Автор раздал роли пяти агентам и <b>сравнил</b> результат.

## Что сделали
- Роли
"""


def load_script() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "migrate_knowledge_to_site.py"
    spec = importlib.util.spec_from_file_location("migrate_knowledge_to_site", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def setup(tmp_path: Path) -> tuple[str, Path, Path]:
    db = str(tmp_path / "news.db")
    init_db(db)
    conn = get_db(db)
    conn.execute("INSERT INTO sources (id, type, name) VALUES (1, 'rss', 'feed')")
    for message_id, md_path in ((1, "knowledge/2026/09/brief-1.md"), (2, "knowledge/2026/09/full-2.md"),
                                (3, "knowledge/2026/09/missing-3.md"), (4, "knowledge/candidates/2026-10-02.md"),
                                (5, None)):
        conn.execute("INSERT INTO messages (id, source_id, external_id, text) VALUES (?, 1, ?, 'x')",
                     (message_id, str(message_id)))
        conn.execute("INSERT INTO analysis (message_id, temperature, md_path) VALUES (?, 5, ?)", (message_id, md_path))
    conn.commit()
    conn.close()
    root = tmp_path / "repo"
    (root / "knowledge/2026/09").mkdir(parents=True)
    (root / "knowledge/2026/09/brief-1.md").write_text(BRIEF, encoding="utf-8")
    (root / "knowledge/2026/09/full-2.md").write_text(FULL, encoding="utf-8")
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({"site": {"enabled": True, "repo": "o/r", "branch": "main"}}), encoding="utf-8")
    return db, root, config


def md_paths(db: str) -> dict[int, Any]:
    conn = get_db(db)
    try:
        return {int(row[0]): row[1] for row in conn.execute("SELECT message_id, md_path FROM analysis")}
    finally:
        conn.close()


def test_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = load_script()
    db, root, config = setup(tmp_path)
    before = md_paths(db)
    assert script.main(["--dry-run", "--db", db, "--root", str(root), "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "Planned 2, skipped 1" in out and "missing file: knowledge/2026/09/missing-3.md" in out
    assert "candidates" not in out
    assert md_paths(db) == before
    conn = get_db(db)
    try:
        assert conn.execute("SELECT count(*) FROM site_files").fetchone()[0] == 0
    finally:
        conn.close()


def test_commit_converts_and_switches_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = load_script()
    db, root, config = setup(tmp_path)
    monkeypatch.setenv("NEURONAVT_GITHUB_TOKEN", "fake")
    calls: list[list[tuple[str, str]]] = []
    ok = False

    async def commit_files(self: GitHubPublisher, files: list[tuple[str, str]], message: str) -> bool:
        calls.append(files)
        self.last_commit_sha = "sha" if ok else None
        return ok

    monkeypatch.setattr(GitHubPublisher, "commit_files", commit_files)
    argv = ["--commit", "--db", db, "--root", str(root), "--config", str(config)]
    # A failed commit keeps the files for a retry and leaves the legacy links alone.
    assert script.main(argv) == 1
    assert md_paths(db)[1] == "knowledge/2026/09/brief-1.md"
    ok = True
    assert script.main(argv) == 0
    assert calls[0] == calls[1] and len(calls[1]) == 2
    files = dict(calls[1])
    paths = md_paths(db)
    assert paths[1] == "blog/src/content/reviews/2026/09/2026-09-28-pakety-znaniy-dlya-mcp-1.md"
    assert paths[2].startswith("blog/src/content/reviews/2026/09/2026-09-30-") and paths[2].endswith("-2.md")
    assert paths[3].startswith("knowledge/") and paths[5] is None
    brief, full = files[paths[1]], files[paths[2]]
    for content in (brief, full):
        assert validate_site_frontmatter(content, require_digest=False)
        assert not validate_site_frontmatter(content)  # no digest field: old issues have no site page
        assert "\ndigest:" not in content
    assert "pubDatetime: 2026-09-28T06:10:00.000Z" in brief
    assert '"description": ' not in brief and 'description: "Команда подключает пакет знаний к MCP-хосту."' in brief
    assert "## Вывод\nСравнения стоит повторить." in brief
    assert "# Агенты в Claude Code" not in full and "&lt;b>сравнил" in full
    assert 'description: "Автор раздал роли пяти агентам и <b>сравнил</b> результат."' in full
    conn = get_db(db)
    try:
        assert conn.execute("SELECT count(*) FROM site_files WHERE commit_sha='sha'").fetchone()[0] == 2
    finally:
        conn.close()
    # Re-running is a no-op.
    assert script.main(argv) == 0 and len(calls) == 2
