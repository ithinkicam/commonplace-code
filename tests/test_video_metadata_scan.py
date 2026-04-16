"""Tests for scripts/video_metadata_scan.py.

Uses a mocked filesystem — no real drive access.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Import the scanner module under test
# ---------------------------------------------------------------------------


def _import_scanner():
    """Import the scanner script as a module (avoids sys.exit on import)."""
    import importlib.util

    script_path = Path(__file__).parent.parent / "scripts" / "video_metadata_scan.py"
    spec = importlib.util.spec_from_file_location("video_metadata_scan", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scanner = _import_scanner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def movies_dir(tmp_path: Path) -> Path:
    """A fake movies directory with a variety of entries."""
    d = tmp_path / "Movies"
    d.mkdir()

    # Standard movie dirs
    (d / "Toy Story (1995) MULTi VFF 2160p BluRay x265-QTZ").mkdir()
    (d / "Addams Family Values 1993 2160p Bluray x265-KiNGDOM").mkdir()
    (d / "A Fantastic Woman (2017) [BluRay] [1080p] [YTS.AM]").mkdir()

    # Standalone video file
    (d / "101 Dalmatians.avi").write_bytes(b"fake video")

    # Junk files (should be skipped)
    (d / "Torrent Downloaded from Glodls.to.txt").write_text("junk")
    (d / ".DS_Store").write_bytes(b"mac junk")
    (d / "._hidden.mkv").write_bytes(b"resource fork")

    return d


@pytest.fixture
def tv_dir(tmp_path: Path) -> Path:
    """A fake TV shows directory."""
    d = tmp_path / "TV Shows"
    d.mkdir()

    (d / "Andor (2022) Season 2 S02 (2160p HDR DSNP WEB-DL x265)").mkdir()
    (d / "Blood.of.Zeus.S01.COMPLETE.720p.NF.WEBRip.x264-GalaxyTV").mkdir()
    (d / "Bluey").mkdir()

    # Junk
    (d / "[TGx]Downloaded from torrentgalaxy.to .txt").write_text("junk")

    return d


# ---------------------------------------------------------------------------
# _find_entries
# ---------------------------------------------------------------------------


def test_find_entries_movies(movies_dir: Path) -> None:
    entries = scanner._find_entries(movies_dir, is_tv=False)
    names = [e[0].name for e in entries]

    # Should include actual movie dirs and standalone video files
    assert "Toy Story (1995) MULTi VFF 2160p BluRay x265-QTZ" in names
    assert "101 Dalmatians.avi" in names

    # Should NOT include junk
    assert not any("Glodls" in n for n in names)
    assert ".DS_Store" not in names
    assert "._hidden.mkv" not in names


def test_find_entries_tv(tv_dir: Path) -> None:
    entries = scanner._find_entries(tv_dir, is_tv=True)
    names = [e[0].name for e in entries]

    assert "Andor (2022) Season 2 S02 (2160p HDR DSNP WEB-DL x265)" in names
    assert "Bluey" in names

    # Junk txt skipped
    assert not any("torrentgalaxy" in n for n in names)


def test_find_entries_marks_is_tv(movies_dir: Path, tv_dir: Path) -> None:
    movie_entries = scanner._find_entries(movies_dir, is_tv=False)
    tv_entries = scanner._find_entries(tv_dir, is_tv=True)

    assert all(not is_tv for _, is_tv in movie_entries)
    assert all(is_tv for _, is_tv in tv_entries)


def test_find_entries_empty_dir(tmp_path: Path) -> None:
    d = tmp_path / "Empty"
    d.mkdir()
    entries = scanner._find_entries(d, is_tv=False)
    assert entries == []


def test_find_entries_permission_error(tmp_path: Path) -> None:
    """PermissionError on directory listing returns empty list."""
    d = tmp_path / "Restricted"
    d.mkdir()
    with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
        entries = scanner._find_entries(d, is_tv=False)
    assert entries == []


# ---------------------------------------------------------------------------
# _should_skip_entry
# ---------------------------------------------------------------------------


def test_should_skip_txt_file(tmp_path: Path) -> None:
    f = tmp_path / "readme.txt"
    f.write_text("x")
    assert scanner._should_skip_entry(f) is True


def test_should_skip_ds_store(tmp_path: Path) -> None:
    f = tmp_path / ".DS_Store"
    f.write_bytes(b"")
    assert scanner._should_skip_entry(f) is True


def test_should_skip_resource_fork(tmp_path: Path) -> None:
    f = tmp_path / "._movie.mkv"
    f.write_bytes(b"")
    assert scanner._should_skip_entry(f) is True


def test_should_not_skip_movie_dir(tmp_path: Path) -> None:
    d = tmp_path / "Toy Story (1995)"
    d.mkdir()
    assert scanner._should_skip_entry(d) is False


def test_should_not_skip_video_file(tmp_path: Path) -> None:
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"")
    assert scanner._should_skip_entry(f) is False


# ---------------------------------------------------------------------------
# _dry_run_parse
# ---------------------------------------------------------------------------


def test_dry_run_parse_all_parseable(movies_dir: Path) -> None:
    entries = scanner._find_entries(movies_dir, is_tv=False)
    unparseable = scanner._dry_run_parse(entries)
    # All standard movie entries should parse OK; .avi standalone too
    assert len(unparseable) == 0


def test_dry_run_parse_returns_unparseable_names(tmp_path: Path) -> None:
    """An entry that fails parse returns its name in unparseable list."""
    d = tmp_path / "Movies"
    d.mkdir()
    (d / "Normal Movie (2020)").mkdir()

    entries = scanner._find_entries(d, is_tv=False)

    # Mock parse to raise ValueError for the entry
    with patch(
        "commonplace_worker.handlers.video_filename.parse",
        side_effect=ValueError("cannot parse"),
    ):
        unparseable = scanner._dry_run_parse(entries)

    assert len(unparseable) == 1


# ---------------------------------------------------------------------------
# main() dry-run
# ---------------------------------------------------------------------------


def test_main_dry_run_returns_zero(movies_dir: Path, tv_dir: Path) -> None:
    """dry-run with real (tmp) directories should exit 0."""
    exit_code = scanner.main(
        [
            "--dry-run",
            "--movies-dir",
            str(movies_dir),
            "--tv-dir",
            str(tv_dir),
        ]
    )
    assert exit_code == 0


def test_main_dry_run_with_limit(movies_dir: Path, tv_dir: Path) -> None:
    """dry-run with --limit should exit 0."""
    exit_code = scanner.main(
        [
            "--dry-run",
            "--limit",
            "2",
            "--movies-dir",
            str(movies_dir),
            "--tv-dir",
            str(tv_dir),
        ]
    )
    assert exit_code == 0


def test_main_nonexistent_dirs_returns_one() -> None:
    """When both dirs don't exist, exit code is 1."""
    exit_code = scanner.main(
        [
            "--dry-run",
            "--movies-dir",
            "/nonexistent/movies",
            "--tv-dir",
            "/nonexistent/tv",
        ]
    )
    assert exit_code == 1


def test_main_one_dir_missing_still_runs(movies_dir: Path, tmp_path: Path) -> None:
    """When only the TV dir is missing, movies still process."""
    exit_code = scanner.main(
        [
            "--dry-run",
            "--movies-dir",
            str(movies_dir),
            "--tv-dir",
            "/nonexistent/tv",
        ]
    )
    assert exit_code == 0
