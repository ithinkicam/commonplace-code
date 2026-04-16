"""Unit tests for commonplace_worker/handlers/profile.py.

Does NOT invoke claude -p live — all skill invocations are mocked.

Covers:
  - cold-start (no current.md)
  - happy-path (existing current.md + inbox + corpus)
  - corpus sampling DB queries
  - parser-fails-so-no-write
  - snapshot written on update
  - atomic write (tmp renamed to current.md)
  - inbox archival
  - missing perennials → FileNotFoundError
"""

from __future__ import annotations

import json
import sqlite3
import types
from pathlib import Path
from typing import Any

import pytest

from commonplace_db.db import migrate
from commonplace_worker.handlers.profile import (
    archive_inbox_files,
    atomic_write,
    build_corpus_sample,
    handle_profile_regen,
    read_current_profile,
    read_inbox_additions,
    read_perennials,
    sample_books_engaged,
    sample_recent_bluesky,
    sample_recent_captures,
    sample_recent_highlights,
    snapshot_current_profile,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    """Return a migrated in-memory-like SQLite connection (file-based for sqlite-vec)."""
    db_path = tmp_path / "test.db"
    import sqlite_vec  # type: ignore[import-untyped]

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    migrate(conn)
    return conn


def _profile_dir(tmp_path: Path) -> Path:
    """Create a minimal profile directory structure and return it."""
    pd = tmp_path / "profile"
    pd.mkdir()
    (pd / "inbox").mkdir()
    return pd


def _write_perennials(profile_dir: Path, content: str = "THINKERS. Gregory of Nyssa.") -> None:
    (profile_dir / "perennials.md").write_text(content, encoding="utf-8")


def _write_current(profile_dir: Path, content: str) -> None:
    (profile_dir / "current.md").write_text(content, encoding="utf-8")


def _write_inbox(profile_dir: Path, filename: str, content: str) -> None:
    (profile_dir / "inbox" / filename).write_text(content, encoding="utf-8")


# Minimal valid profile output (≤500 tokens, starts with #, required sections)
VALID_PROFILE_OUTPUT = """\
# Profile — updated 2026-04-15

## How to talk to me

- Skip preamble; get to the argument. [inferred]
- Push back when you disagree. [inferred]

## How I think

- Reads fiction as theologically load-bearing, not as illustration. [inferred]
"""

PROFILE_WITH_DIRECTIVE = """\
# Profile — updated 2026-03-15

## How to talk to me

- Skip the disclaimers. [directive, 2026-01-10]
- Match the register. [inferred]

## How I think

- Reads texts for what they know. [inferred]
"""


def _make_parser_module(
    parse_ok: bool = True,
    directives_missing: list[str] | None = None,
) -> types.ModuleType:
    """Create a mock parser module."""
    mod = types.ModuleType("mock_parser")

    class _ParseError(ValueError):
        pass

    mod.ParseError = _ParseError  # type: ignore[attr-defined]

    if parse_ok:
        def _parse(output: str) -> object:
            return object()
        mod.parse = _parse  # type: ignore[attr-defined]
    else:
        def _parse_fail(output: str) -> object:
            raise _ParseError("mock parse failure")
        mod.parse = _parse_fail  # type: ignore[attr-defined]

    _missing = directives_missing or []

    def _verify(input_profile: str, output_profile: str) -> list[str]:
        return _missing

    mod.verify_directives_preserved = _verify  # type: ignore[attr-defined]

    return mod


def _make_invoke(output: str = VALID_PROFILE_OUTPUT) -> Any:
    """Return a callable that simulates a successful skill invocation."""
    def _invoke(json_payload: str) -> str:
        return output
    return _invoke


# ---------------------------------------------------------------------------
# Tests: read_perennials
# ---------------------------------------------------------------------------


def test_read_perennials_ok(tmp_path: Path) -> None:
    pd = _profile_dir(tmp_path)
    _write_perennials(pd, "Some perennials content.")
    assert read_perennials(pd) == "Some perennials content."


def test_read_perennials_missing_raises(tmp_path: Path) -> None:
    pd = _profile_dir(tmp_path)
    with pytest.raises(FileNotFoundError, match="perennials.md not found"):
        read_perennials(pd)


# ---------------------------------------------------------------------------
# Tests: read_current_profile
# ---------------------------------------------------------------------------


def test_read_current_profile_exists(tmp_path: Path) -> None:
    pd = _profile_dir(tmp_path)
    _write_current(pd, PROFILE_WITH_DIRECTIVE)
    assert read_current_profile(pd) == PROFILE_WITH_DIRECTIVE


def test_read_current_profile_missing_returns_empty(tmp_path: Path) -> None:
    pd = _profile_dir(tmp_path)
    assert read_current_profile(pd) == ""


# ---------------------------------------------------------------------------
# Tests: read_inbox_additions
# ---------------------------------------------------------------------------


def test_read_inbox_additions_empty(tmp_path: Path) -> None:
    pd = _profile_dir(tmp_path)
    assert read_inbox_additions(pd) == []


def test_read_inbox_additions_no_inbox_dir(tmp_path: Path) -> None:
    pd = tmp_path / "profile"
    pd.mkdir()
    assert read_inbox_additions(pd) == []


def test_read_inbox_additions_with_frontmatter(tmp_path: Path) -> None:
    pd = _profile_dir(tmp_path)
    content = "---\ntimestamp: 2026-03-22T14:08:11Z\n---\nI am so tired."
    _write_inbox(pd, "001.md", content)
    additions = read_inbox_additions(pd)
    assert len(additions) == 1
    assert additions[0]["timestamp"] == "2026-03-22T14:08:11Z"
    assert "I am so tired" in additions[0]["content"]


def test_read_inbox_additions_without_frontmatter(tmp_path: Path) -> None:
    pd = _profile_dir(tmp_path)
    _write_inbox(pd, "plain.md", "Just a plain note, no frontmatter.")
    additions = read_inbox_additions(pd)
    assert len(additions) == 1
    assert "plain note" in additions[0]["content"]
    # timestamp falls back to file mtime (some ISO8601 string)
    assert "T" in additions[0]["timestamp"]


def test_read_inbox_additions_multiple_sorted(tmp_path: Path) -> None:
    pd = _profile_dir(tmp_path)
    _write_inbox(pd, "a.md", "---\ntimestamp: 2026-01-01T00:00:00Z\n---\nFirst note.")
    _write_inbox(pd, "b.md", "---\ntimestamp: 2026-02-01T00:00:00Z\n---\nSecond note.")
    additions = read_inbox_additions(pd)
    assert len(additions) == 2
    # Sorted by filename (a before b)
    assert "First note" in additions[0]["content"]
    assert "Second note" in additions[1]["content"]


# ---------------------------------------------------------------------------
# Tests: corpus sampling
# ---------------------------------------------------------------------------


def _insert_doc(conn: sqlite3.Connection, content_type: str, title: str | None = None) -> int:
    """Insert a minimal document and one chunk. Returns document_id."""
    with conn:
        cur = conn.execute(
            "INSERT INTO documents (content_type, title, source_uri, status) VALUES (?, ?, ?, 'embedded')",
            (content_type, title, f"http://example.com/{content_type}"),
        )
        doc_id = cur.lastrowid
        conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text) VALUES (?, 0, ?)",
            (doc_id, f"Sample text for {content_type} document {doc_id}"),
        )
    return doc_id  # type: ignore[return-value]


