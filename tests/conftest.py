"""Shared pytest fixtures. Phase 0.0 skeletons; later phases flesh these out."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def temp_vault(tmp_path: Path) -> Path:
    """A throwaway vault root mirroring the ~/commonplace/ layout."""
    for sub in ("books", "captures", "bluesky", "profile", "skills"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def memory_db() -> Iterator[sqlite3.Connection]:
    """In-memory SQLite connection. WAL-like settings not applicable; good enough for unit tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def fake_claude_cli(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record `claude -p` invocations without actually running the subprocess.

    Returns the list of captured argv lists. Later phases will extend this to
    return canned skill outputs keyed on the skill name.
    """
    captured: list[list[str]] = []

    def fake_run(argv: list[str], *_args: object, **_kwargs: object) -> object:
        captured.append(list(argv))

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Result()

    import subprocess

    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured
