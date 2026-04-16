"""Tests for scripts/audiobooks_scan.py."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

# ---------------------------------------------------------------------------
# Helper: run the scan script against a tmp_path
# ---------------------------------------------------------------------------


def _run_scan(argv: list[str]) -> tuple[int, str]:
    """Run audiobooks_scan.main() and capture stdout."""
    scripts_dir = Path(__file__).parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "audiobooks_scan",
        scripts_dir / "audiobooks_scan.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.main(argv)
    return rc, buf.getvalue()


# ---------------------------------------------------------------------------
# Test drive structure builder
# ---------------------------------------------------------------------------


def _make_audiobooks_tree(base: Path) -> None:
    """Create a realistic fake audiobook tree under *base*."""
    # Top-level .m4b files (bare)
    (base / "A_Christmas_Carol-AAX_44_128.m4b").write_bytes(b"audio1")
    (base / "Anna_Karenina-LC_64_22050_stereo.m4b").write_bytes(b"audio2")

    # Subdirectory with single m4b
    d1 = base / "Brandon Sanderson - The Way of Kings"
    d1.mkdir()
    (d1 / "The Way of Kings.m4b").write_bytes(b"audio3")
    (d1 / "._The Way of Kings.m4b").write_bytes(b"junk")  # macOS resource fork

    # Subdirectory with multiple mp3 (multi-part)
    d2 = base / "War and Peace"
    d2.mkdir()
    (d2 / "Part01.mp3").write_bytes(b"part1")
    (d2 / "Part02.mp3").write_bytes(b"part2")

    # Subdirectory with only junk (no audio) — should be skipped
    d3 = base / "Empty Dir"
    d3.mkdir()
    (d3 / "cover.jpg").write_bytes(b"cover")
    (d3 / ".DS_Store").write_bytes(b"ds_store")

    # macOS resource-fork top-level (should be skipped)
    (base / "._A_Christmas_Carol-AAX_44_128.m4b").write_bytes(b"junk")

    # Non-audio top-level file (should be skipped)
    (base / "README.txt").write_bytes(b"readme")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dry_run_finds_correct_count(tmp_path: Path) -> None:
    """--dry-run reports the right number of books found."""
    _make_audiobooks_tree(tmp_path)

    rc, out = _run_scan(["--dry-run", "--audiobooks-path", str(tmp_path)])

    assert rc == 0
    # 2 bare m4b + 1 single-file subdir + 1 multi-part subdir = 4 books
    assert "found=4" in out


def test_dry_run_skips_empty_subdirs(tmp_path: Path) -> None:
    """Directories with no audio files are not counted."""
    _make_audiobooks_tree(tmp_path)

    rc, out = _run_scan(["--dry-run", "--audiobooks-path", str(tmp_path)])

    assert rc == 0
    # "Empty Dir" and ._* junk shouldn't inflate the count
    assert "found=4" in out


def test_dry_run_does_not_write_jobs(tmp_path: Path) -> None:
    """--dry-run never writes to the database (no DB connection needed)."""
    _make_audiobooks_tree(tmp_path)
    # If this doesn't raise (from missing DB), dry-run is confirmed safe
    rc, out = _run_scan(["--dry-run", "--audiobooks-path", str(tmp_path)])
    assert rc == 0
    assert "dry-run" in out.lower()


def test_limit_caps_enqueued(tmp_path: Path) -> None:
    """--dry-run + --limit N reports at most N would_enqueue."""
    _make_audiobooks_tree(tmp_path)

    rc, out = _run_scan(["--dry-run", "--limit", "2", "--audiobooks-path", str(tmp_path)])

    assert rc == 0
    assert "would_enqueue=2" in out


def test_limit_zero(tmp_path: Path) -> None:
    """--limit 0 results in 0 would_enqueue."""
    _make_audiobooks_tree(tmp_path)

    rc, out = _run_scan(["--dry-run", "--limit", "0", "--audiobooks-path", str(tmp_path)])

    assert rc == 0
    assert "would_enqueue=0" in out


def test_missing_path_returns_error(tmp_path: Path) -> None:
    """Non-existent path returns exit code 1."""
    rc, out = _run_scan(
        ["--dry-run", "--audiobooks-path", str(tmp_path / "does_not_exist")]
    )
    assert rc == 1


def test_skip_macos_resource_forks(tmp_path: Path) -> None:
    """._* files at top level are not counted as books."""
    (tmp_path / "._My Book.m4b").write_bytes(b"junk")
    (tmp_path / "My Book.m4b").write_bytes(b"audio")

    rc, out = _run_scan(["--dry-run", "--audiobooks-path", str(tmp_path)])

    assert rc == 0
    assert "found=1" in out


def test_skip_ds_store(tmp_path: Path) -> None:
    """.DS_Store is not counted as a book."""
    (tmp_path / ".DS_Store").write_bytes(b"ds")
    (tmp_path / "My Book.m4b").write_bytes(b"audio")

    rc, out = _run_scan(["--dry-run", "--audiobooks-path", str(tmp_path)])

    assert rc == 0
    assert "found=1" in out


def test_skip_non_audio_files(tmp_path: Path) -> None:
    """Plain text and image files are not counted as books."""
    (tmp_path / "notes.txt").write_bytes(b"txt")
    (tmp_path / "cover.jpg").write_bytes(b"img")
    (tmp_path / "Real Book.m4b").write_bytes(b"audio")

    rc, out = _run_scan(["--dry-run", "--audiobooks-path", str(tmp_path)])

    assert rc == 0
    assert "found=1" in out


def test_nested_author_dir_discovered(tmp_path: Path) -> None:
    """Author folder containing book subdirs is handled (one level deeper scan)."""
    author_dir = tmp_path / "Becky Chambers"
    author_dir.mkdir()

    book1 = author_dir / "A Prayer for the Crown-Shy"
    book1.mkdir()
    (book1 / "A Prayer for the Crown-Shy.m4b").write_bytes(b"audio1")

    book2 = author_dir / "A Psalm for the Wild-Built"
    book2.mkdir()
    (book2 / "A Psalm for the Wild-Built.m4b").write_bytes(b"audio2")

    rc, out = _run_scan(["--dry-run", "--audiobooks-path", str(tmp_path)])

    assert rc == 0
    # Two nested books should be discovered
    assert "found=2" in out


def test_single_file_book_is_found(tmp_path: Path) -> None:
    """A single bare .m4b file at root level counts as one book."""
    (tmp_path / "Candide.m4b").write_bytes(b"audio")

    rc, out = _run_scan(["--dry-run", "--audiobooks-path", str(tmp_path)])

    assert rc == 0
    assert "found=1" in out


def test_empty_audiobooks_folder(tmp_path: Path) -> None:
    """An empty audiobooks folder results in found=0 without error."""
    rc, out = _run_scan(["--dry-run", "--audiobooks-path", str(tmp_path)])

    assert rc == 0
    assert "found=0" in out