def test_sample_recent_highlights_empty(tmp_path: Path) -> None:
    conn = _make_db(tmp_path)
    assert sample_recent_highlights(conn) == []


def test_sample_recent_highlights_kindle(tmp_path: Path) -> None:
    conn = _make_db(tmp_path)
    _insert_doc(conn, "kindle")
    _insert_doc(conn, "kindle_highlight")
    results = sample_recent_highlights(conn)
    assert len(results) == 2
    assert all(isinstance(s, str) for s in results)


def test_sample_recent_captures(tmp_path: Path) -> None:
    conn = _make_db(tmp_path)
    _insert_doc(conn, "article")
    _insert_doc(conn, "youtube")
    _insert_doc(conn, "podcast")
    results = sample_recent_captures(conn)
    assert len(results) == 3


def test_sample_recent_bluesky(tmp_path: Path) -> None:
    conn = _make_db(tmp_path)
    _insert_doc(conn, "bluesky")
    _insert_doc(conn, "bluesky")
    results = sample_recent_bluesky(conn)
    assert len(results) == 2


def test_sample_books_engaged(tmp_path: Path) -> None:
    conn = _make_db(tmp_path)
    # Insert book with a title (within the 90-day window — default created_at is 'now')
    _insert_doc(conn, "book", title="Test Book Title")
    _insert_doc(conn, "audiobook", title="Test Audiobook")
    results = sample_books_engaged(conn)
    assert len(results) == 2
    assert "Test Book Title" in results or "Test Audiobook" in results


def test_build_corpus_sample_structure(tmp_path: Path) -> None:
    conn = _make_db(tmp_path)
    sample = build_corpus_sample(conn)
    assert set(sample.keys()) == {
        "recent_highlights",
        "recent_captures",
        "recent_bluesky",
        "books_engaged",
    }
    for v in sample.values():
        assert isinstance(v, list)


