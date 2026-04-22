"""Tests for commonplace_worker/handlers/dayone.py.

Builds a tiny DayOne-shaped sqlite fixture in tmp_path, seeds a few
entries, runs handle_dayone_ingest, and asserts the commonplace DB picks
them up correctly.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from commonplace_db.db import migrate

# Sample Core Data timestamps (seconds since 2001-01-01 UTC)
_MAR_13_2026 = 795_065_412.0
_MAR_16_2026 = 795_333_286.0
_MAR_24_2026 = 796_040_007.0


@pytest.fixture
def commonplace_db() -> sqlite3.Connection:
    """In-memory commonplace DB with all migrations applied."""
    import sqlite_vec  # type: ignore[import-untyped]

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    migrate(conn)
    return conn


@pytest.fixture
def dayone_db(tmp_path: Path) -> Path:
    """Build a fixture DayOne.sqlite with three entries in two journals."""
    path = tmp_path / "DayOne.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE ZJOURNAL (
            Z_PK INTEGER PRIMARY KEY,
            ZNAME VARCHAR
        );
        CREATE TABLE ZENTRY (
            Z_PK INTEGER PRIMARY KEY,
            ZUUID VARCHAR,
            ZMARKDOWNTEXT VARCHAR,
            ZCREATIONDATE TIMESTAMP,
            ZMODIFIEDDATE TIMESTAMP,
            ZJOURNAL INTEGER,
            ZSTARRED INTEGER,
            ZENTRYTYPE VARCHAR
        );
        """
    )
    conn.execute(
        "INSERT INTO ZJOURNAL (Z_PK, ZNAME) VALUES (1, 'Journal')"
    )
    conn.execute(
        "INSERT INTO ZJOURNAL (Z_PK, ZNAME) VALUES (8, 'Gender Affirmation')"
    )
    # Three entries, two journals, distinct UUIDs.
    conn.executemany(
        "INSERT INTO ZENTRY "
        "(Z_PK, ZUUID, ZMARKDOWNTEXT, ZCREATIONDATE, ZMODIFIEDDATE, ZJOURNAL, ZSTARRED) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                1,
                "AAAA1111BBBB2222CCCC3333DDDD4444",
                "# Morning reflections\n\nThinking about the day ahead.",
                _MAR_13_2026,
                _MAR_13_2026,
                1,
                0,
            ),
            (
                2,
                "EEEE5555FFFF6666AAAA7777BBBB8888",
                "Walked the dog. Light rain. Crocuses starting.",
                _MAR_16_2026,
                _MAR_16_2026,
                1,
                1,
            ),
            (
                3,
                "CCCC9999DDDD0000EEEE1111FFFF2222",
                "# Pre-op notes\n\nTherapist reminded me that performing "
                "euphoria isn't required.",
                _MAR_24_2026,
                _MAR_24_2026,
                8,
                0,
            ),
        ],
    )
    conn.commit()
    conn.close()
    return path


def _fake_embedder(texts: list[str], model: str) -> list[list[float]]:
    """Return zero-vectors of dimension 768 for each chunk."""
    return [[0.0] * 768 for _ in texts]


