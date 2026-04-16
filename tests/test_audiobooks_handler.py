"""Tests for commonplace_worker/handlers/audiobooks.py."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from commonplace_db.db import migrate

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """In-memory SQLite connection with all migrations applied."""
    import sqlite_vec  # type: ignore[import-untyped]

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    migrate(conn)
    return conn


@pytest.fixture
def audiobook_dir(tmp_path: Path) -> Path:
    """A fake audiobook directory with a single .m4b file."""
    book_dir = tmp_path / "Test Author - Test Book"
    book_dir.mkdir()
    (book_dir / "Test Author - Test Book.m4b").write_bytes(b"fake m4b audio")
    return book_dir


@pytest.fixture
def multi_part_dir(tmp_path: Path) -> Path:
    """A fake multi-part audiobook directory with multiple mp3 files."""
    book_dir = tmp_path / "War and Peace"
    book_dir.mkdir()
    (book_dir / "Part 01.mp3").write_bytes(b"part1 audio")
    (book_dir / "Part 02.mp3").write_bytes(b"part2 audio")
    (book_dir / "Part 03.mp3").write_bytes(b"part3 audio")
    return book_dir


@pytest.fixture
def dir_with_junk(tmp_path: Path) -> Path:
    """A directory that contains macOS junk alongside a real m4b."""
    book_dir = tmp_path / "My Book"
    book_dir.mkdir()
    (book_dir / "._My Book.m4b").write_bytes(b"macOS resource fork")
    (book_dir / ".DS_Store").write_bytes(b"macOS DS_Store")
    (book_dir / "My Book.m4b").write_bytes(b"real audio")
    (book_dir / "cover.jpg").write_bytes(b"cover image")
    return book_dir


def _make_mock_mutagen_file(title: str, author: str, narrator: str = "") -> MagicMock:
    """Build a MagicMock that looks like a mutagen MP4 File with given tags."""
    mock_file = MagicMock()
    mock_file.tags = {
        "©nam": [title],
        "©ART": [author],
        "aART": [author],
        "©nrt": [narrator] if narrator else [],
    }
    return mock_file


# ---------------------------------------------------------------------------
# Directory-name parsing
# ---------------------------------------------------------------------------


def test_parse_author_dash_title() -> None:
    """Standard 'Author - Title' directory name is split correctly."""
    from commonplace_worker.handlers.audiobooks import _parse_dir_name

    result = _parse_dir_name("Brandon Sanderson - The Way of Kings")
    assert result["author"] == "Brandon Sanderson"
    assert result["title"] == "The Way of Kings"


def test_parse_bare_title() -> None:
    """Directory with no dash gets full name as title."""
    from commonplace_worker.handlers.audiobooks import _parse_dir_name

    result = _parse_dir_name("All Fours")
    assert result["title"] == "All Fours"
    assert result["author"] is None


def test_parse_unicode_colon() -> None:
    """Unicode colon substitute (U+A789) is normalized to ':'."""
    from commonplace_worker.handlers.audiobooks import _parse_dir_name

    # "America, América꞉ A New History of the New World"
    raw = "America, América\ua789 A New History of the New World"
    result = _parse_dir_name(raw)
    assert ":" in result["title"]
    assert result["author"] is None


def test_parse_trailing_bracket_stripped() -> None:
    """Trailing [2021 MP3] descriptor is stripped."""
    from commonplace_worker.handlers.audiobooks import _parse_dir_name

    result = _parse_dir_name("Craig A. Boyd - The Virtues A Very Short Introduction [2021 MP3]")
    assert result["author"] == "Craig A. Boyd"
    assert "[2021 MP3]" not in result["title"]


def test_parse_unabridged_stripped() -> None:
    """(Unabridged) parenthetical is stripped."""
    from commonplace_worker.handlers.audiobooks import _parse_dir_name

    result = _parse_dir_name("Jane Austen - Pride and Prejudice (Unabridged)")
    assert result["author"] == "Jane Austen"
    assert "Unabridged" not in result["title"]


def test_parse_narrator_bracket_stripped() -> None:
    """[Narrator Name] bracket at end of dir name is stripped from title."""
    from commonplace_worker.handlers.audiobooks import _parse_dir_name

    result = _parse_dir_name("Murdoch, Iris - Under the Net [Samuel West]")
    assert result["author"] == "Murdoch, Iris"
    assert "Samuel West" not in result["title"]
    assert "Under the Net" in result["title"]


def test_parse_codec_suffix_stripped() -> None:
    """Codec suffix like -LC_64_22050_stereo is removed."""
    from commonplace_worker.handlers.audiobooks import _parse_dir_name

    result = _parse_dir_name("A_Christmas_Carol-AAX_44_128")
    # After underscore normalization: "A Christmas Carol"
    assert "Christmas Carol" in result["title"]


def test_parse_multi_dash_title() -> None:
    """Author - Title - Subtitle stays grouped as title."""
    from commonplace_worker.handlers.audiobooks import _parse_dir_name

    result = _parse_dir_name(
        "Andrew Jotischky - The Monastic World - A 1,200-Year History"
    )
    assert result["author"] == "Andrew Jotischky"
    assert "Monastic World" in result["title"]


# ---------------------------------------------------------------------------
# Metadata extraction (mutagen mocked)
# ---------------------------------------------------------------------------


def test_extract_tags_mp4(tmp_path: Path) -> None:
    """mutagen MP4 tags are extracted correctly."""
    from commonplace_worker.handlers.audiobooks import _extract_tags

    fake_file = tmp_path / "book.m4b"
    fake_file.write_bytes(b"fake")

    mock_mf = _make_mock_mutagen_file("My Book", "My Author", "My Narrator")

    with patch("commonplace_worker.handlers.audiobooks.MutagenFile", return_value=mock_mf):
        result = _extract_tags(fake_file)

    assert result["title"] == "My Book"
    assert result["author"] == "My Author"


def test_extract_tags_no_tags(tmp_path: Path) -> None:
    """mutagen returning None tags yields empty dict."""
    from commonplace_worker.handlers.audiobooks import _extract_tags

    fake_file = tmp_path / "book.m4b"
    fake_file.write_bytes(b"fake")

    mock_mf = MagicMock()
    mock_mf.tags = None

    with patch("commonplace_worker.handlers.audiobooks.MutagenFile", return_value=mock_mf):
        result = _extract_tags(fake_file)

    assert result == {}


def test_extract_tags_mutagen_exception(tmp_path: Path) -> None:
    """mutagen read errors return empty dict (don't propagate)."""
    from commonplace_worker.handlers.audiobooks import _extract_tags

    fake_file = tmp_path / "book.m4b"
    fake_file.write_bytes(b"not audio")

    with patch(
        "commonplace_worker.handlers.audiobooks.MutagenFile", side_effect=Exception("bad file")
    ):
        result = _extract_tags(fake_file)

    assert result == {}


# ---------------------------------------------------------------------------
# Fuzzy merge
# ---------------------------------------------------------------------------


def test_fuzzy_merge_exact_match(db_conn: sqlite3.Connection) -> None:
    """Exact title match returns the storygraph_entry id."""
    from commonplace_worker.handlers.audiobooks import _fuzzy_merge

    db_conn.execute(
        """INSERT INTO documents (content_type, title, author, content_hash, status)
           VALUES ('storygraph_entry', 'The Way of Kings', 'Brandon Sanderson',
                   'aabbcc', 'complete')"""
    )
    db_conn.commit()

    result = _fuzzy_merge(db_conn, "The Way of Kings", "Brandon Sanderson")
    assert result is not None


def test_fuzzy_merge_near_match(db_conn: sqlite3.Connection) -> None:
    """Near-title match (minor variation) still merges."""
    from commonplace_worker.handlers.audiobooks import _fuzzy_merge

    db_conn.execute(
        """INSERT INTO documents (content_type, title, author, content_hash, status)
           VALUES ('storygraph_entry', 'Pride and Prejudice', 'Jane Austen',
                   'ddeeff', 'complete')"""
    )
    db_conn.commit()

    # Slight variation — should still match at high token overlap
    result = _fuzzy_merge(db_conn, "Pride and Prejudice (Unabridged)", "Jane Austen")
    assert result is not None


def test_fuzzy_merge_no_match(db_conn: sqlite3.Connection) -> None:
    """Very different title returns None."""
    from commonplace_worker.handlers.audiobooks import _fuzzy_merge

    db_conn.execute(
        """INSERT INTO documents (content_type, title, author, content_hash, status)
           VALUES ('storygraph_entry', 'Moby Dick', 'Herman Melville', '001122', 'complete')"""
    )
    db_conn.commit()

    result = _fuzzy_merge(db_conn, "The Sound and the Fury", "William Faulkner")
    assert result is None


def test_fuzzy_merge_empty_table(db_conn: sqlite3.Connection) -> None:
    """Empty storygraph_entry table returns None without error."""
    from commonplace_worker.handlers.audiobooks import _fuzzy_merge

    result = _fuzzy_merge(db_conn, "Some Book", "Some Author")
    assert result is None


# ---------------------------------------------------------------------------
# Full handler: insert path
# ---------------------------------------------------------------------------


def test_handle_audiobook_ingest_inserts_document(
    audiobook_dir: Path, db_conn: sqlite3.Connection
) -> None:
    """Ingesting a new audiobook inserts a documents row with content_type='audiobook'."""
    from commonplace_worker.handlers.audiobooks import handle_audiobook_ingest

    mock_mf = _make_mock_mutagen_file("Test Book", "Test Author")
    with patch("commonplace_worker.handlers.audiobooks.MutagenFile", return_value=mock_mf):
        result = handle_audiobook_ingest({"path": str(audiobook_dir)}, db_conn)

    assert result["document_id"] is not None
    assert result["action"] in ("inserted", "matched")

    doc = db_conn.execute(
        "SELECT * FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    assert doc is not None
    assert doc["content_type"] == "audiobook"
    assert doc["status"] == "complete"


def test_handle_audiobook_ingest_enqueues_classify_book(
    audiobook_dir: Path, db_conn: sqlite3.Connection
) -> None:
    """A classify_book job is enqueued after a successful ingest."""
    from commonplace_worker.handlers.audiobooks import handle_audiobook_ingest

    mock_mf = _make_mock_mutagen_file("Test Book", "Test Author")
    with patch("commonplace_worker.handlers.audiobooks.MutagenFile", return_value=mock_mf):
        result = handle_audiobook_ingest({"path": str(audiobook_dir)}, db_conn)

    job = db_conn.execute(
        "SELECT * FROM job_queue WHERE kind = 'classify_book' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert job is not None
    payload = json.loads(job["payload"])
    assert payload["document_id"] == result["document_id"]
    assert payload["content_type"] == "audiobook"


def test_handle_audiobook_ingest_storygraph_match(
    audiobook_dir: Path, db_conn: sqlite3.Connection
) -> None:
    """When a matching storygraph_entry exists, it is updated with audiobook_path."""
    from commonplace_worker.handlers.audiobooks import handle_audiobook_ingest

    # Pre-insert a storygraph_entry
    db_conn.execute(
        """INSERT INTO documents (content_type, title, author, content_hash, status)
           VALUES ('storygraph_entry', 'Test Book', 'Test Author', 'sg001', 'complete')"""
    )
    db_conn.commit()
    sg_id = db_conn.execute(
        "SELECT id FROM documents WHERE content_type = 'storygraph_entry'"
    ).fetchone()["id"]

    mock_mf = _make_mock_mutagen_file("Test Book", "Test Author")
    with patch("commonplace_worker.handlers.audiobooks.MutagenFile", return_value=mock_mf):
        result = handle_audiobook_ingest({"path": str(audiobook_dir)}, db_conn)

    assert result["action"] == "matched"

    # storygraph_entry should now have audiobook_path set
    updated = db_conn.execute(
        "SELECT audiobook_path FROM documents WHERE id = ?", (sg_id,)
    ).fetchone()
    assert updated["audiobook_path"] == str(audiobook_dir)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_second_run(
    audiobook_dir: Path, db_conn: sqlite3.Connection
) -> None:
    """Re-running on the same path is a no-op (same document_id returned)."""
    from commonplace_worker.handlers.audiobooks import handle_audiobook_ingest

    mock_mf = _make_mock_mutagen_file("Test Book", "Test Author")
    with patch("commonplace_worker.handlers.audiobooks.MutagenFile", return_value=mock_mf):
        r1 = handle_audiobook_ingest({"path": str(audiobook_dir)}, db_conn)
        r2 = handle_audiobook_ingest({"path": str(audiobook_dir)}, db_conn)

    assert r2["action"] == "skipped"
    assert r2["document_id"] == r1["document_id"]

    # Only one audiobook document row created
    count = db_conn.execute(
        "SELECT COUNT(*) FROM documents WHERE content_type = 'audiobook'"
    ).fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# Multi-part book handling
# ---------------------------------------------------------------------------


def test_multi_part_book_single_document(
    multi_part_dir: Path, db_conn: sqlite3.Connection
) -> None:
    """A directory with multiple audio files → one logical book document."""
    from commonplace_worker.handlers.audiobooks import handle_audiobook_ingest

    mock_mf = _make_mock_mutagen_file("War and Peace", "Leo Tolstoy")
    with patch("commonplace_worker.handlers.audiobooks.MutagenFile", return_value=mock_mf):
        result = handle_audiobook_ingest({"path": str(multi_part_dir)}, db_conn)

    assert result["document_id"] is not None

    count = db_conn.execute(
        "SELECT COUNT(*) FROM documents WHERE content_type = 'audiobook'"
    ).fetchone()[0]
    assert count == 1  # Not 3 (one per part)


# ---------------------------------------------------------------------------
# Skip rules
# ---------------------------------------------------------------------------


def test_skip_resource_fork_files(dir_with_junk: Path, db_conn: sqlite3.Connection) -> None:
    """._* and .DS_Store files are ignored; real .m4b is processed."""
    from commonplace_worker.handlers.audiobooks import _collect_audio_files

    audio_files = _collect_audio_files(dir_with_junk)
    names = [f.name for f in audio_files]

    assert "My Book.m4b" in names
    assert not any(n.startswith("._") for n in names)
    assert ".DS_Store" not in names


def test_skip_non_audio_extensions(tmp_path: Path) -> None:
    """Non-audio files are not collected."""
    from commonplace_worker.handlers.audiobooks import _collect_audio_files

    book_dir = tmp_path / "My Book"
    book_dir.mkdir()
    (book_dir / "book.m4b").write_bytes(b"audio")
    (book_dir / "cover.jpg").write_bytes(b"cover")
    (book_dir / "notes.txt").write_bytes(b"text")
    (book_dir / "book.nfo").write_bytes(b"nfo")

    files = _collect_audio_files(book_dir)
    names = [f.name for f in files]

    assert "book.m4b" in names
    assert "cover.jpg" not in names
    assert "notes.txt" not in names


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_missing_path_raises(db_conn: sqlite3.Connection) -> None:
    from commonplace_worker.handlers.audiobooks import handle_audiobook_ingest

    with pytest.raises(ValueError, match="missing 'path'"):
        handle_audiobook_ingest({}, db_conn)


def test_nonexistent_path_raises(db_conn: sqlite3.Connection) -> None:
    from commonplace_worker.handlers.audiobooks import handle_audiobook_ingest

    with pytest.raises(FileNotFoundError):
        handle_audiobook_ingest({"path": "/nonexistent/audiobook/dir"}, db_conn)


def test_drive_not_mounted_raises(db_conn: sqlite3.Connection) -> None:
    """AudiobookDriveNotMounted is raised when /Volumes/Expansion/ is absent."""
    from commonplace_worker.handlers.audiobooks import (
        AudiobookDriveNotMounted,
        handle_audiobook_ingest,
    )

    with (
        patch("pathlib.Path.exists", return_value=False),
        pytest.raises(AudiobookDriveNotMounted),
    ):
        handle_audiobook_ingest(
            {"path": "/Volumes/Expansion/Audiobooks/Some Book"},
            db_conn,
        )


def test_empty_dir_skipped(tmp_path: Path, db_conn: sqlite3.Connection) -> None:
    """An empty directory (no audio files) is skipped with action='skipped'."""
    from commonplace_worker.handlers.audiobooks import handle_audiobook_ingest

    empty_dir = tmp_path / "Empty Book"
    empty_dir.mkdir()
    (empty_dir / "cover.jpg").write_bytes(b"cover")

    result = handle_audiobook_ingest({"path": str(empty_dir)}, db_conn)
    assert result["action"] == "skipped"
    assert result["document_id"] is None


# ---------------------------------------------------------------------------
# Normalize title helper
# ---------------------------------------------------------------------------


def test_normalize_title_strips_codec() -> None:
    from commonplace_worker.handlers.audiobooks import _normalize_title

    result = _normalize_title("A_Christmas_Carol-AAX_44_128")
    assert "Christmas Carol" in result
    assert "AAX" not in result


def test_normalize_title_unicode_colon() -> None:
    from commonplace_worker.handlers.audiobooks import _normalize_title

    raw = "Trauma Plot\ua789 A Life"
    result = _normalize_title(raw)
    assert ":" in result
    assert "\ua789" not in result