def test_snippet_truncation(tmp_path: Path) -> None:
    conn = _make_db(tmp_path)
    long_text = "x" * 500
    with conn:
        cur = conn.execute(
            "INSERT INTO documents (content_type, status) VALUES ('bluesky', 'embedded')"
        )
        doc_id = cur.lastrowid
        conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text) VALUES (?, 0, ?)",
            (doc_id, long_text),
        )
    results = sample_recent_bluesky(conn)
    assert len(results) == 1
    assert len(results[0]) == 300


# ---------------------------------------------------------------------------
# Tests: snapshot + atomic write
# ---------------------------------------------------------------------------


def test_snapshot_no_current_returns_none(tmp_path: Path) -> None:
    pd = _profile_dir(tmp_path)
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    result = snapshot_current_profile(pd, now)
    assert result is None


def test_snapshot_creates_history_file(tmp_path: Path) -> None:
    pd = _profile_dir(tmp_path)
    _write_current(pd, "Old profile content.")
    from datetime import UTC, datetime
    now = datetime(2026, 4, 15, 3, 0, 0, tzinfo=UTC)
    snap = snapshot_current_profile(pd, now)
    assert snap is not None
    assert snap.exists()
    assert "current-2026-04-15T03-00-00Z.md" in snap.name
    assert snap.read_text() == "Old profile content."


def test_atomic_write(tmp_path: Path) -> None:
    pd = _profile_dir(tmp_path)
    target = pd / "current.md"
    atomic_write(target, "New profile content.\n")
    assert target.exists()
    assert target.read_text() == "New profile content.\n"
    # tmp file should be gone
    assert not (pd / "current.md.tmp").exists()


# ---------------------------------------------------------------------------
# Tests: inbox archival
# ---------------------------------------------------------------------------


def test_archive_inbox_files(tmp_path: Path) -> None:
    pd = _profile_dir(tmp_path)
    inbox_dir = pd / "inbox"
    _write_inbox(pd, "note1.md", "Note one")
    _write_inbox(pd, "note2.md", "Note two")

    count = archive_inbox_files(pd, inbox_dir)
    assert count == 2
    assert not (inbox_dir / "note1.md").exists()
    assert not (inbox_dir / "note2.md").exists()
    assert (inbox_dir / "processed" / "note1.md").exists()
    assert (inbox_dir / "processed" / "note2.md").exists()


def test_archive_inbox_empty(tmp_path: Path) -> None:
    pd = _profile_dir(tmp_path)
    inbox_dir = pd / "inbox"
    count = archive_inbox_files(pd, inbox_dir)
    assert count == 0


# ---------------------------------------------------------------------------
# Tests: handle_profile_regen — cold start
# ---------------------------------------------------------------------------