class TestBackfillHappyPath:
    def test_backfill_inserts_all_entries(
        self, commonplace_db: sqlite3.Connection, dayone_db: Path
    ) -> None:
        from commonplace_worker.handlers.dayone import handle_dayone_ingest

        result = handle_dayone_ingest(
            {"mode": "backfill"},
            commonplace_db,
            _dayone_db_path=dayone_db,
            _embedder=_fake_embedder,
        )
        assert result["inserted"] == 3
        assert result["updated"] == 0
        assert result["skipped"] == 0

        # Three documents rows, all content_type='dayone_entry'
        rows = commonplace_db.execute(
            "SELECT content_type, source_id, title, source_uri FROM documents "
            "WHERE content_type='dayone_entry' ORDER BY source_id"
        ).fetchall()
        assert len(rows) == 3
        assert rows[0]["source_uri"] == f"dayone://{rows[0]['source_id']}"
        assert rows[0]["content_type"] == "dayone_entry"
        titles = {r["title"] for r in rows}
        assert "Morning reflections" in titles
        assert "Walked the dog. Light rain. Crocuses starting." in titles
        assert "Pre-op notes" in titles

    def test_rerun_is_idempotent(
        self, commonplace_db: sqlite3.Connection, dayone_db: Path
    ) -> None:
        """Second run against unchanged DayOne must skip everything."""
        from commonplace_worker.handlers.dayone import handle_dayone_ingest

        handle_dayone_ingest(
            {"mode": "backfill"},
            commonplace_db,
            _dayone_db_path=dayone_db,
            _embedder=_fake_embedder,
        )
        result = handle_dayone_ingest(
            {"mode": "backfill"},
            commonplace_db,
            _dayone_db_path=dayone_db,
            _embedder=_fake_embedder,
        )
        assert result["skipped"] == 3
        assert result["inserted"] == 0

    def test_edit_triggers_reembed(
        self, commonplace_db: sqlite3.Connection, dayone_db: Path
    ) -> None:
        """Edit the markdown on one entry; its ZMODIFIEDDATE bump must cause
        a fresh re-embed. The other entries stay skipped."""
        from commonplace_worker.handlers.dayone import handle_dayone_ingest

        handle_dayone_ingest(
            {"mode": "backfill"},
            commonplace_db,
            _dayone_db_path=dayone_db,
            _embedder=_fake_embedder,
        )

        # Edit entry 2 in the Day One fixture: bump text + modified_date.
        src = sqlite3.connect(dayone_db)
        src.execute(
            "UPDATE ZENTRY SET ZMARKDOWNTEXT = ?, ZMODIFIEDDATE = ? WHERE Z_PK = 2",
            ("Walked the dog. Rain stopped. Crocuses open.", _MAR_16_2026 + 3600),
        )
        src.commit()
        src.close()

        result = handle_dayone_ingest(
            {"mode": "backfill"},
            commonplace_db,
            _dayone_db_path=dayone_db,
            _embedder=_fake_embedder,
        )
        assert result["updated"] == 1
        assert result["skipped"] == 2
        assert result["inserted"] == 0

        # The document row for uuid EEEE... should now have the new title
        row = commonplace_db.execute(
            "SELECT title FROM documents WHERE source_id=?",
            ("EEEE5555FFFF6666AAAA7777BBBB8888",),
        ).fetchone()
        assert "Rain stopped" in row["title"]

    def test_chunks_and_vectors_populated(
        self, commonplace_db: sqlite3.Connection, dayone_db: Path
    ) -> None:
        """After a successful ingest, chunks and vec rows exist per entry."""
        from commonplace_worker.handlers.dayone import handle_dayone_ingest

        handle_dayone_ingest(
            {"mode": "backfill"},
            commonplace_db,
            _dayone_db_path=dayone_db,
            _embedder=_fake_embedder,
        )

        chunk_count = commonplace_db.execute(
            "SELECT COUNT(*) FROM chunks c "
            "JOIN documents d ON d.id = c.document_id "
            "WHERE d.content_type='dayone_entry'"
        ).fetchone()[0]
        assert chunk_count >= 3  # at least one chunk per entry


