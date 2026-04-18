"""Tests for scripts/library_scan.py."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_scan(argv: list[str]) -> tuple[int, str]:
    """Run library_scan.main() and capture stdout."""
    import io
    from contextlib import redirect_stdout

    # Ensure scripts/ is importable
    scripts_dir = Path(__file__).parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import importlib
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "library_scan",
        scripts_dir / "library_scan.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.main(argv)
    return rc, buf.getvalue()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dry_run_lists_supported_files(tmp_path: Path) -> None:
    """--dry-run reports supported files and skips unsupported."""
    (tmp_path / "book.epub").write_bytes(b"fake epub")
    (tmp_path / "book2.pdf").write_bytes(b"fake pdf")
    (tmp_path / "book.chm").write_bytes(b"fake chm")
    (tmp_path / "notes.txt").write_bytes(b"text file")

    rc, out = _run_scan(["--dry-run", "--library-path", str(tmp_path)])

    assert rc == 0
    # 2 supported files (epub + pdf), chm and txt skipped
    assert "found=2" in out
    assert "enqueued=(dry-run) 2" in out
    assert "skipped_format=2" in out


def test_since_filters_old_files(tmp_path: Path) -> None:
    """--since filters files older than the timestamp."""
    old = tmp_path / "old.epub"
    old.write_bytes(b"old epub")
    # Make it old
    old_ts = (datetime.now(UTC) - timedelta(days=10)).timestamp()
    import os
    os.utime(old, (old_ts, old_ts))

    new = tmp_path / "new.epub"
    new.write_bytes(b"new epub")
    # New file has current mtime — no need to touch

    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    rc, out = _run_scan(["--dry-run", "--library-path", str(tmp_path), "--since", since])

    assert rc == 0
    assert "found=1" in out
    assert "skipped_since=1" in out


def test_unsupported_formats_skipped(tmp_path: Path) -> None:
    """Unsupported formats (txt, docx, etc.) are reported as skipped."""
    (tmp_path / "book.epub").write_bytes(b"epub")
    (tmp_path / "notes.docx").write_bytes(b"docx")
    (tmp_path / "readme.txt").write_bytes(b"txt")

    rc, out = _run_scan(["--dry-run", "--library-path", str(tmp_path)])

    assert rc == 0
    assert "found=1" in out
    assert "skipped_format=2" in out


def test_missing_library_path_returns_error(tmp_path: Path) -> None:
    """Non-existent library path returns exit code 1."""
    rc, out = _run_scan(["--dry-run", "--library-path", str(tmp_path / "does_not_exist")])
    assert rc == 1


def test_invalid_since_returns_error(tmp_path: Path) -> None:
    """Invalid --since value returns exit code 1."""
    (tmp_path / "book.epub").write_bytes(b"epub")
    rc, out = _run_scan(["--dry-run", "--library-path", str(tmp_path), "--since", "not-a-date"])
    assert rc == 1


def test_chm_files_skipped(tmp_path: Path) -> None:
    """chm files appear in skipped_format count."""
    (tmp_path / "book.chm").write_bytes(b"chm content")
    (tmp_path / "book.epub").write_bytes(b"epub content")

    rc, out = _run_scan(["--dry-run", "--library-path", str(tmp_path)])

    assert rc == 0
    assert "found=1" in out
    assert "skipped_format=1" in out


def test_fast_path_skip_when_path_size_mtime_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-existing documents row with matching path+size+mtime is a fast-path skip.

    The slow-path SHA-256 hasher must NOT be called for such rows.
    """
    import sqlite3

    # Put a real book file in the library root
    books_dir = tmp_path / "books"
    books_dir.mkdir()
    book = books_dir / "already.epub"
    book.write_bytes(b"the contents")
    st = book.stat()

    # Redirect connect()'s default db path to a throwaway file.  The module
    # captures DB_PATH at import time, so env-var monkeypatching is too late —
    # patch the module attribute directly.
    db_path = tmp_path / "library.db"
    import commonplace_db.db as db_mod

    monkeypatch.setattr(db_mod, "DB_PATH", str(db_path))

    # Apply migrations and insert a documents row whose stat fields match the file.
    conn = db_mod.connect(str(db_path))
    db_mod.migrate(conn)
    with conn:
        conn.execute(
            """
            INSERT INTO documents
                (content_type, source_uri, content_hash, file_size, file_mtime, status)
            VALUES ('book', ?, 'fake-hash-never-matched', ?, ?, 'embedded')
            """,
            (str(book), st.st_size, st.st_mtime),
        )
    conn.close()

    # Patch _sha256 to blow up if the fast-path ever falls through.
    calls: list[Path] = []

    def _boom(p: Path) -> str:
        calls.append(p)
        raise AssertionError(f"_sha256 should not be called on fast-path; got {p}")

    import commonplace_worker.handlers.library as library_handler

    monkeypatch.setattr(library_handler, "_sha256", _boom)

    rc, out = _run_scan(["--library-path", str(books_dir)])

    assert rc == 0
    assert calls == []  # fast-path; hasher never invoked
    assert "fast-path" in out.lower() or "skipped_fast_path=1" in out
    assert "skipped_already_ingested=1" in out
    # And of course nothing was enqueued
    with sqlite3.connect(str(db_path)) as check:
        check.row_factory = sqlite3.Row
        n_jobs = check.execute("SELECT COUNT(*) FROM job_queue").fetchone()[0]
    assert n_jobs == 0