def test_cold_start_no_current_md(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cold start: no current.md. Handler should pass empty string and write new profile."""
    pd = _profile_dir(tmp_path)
    _write_perennials(pd)

    conn = _make_db(tmp_path)

    monkeypatch.setenv("COMMONPLACE_PROFILE_DIR", str(pd))

    result = handle_profile_regen(
        {},
        conn,
        _invoke_skill=_make_invoke(VALID_PROFILE_OUTPUT),
        _parser_module=_make_parser_module(),
    )

    assert result["status"] == "complete"
    assert result["snapshot_path"] is None  # no old profile to snapshot
    assert (pd / "current.md").exists()
    assert (pd / "current.md").read_text() == VALID_PROFILE_OUTPUT
    assert result["inbox_processed"] == 0


# ---------------------------------------------------------------------------
# Tests: handle_profile_regen — happy path with existing profile + inbox
# ---------------------------------------------------------------------------


def test_happy_path_with_existing_profile_and_inbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pd = _profile_dir(tmp_path)
    _write_perennials(pd)
    _write_current(pd, PROFILE_WITH_DIRECTIVE)
    _write_inbox(pd, "add1.md", "---\ntimestamp: 2026-04-01T10:00:00Z\n---\nNew addition.")

    conn = _make_db(tmp_path)
    _insert_doc(conn, "kindle")
    _insert_doc(conn, "bluesky")

    monkeypatch.setenv("COMMONPLACE_PROFILE_DIR", str(pd))

    result = handle_profile_regen(
        {},
        conn,
        _invoke_skill=_make_invoke(VALID_PROFILE_OUTPUT),
        _parser_module=_make_parser_module(),
    )

    assert result["status"] == "complete"
    assert result["snapshot_path"] is not None
    assert Path(result["snapshot_path"]).exists()
    assert (pd / "current.md").read_text() == VALID_PROFILE_OUTPUT
    assert result["inbox_processed"] == 1
    # Inbox file archived
    assert (pd / "inbox" / "processed" / "add1.md").exists()
    assert not (pd / "inbox" / "add1.md").exists()


# ---------------------------------------------------------------------------
# Tests: parser failure → no write
# ---------------------------------------------------------------------------


def test_parser_failure_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pd = _profile_dir(tmp_path)
    _write_perennials(pd)
    _write_current(pd, PROFILE_WITH_DIRECTIVE)

    conn = _make_db(tmp_path)
    monkeypatch.setenv("COMMONPLACE_PROFILE_DIR", str(pd))

    with pytest.raises(RuntimeError, match="failed validation"):
        handle_profile_regen(
            {},
            conn,
            _invoke_skill=_make_invoke("BAD OUTPUT — no # prefix"),
            _parser_module=_make_parser_module(parse_ok=False),
        )

    # Old profile must be intact
    assert (pd / "current.md").read_text() == PROFILE_WITH_DIRECTIVE
    # No snapshot should have been written
    history_dir = pd / "history"
    assert not history_dir.exists() or len(list(history_dir.glob("*.md"))) == 0


# ---------------------------------------------------------------------------
# Tests: directive preservation failure → no write
# ---------------------------------------------------------------------------


def test_directive_drop_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pd = _profile_dir(tmp_path)
    _write_perennials(pd)
    _write_current(pd, PROFILE_WITH_DIRECTIVE)

    conn = _make_db(tmp_path)
    monkeypatch.setenv("COMMONPLACE_PROFILE_DIR", str(pd))

    with pytest.raises(RuntimeError, match="dropped.*directive"):
        handle_profile_regen(
            {},
            conn,
            _invoke_skill=_make_invoke(VALID_PROFILE_OUTPUT),
            _parser_module=_make_parser_module(
                parse_ok=True,
                directives_missing=["- Skip the disclaimers. [directive, 2026-01-10]"],
            ),
        )

    # Old profile intact
    assert (pd / "current.md").read_text() == PROFILE_WITH_DIRECTIVE


# ---------------------------------------------------------------------------
# Tests: missing perennials → FileNotFoundError
# ---------------------------------------------------------------------------


def test_missing_perennials_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pd = _profile_dir(tmp_path)
    # No perennials.md created
    conn = _make_db(tmp_path)
    monkeypatch.setenv("COMMONPLACE_PROFILE_DIR", str(pd))

    with pytest.raises(FileNotFoundError, match="perennials.md not found"):
        handle_profile_regen({}, conn)


# ---------------------------------------------------------------------------
# Tests: invoke_skill receives correct JSON shape
# ---------------------------------------------------------------------------


def test_invoke_receives_correct_json_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pd = _profile_dir(tmp_path)
    _write_perennials(pd, "THINKERS. Gregory.")
    _write_current(pd, PROFILE_WITH_DIRECTIVE)
    _write_inbox(pd, "a.md", "---\ntimestamp: 2026-04-10T00:00:00Z\n---\nAn addition.")

    conn = _make_db(tmp_path)
    monkeypatch.setenv("COMMONPLACE_PROFILE_DIR", str(pd))

    captured: list[str] = []

    def _capture_invoke(json_str: str) -> str:
        captured.append(json_str)
        return VALID_PROFILE_OUTPUT

    handle_profile_regen(
        {},
        conn,
        _invoke_skill=_capture_invoke,
        _parser_module=_make_parser_module(),
    )

    assert len(captured) == 1
    payload = json.loads(captured[0])

    assert "current_profile" in payload
    assert "perennials" in payload
    assert "inbox_additions" in payload
    assert "corpus_sample" in payload

    assert payload["perennials"] == "THINKERS. Gregory."
    assert payload["current_profile"] == PROFILE_WITH_DIRECTIVE
    assert isinstance(payload["inbox_additions"], list)
    assert len(payload["inbox_additions"]) == 1
    assert payload["inbox_additions"][0]["timestamp"] == "2026-04-10T00:00:00Z"

    cs = payload["corpus_sample"]
    assert set(cs.keys()) == {"recent_highlights", "recent_captures", "recent_bluesky", "books_engaged"}


# ---------------------------------------------------------------------------
# Tests: snapshot written before new profile
# ---------------------------------------------------------------------------


def test_snapshot_written_before_new_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pd = _profile_dir(tmp_path)
    _write_perennials(pd)
    old_content = PROFILE_WITH_DIRECTIVE
    _write_current(pd, old_content)

    conn = _make_db(tmp_path)
    monkeypatch.setenv("COMMONPLACE_PROFILE_DIR", str(pd))

    result = handle_profile_regen(
        {},
        conn,
        _invoke_skill=_make_invoke(VALID_PROFILE_OUTPUT),
        _parser_module=_make_parser_module(),
    )

    snap = Path(result["snapshot_path"])
    assert snap.exists()
    assert snap.read_text() == old_content
    assert (pd / "current.md").read_text() == VALID_PROFILE_OUTPUT