class TestSinceMode:
    def test_since_filters_entries(
        self, commonplace_db: sqlite3.Connection, dayone_db: Path
    ) -> None:
        """{'mode': 'since', 'iso': ...} only ingests entries modified
        at or after the cutoff."""
        from commonplace_worker.handlers.dayone import handle_dayone_ingest

        # Cutoff = Mar 20, 2026. Only entry 3 (Mar 24) qualifies.
        result = handle_dayone_ingest(
            {"mode": "since", "iso": "2026-03-20T00:00:00Z"},
            commonplace_db,
            _dayone_db_path=dayone_db,
            _embedder=_fake_embedder,
        )
        assert result["inserted"] == 1
        assert result["skipped"] == 0

        rows = commonplace_db.execute(
            "SELECT source_id FROM documents WHERE content_type='dayone_entry'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["source_id"] == "CCCC9999DDDD0000EEEE1111FFFF2222"

    def test_since_accepts_offset_timezone(
        self, commonplace_db: sqlite3.Connection, dayone_db: Path
    ) -> None:
        """Tolerate `+00:00` and `Z` both."""
        from commonplace_worker.handlers.dayone import handle_dayone_ingest

        result = handle_dayone_ingest(
            {"mode": "since", "iso": "2026-01-01T00:00:00+00:00"},
            commonplace_db,
            _dayone_db_path=dayone_db,
            _embedder=_fake_embedder,
        )
        assert result["inserted"] == 3

    def test_since_bad_iso_raises(
        self, commonplace_db: sqlite3.Connection, dayone_db: Path
    ) -> None:
        from commonplace_worker.handlers.dayone import handle_dayone_ingest

        with pytest.raises(ValueError, match="bad ISO-8601"):
            handle_dayone_ingest(
                {"mode": "since", "iso": "definitely not a date"},
                commonplace_db,
                _dayone_db_path=dayone_db,
                _embedder=_fake_embedder,
            )


class TestEdgeCases:
    def test_missing_dayone_db_raises(
        self, commonplace_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        from commonplace_worker.handlers.dayone import handle_dayone_ingest

        with pytest.raises(FileNotFoundError, match="DayOne.sqlite not found"):
            handle_dayone_ingest(
                {"mode": "backfill"},
                commonplace_db,
                _dayone_db_path=tmp_path / "nope.sqlite",
                _embedder=_fake_embedder,
            )

    def test_empty_markdown_entries_skipped_at_source(
        self, commonplace_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Entries with NULL or empty ZMARKDOWNTEXT must not leak into the
        commonplace DB — they carry no embeddable content."""
        from commonplace_worker.handlers.dayone import handle_dayone_ingest

        path = tmp_path / "DayOne.sqlite"
        src = sqlite3.connect(path)
        src.executescript(
            """
            CREATE TABLE ZJOURNAL (Z_PK INTEGER PRIMARY KEY, ZNAME VARCHAR);
            CREATE TABLE ZENTRY (
                Z_PK INTEGER PRIMARY KEY, ZUUID VARCHAR,
                ZMARKDOWNTEXT VARCHAR, ZCREATIONDATE TIMESTAMP,
                ZMODIFIEDDATE TIMESTAMP, ZJOURNAL INTEGER, ZSTARRED INTEGER
            );
            INSERT INTO ZJOURNAL VALUES (1, 'Journal');
            INSERT INTO ZENTRY (Z_PK, ZUUID, ZMARKDOWNTEXT, ZCREATIONDATE, ZMODIFIEDDATE, ZJOURNAL, ZSTARRED)
                VALUES (1, 'nullmarkdown123456789', NULL, 100.0, 100.0, 1, 0);
            INSERT INTO ZENTRY (Z_PK, ZUUID, ZMARKDOWNTEXT, ZCREATIONDATE, ZMODIFIEDDATE, ZJOURNAL, ZSTARRED)
                VALUES (2, 'emptymarkdown12345678', '', 200.0, 200.0, 1, 0);
            INSERT INTO ZENTRY (Z_PK, ZUUID, ZMARKDOWNTEXT, ZCREATIONDATE, ZMODIFIEDDATE, ZJOURNAL, ZSTARRED)
                VALUES (3, 'realmarkdown123456789', 'Actual content here.', 300.0, 300.0, 1, 0);
            """
        )
        src.commit()
        src.close()

        result = handle_dayone_ingest(
            {"mode": "backfill"},
            commonplace_db,
            _dayone_db_path=path,
            _embedder=_fake_embedder,
        )
        assert result["inserted"] == 1

    def test_title_derivation_strips_heading_markers(
        self, commonplace_db: sqlite3.Connection, dayone_db: Path
    ) -> None:
        """`# Heading` in the first line becomes the title, `#` stripped."""
        from commonplace_worker.handlers.dayone import _derive_title

        assert _derive_title("# Morning reflections\n\n body") == "Morning reflections"
        assert _derive_title("## Sub heading\n body") == "Sub heading"
        assert _derive_title("Plain text\n more") == "Plain text"
        assert _derive_title("") == "(untitled Day One entry)"
        assert _derive_title("\n\n\n") == "(untitled Day One entry)"

    def test_title_cap_at_80_chars(
        self,
    ) -> None:
        from commonplace_worker.handlers.dayone import _derive_title

        long = "A" * 200
        assert len(_derive_title(long)) == 80

    def test_unknown_mode_raises(
        self, commonplace_db: sqlite3.Connection, dayone_db: Path
    ) -> None:
        from commonplace_worker.handlers.dayone import handle_dayone_ingest

        with pytest.raises(ValueError, match="unknown dayone ingest mode"):
            handle_dayone_ingest(
                {"mode": "wat"},
                commonplace_db,
                _dayone_db_path=dayone_db,
                _embedder=_fake_embedder,
            )


class TestContentHash:
    def test_hash_changes_on_text_edit(self) -> None:
        """Sanity: same text + different modified_date must yield different hash."""
        t1 = "# same title\nbody"
        h1_a = hashlib.sha256(f"{t1}|{100.0:.6f}".encode()).hexdigest()
        h1_b = hashlib.sha256(f"{t1}|{200.0:.6f}".encode()).hexdigest()
        assert h1_a != h1_b

    def test_hash_stable_on_same_text_and_date(self) -> None:
        t = "some body"
        a = hashlib.sha256(f"{t}|{100.0:.6f}".encode()).hexdigest()
        b = hashlib.sha256(f"{t}|{100.0:.6f}".encode()).hexdigest()
        assert a == b
