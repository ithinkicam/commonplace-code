"""Tests for scripts/import_storygraph.py — StoryGraph CSV importer."""

from __future__ import annotations

import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest

from commonplace_db import connect, migrate

# ---------------------------------------------------------------------------
# Make scripts/ importable without installing it as a package.
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from import_storygraph import (  # noqa: E402
    _content_hash,
    _parse_rating,
    _parse_read_date,
    run_import,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CSV = Path(__file__).parent / "fixtures" / "storygraph" / "sample.csv"

INLINE_CSV = textwrap.dedent("""\
    Title,Authors,Read Status,Star Rating,Last Date Read,StoryGraph ID
    The Pragmatic Programmer,David Thomas|Andrew Hunt,read,5.0,2024-01-15,sg-001
    Dune,Frank Herbert,read,4.75,2023-11-20,sg-002
    Good Omens,Terry Pratchett|Neil Gaiman,read,4.5,2023-09-10,sg-003
    Unfinished Book,Some Author,to-read,,,sg-004
    No ID Book,Anonymous Author,read,3.5,2024-03-01,
    Bad Date Book,Another Author,read,4.0,not-a-date,sg-005
""")


@pytest.fixture()
def migrated_conn() -> sqlite3.Connection:
    """Fresh in-memory DB with all migrations applied."""
    conn = connect(":memory:")
    migrate(conn)
    return conn


@pytest.fixture()
def inline_csv(tmp_path: Path) -> Path:
    """Write the inline CSV to a temp file and return its path."""
    p = tmp_path / "storygraph_test.csv"
    p.write_text(INLINE_CSV, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestParseRating:
    def test_valid_float(self) -> None:
        assert _parse_rating("4.75") == pytest.approx(4.75)

    def test_integer_string(self) -> None:
        assert _parse_rating("5") == pytest.approx(5.0)

    def test_empty_string(self) -> None:
        assert _parse_rating("") is None

    def test_none(self) -> None:
        assert _parse_rating(None) is None

    def test_non_numeric(self) -> None:
        assert _parse_rating("not-a-number") is None


class TestParseReadDate:
    def test_iso_date(self) -> None:
        assert _parse_read_date("2024-01-15") == "2024-01-15"

    def test_slash_separated(self) -> None:
        assert _parse_read_date("2024/01/15") == "2024-01-15"

    def test_pipe_separated_takes_first(self) -> None:
        result = _parse_read_date("2024-01-15|2023-06-01")
        assert result == "2024-01-15"

    def test_none(self) -> None:
        assert _parse_read_date(None) is None

    def test_empty(self) -> None:
        assert _parse_read_date("") is None

    def test_malformed_stored_as_is(self) -> None:
        # Bad dates are stored (with a warning) rather than dropped.
        result = _parse_read_date("not-a-date")
        assert result == "not-a-date"


class TestContentHash:
    def test_reproducible(self) -> None:
        h1 = _content_hash("Dune", "Frank Herbert")
        h2 = _content_hash("Dune", "Frank Herbert")
        assert h1 == h2

    def test_different_inputs_differ(self) -> None:
        assert _content_hash("Dune", "Frank Herbert") != _content_hash(
            "Dune", "Someone Else"
        )

    def test_sha256_format(self) -> None:
        h = _content_hash("X", "Y")
        assert len(h) == 64
        int(h, 16)  # should not raise


# ---------------------------------------------------------------------------
# Integration tests using in-memory DB
# ---------------------------------------------------------------------------


class TestRunImport:
    def test_basic_counts(self, migrated_conn: sqlite3.Connection, inline_csv: Path) -> None:
        summary = run_import(inline_csv, migrated_conn)
        # 6 data rows: 5 read/finished, 1 to-read
        assert summary["rows_read"] == 6
        assert summary["skipped_unread"] == 1
        # 5 read rows inserted (no pre-existing data)
        assert summary["inserted"] == 5
        assert summary["skipped_existing"] == 0

    def test_unread_entries_skipped(
        self, migrated_conn: sqlite3.Connection, inline_csv: Path
    ) -> None:
        run_import(inline_csv, migrated_conn)
        rows = migrated_conn.execute(
            "SELECT title FROM documents WHERE content_type='storygraph_entry'"
        ).fetchall()
        titles = [r[0] for r in rows]
        assert "Unfinished Book" not in titles

    def test_multi_author_joined_correctly(
        self, migrated_conn: sqlite3.Connection, inline_csv: Path
    ) -> None:
        run_import(inline_csv, migrated_conn)
        row = migrated_conn.execute(
            "SELECT author FROM documents WHERE title=?",
            ("Good Omens",),
        ).fetchone()
        assert row is not None
        # Authors field should preserve the pipe-joined string from CSV
        assert "Pratchett" in row[0] or "Gaiman" in row[0]

    def test_missing_source_id_accepted(
        self, migrated_conn: sqlite3.Connection, inline_csv: Path
    ) -> None:
        run_import(inline_csv, migrated_conn)
        row = migrated_conn.execute(
            "SELECT source_id, content_hash FROM documents WHERE title=?",
            ("No ID Book",),
        ).fetchone()
        assert row is not None
        assert row["source_id"] is None
        # content_hash fallback should be populated
        assert row["content_hash"] is not None
        assert len(row["content_hash"]) == 64

    def test_rating_stored(
        self, migrated_conn: sqlite3.Connection, inline_csv: Path
    ) -> None:
        run_import(inline_csv, migrated_conn)
        row = migrated_conn.execute(
            "SELECT rating FROM documents WHERE title=?",
            ("Dune",),
        ).fetchone()
        assert row is not None
        assert row["rating"] == pytest.approx(4.75)

    def test_read_date_stored(
        self, migrated_conn: sqlite3.Connection, inline_csv: Path
    ) -> None:
        run_import(inline_csv, migrated_conn)
        row = migrated_conn.execute(
            "SELECT read_date FROM documents WHERE title=?",
            ("The Pragmatic Programmer",),
        ).fetchone()
        assert row is not None
        assert row["read_date"] == "2024-01-15"

    def test_idempotent_reimport(
        self, migrated_conn: sqlite3.Connection, inline_csv: Path
    ) -> None:
        s1 = run_import(inline_csv, migrated_conn)
        s2 = run_import(inline_csv, migrated_conn)
        # Second import inserts nothing new.
        assert s2["inserted"] == 0
        assert s2["skipped_existing"] == s1["inserted"]

    def test_unique_index_prevents_duplicate_source_id(
        self, migrated_conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Two imports of the same source_id do not create duplicate rows."""
        csv1 = tmp_path / "a.csv"
        csv1.write_text(
            "Title,Authors,Read Status,Star Rating,Last Date Read,StoryGraph ID\n"
            "Dune,Frank Herbert,read,4.75,2023-11-20,sg-002\n",
            encoding="utf-8",
        )
        csv2 = tmp_path / "b.csv"
        csv2.write_text(
            "Title,Authors,Read Status,Star Rating,Last Date Read,StoryGraph ID\n"
            "Dune (alt title),Frank Herbert,read,3.0,2024-01-01,sg-002\n",
            encoding="utf-8",
        )
        run_import(csv1, migrated_conn)
        s2 = run_import(csv2, migrated_conn)
        count = migrated_conn.execute(
            "SELECT COUNT(*) FROM documents WHERE source_id='sg-002'"
        ).fetchone()[0]
        assert count == 1
        assert s2["skipped_existing"] == 1

    def test_dry_run_writes_nothing(
        self, migrated_conn: sqlite3.Connection, inline_csv: Path
    ) -> None:
        summary = run_import(inline_csv, migrated_conn, dry_run=True)
        # In dry-run mode, inserted count represents "would-insert".
        assert summary["inserted"] > 0
        # But no rows were actually written.
        count = migrated_conn.execute(
            "SELECT COUNT(*) FROM documents WHERE content_type='storygraph_entry'"
        ).fetchone()[0]
        assert count == 0

    def test_content_type_is_storygraph_entry(
        self, migrated_conn: sqlite3.Connection, inline_csv: Path
    ) -> None:
        run_import(inline_csv, migrated_conn)
        rows = migrated_conn.execute(
            "SELECT DISTINCT content_type FROM documents"
        ).fetchall()
        types = [r[0] for r in rows]
        assert "storygraph_entry" in types

    def test_status_set_to_complete(
        self, migrated_conn: sqlite3.Connection, inline_csv: Path
    ) -> None:
        run_import(inline_csv, migrated_conn)
        rows = migrated_conn.execute(
            "SELECT DISTINCT status FROM documents WHERE content_type='storygraph_entry'"
        ).fetchall()
        statuses = [r[0] for r in rows]
        assert statuses == ["complete"]


# ---------------------------------------------------------------------------
# Fixture-file sanity (the real sample.csv under tests/fixtures/)
# ---------------------------------------------------------------------------


class TestSampleFixture:
    def test_sample_csv_exists(self) -> None:
        assert SAMPLE_CSV.exists(), f"Missing fixture: {SAMPLE_CSV}"

    def test_sample_csv_importable(self, migrated_conn: sqlite3.Connection) -> None:
        summary = run_import(SAMPLE_CSV, migrated_conn)
        assert summary["rows_read"] > 0
        assert summary["inserted"] > 0
        assert summary["skipped_unread"] >= 1  # at least the "to-read" row
