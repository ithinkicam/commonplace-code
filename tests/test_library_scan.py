"""Tests for scripts/library_scan.py."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
