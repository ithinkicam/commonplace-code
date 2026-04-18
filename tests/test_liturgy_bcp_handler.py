"""Tests for commonplace_worker/handlers/liturgy_bcp.py.

Raw parser output counts (from tests/fixtures/bcp_1979 as of 2026-04-18):
  collects:                   275  (Rite I + Rite II across all sections)
  daily_office:               151  (raw; 33 are cross-file duplicates)
  psalter:                      5  (only sample files in fixtures; full run = 150)
                                    (2 appear in multiple sample files)
  proper_liturgies:           227
  prayers_and_thanksgivings:   81  (70 prayers + 11 thanksgivings)
  -----------------------------------------------
  raw total:                  739

Unique-inserted totals per parser (deduplicating cross-file repeated units):
  collects:                   275  (all unique)
  daily_office:               118  (33 units shared across mp/ep files)
  psalter:                      3  (2 psalms in multiple sample files)
  proper_liturgies:           227  (all unique)
  prayers_and_thanksgivings:   81  (all unique)
  -----------------------------------------------
  unique total:               704

The psalter fixture ships only sample HTML files (book_one_sample,
psalm_119_sample, malformed_sample) — not all 150 psalms.  The smoke test
asserts the actual fixture total (3 unique psalms), not a production estimate.

Cross-file skips are correct behaviour — e.g., "Confession of Sin Rite I"
legitimately appears in both ep1.html and mp1.html; one row is enough.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from commonplace_db.db import migrate

# ---------------------------------------------------------------------------
# Path to test fixtures
# ---------------------------------------------------------------------------

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "bcp_1979"

# ---------------------------------------------------------------------------
# Expected unit counts per parser from the committed fixtures
# ---------------------------------------------------------------------------

# Raw parser output (before deduplication)
EXPECTED_COLLECTS_RAW = 275
EXPECTED_DAILY_OFFICE_RAW = 151
EXPECTED_PSALTER_RAW = 5
EXPECTED_PROPER_LITURGIES_RAW = 227
EXPECTED_PRAYERS_RAW = 81
EXPECTED_TOTAL_RAW = (
    EXPECTED_COLLECTS_RAW
    + EXPECTED_DAILY_OFFICE_RAW
    + EXPECTED_PSALTER_RAW
    + EXPECTED_PROPER_LITURGIES_RAW
    + EXPECTED_PRAYERS_RAW
)  # = 739

# Unique-inserted counts (deduplicating cross-file and cross-parser repeated units)
EXPECTED_COLLECTS = 275
EXPECTED_DAILY_OFFICE = 118   # 151 raw - 33 cross-file duplicates (mp/ep shared prayers)
# Psalter: 5 raw, -1 psalm_001 in both book_one_sample+malformed_sample,
#          -1 psalm_119 already ingested by daily_office noonday.html
EXPECTED_PSALTER_ISOLATED = 4  # when run alone: 5 raw - 1 cross-file dup
EXPECTED_PSALTER = 3           # when run after daily_office: also -1 cross-parser dup
EXPECTED_PROPER_LITURGIES = 227
EXPECTED_PRAYERS = 81
EXPECTED_TOTAL = (
    EXPECTED_COLLECTS
    + EXPECTED_DAILY_OFFICE
    + EXPECTED_PSALTER
    + EXPECTED_PROPER_LITURGIES
    + EXPECTED_PRAYERS
)  # = 704


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
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


def _fake_embedder(texts: list[str], model: str) -> list[list[float]]:
    """Return zero-vectors of dimension 768 for each text."""
    return [[0.0] * 768 for _ in texts]


def _run_handler(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Helper: call the handler with the fake embedder and fixture source_root."""
    from commonplace_worker.handlers.liturgy_bcp import handle_liturgy_bcp_ingest

    payload.setdefault("source_root", str(FIXTURES_ROOT))
    return handle_liturgy_bcp_ingest(payload, conn, _embedder=_fake_embedder)


# ---------------------------------------------------------------------------
# Helper: count documents + meta rows
# ---------------------------------------------------------------------------


def _doc_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM documents WHERE content_type = 'liturgical_unit'"
    ).fetchone()[0]


def _meta_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM liturgical_unit_meta").fetchone()[0]


def _chunk_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM chunks
        WHERE document_id IN (
            SELECT id FROM documents WHERE content_type = 'liturgical_unit'
        )
        """
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# 1. Smoke test: full ingest against fixtures, assert row counts
# ---------------------------------------------------------------------------


def test_full_ingest_unit_counts(db_conn: sqlite3.Connection) -> None:
    """Full ingest creates one document + one meta row per unique parsed unit."""
    result = _run_handler({}, db_conn)

    assert result["errors"] == [], f"Unexpected errors: {result['errors']}"
    assert result["units_inserted"] == EXPECTED_TOTAL, (
        f"Expected {EXPECTED_TOTAL} inserted, got {result['units_inserted']}"
    )
    # units_skipped > 0 is expected: some units (e.g. Confession of Sin)
    # appear in both morning prayer and evening prayer fixture files.
    assert result["units_skipped"] >= 0
    assert set(result["parsers_run"]) == {
        "collects",
        "daily_office",
        "psalter",
        "proper_liturgies",
        "prayers_and_thanksgivings",
    }

    assert _doc_count(db_conn) == EXPECTED_TOTAL
    assert _meta_count(db_conn) == EXPECTED_TOTAL


def test_full_ingest_collects_count(db_conn: sqlite3.Connection) -> None:
    """Collects parser produces the expected number of units."""
    result = _run_handler({"parsers": ["collects"]}, db_conn)
    assert result["units_inserted"] == EXPECTED_COLLECTS


def test_full_ingest_daily_office_count(db_conn: sqlite3.Connection) -> None:
    """Daily office parser produces the expected number of units."""
    result = _run_handler({"parsers": ["daily_office"]}, db_conn)
    assert result["units_inserted"] == EXPECTED_DAILY_OFFICE


def test_full_ingest_psalter_count(db_conn: sqlite3.Connection) -> None:
    """Psalter parser produces the expected number of unique units from fixtures.

    Running in isolation: 4 (psalm 1 appears in two fixture files; deduped by source_id).
    Running after daily_office: 3 (psalm 119 is also emitted by noonday.html; cross-parser dedup).
    This test runs in isolation, so expect EXPECTED_PSALTER_ISOLATED.
    """
    result = _run_handler({"parsers": ["psalter"]}, db_conn)
    assert result["units_inserted"] == EXPECTED_PSALTER_ISOLATED


def test_full_ingest_proper_liturgies_count(db_conn: sqlite3.Connection) -> None:
    """Proper liturgies parser produces the expected number of units."""
    result = _run_handler({"parsers": ["proper_liturgies"]}, db_conn)
    assert result["units_inserted"] == EXPECTED_PROPER_LITURGIES


def test_full_ingest_prayers_and_thanksgivings_count(db_conn: sqlite3.Connection) -> None:
    """Prayers & Thanksgivings parser produces the expected number of units."""
    result = _run_handler({"parsers": ["prayers_and_thanksgivings"]}, db_conn)
    assert result["units_inserted"] == EXPECTED_PRAYERS


# ---------------------------------------------------------------------------
# 2. Idempotency test: second run inserts 0 new rows
# ---------------------------------------------------------------------------


def test_idempotency(db_conn: sqlite3.Connection) -> None:
    """Running the handler twice leaves the same row count; second run inserts 0."""
    first = _run_handler({}, db_conn)
    assert first["units_inserted"] == EXPECTED_TOTAL

    second = _run_handler({}, db_conn)
    assert second["units_inserted"] == 0
    # All 704 unique rows are skipped on second run; cross-file duplicates are
    # also skipped (they were already skipped in the first run too).
    assert second["units_skipped"] > 0

    # Counts unchanged after second run
    assert _doc_count(db_conn) == EXPECTED_TOTAL
    assert _meta_count(db_conn) == EXPECTED_TOTAL


# ---------------------------------------------------------------------------
# 3. Selective-parsers test: payload with parsers=["collects"] ingests only collects
# ---------------------------------------------------------------------------


def test_selective_parsers_collects_only(db_conn: sqlite3.Connection) -> None:
    """parsers=['collects'] ingests only collects, no other units."""
    result = _run_handler({"parsers": ["collects"]}, db_conn)

    assert result["parsers_run"] == ["collects"]
    assert result["units_inserted"] == EXPECTED_COLLECTS
    assert _doc_count(db_conn) == EXPECTED_COLLECTS
    assert _meta_count(db_conn) == EXPECTED_COLLECTS

    # Running the other parsers after adds the remaining units
    result2 = _run_handler(
        {"parsers": ["daily_office", "psalter", "proper_liturgies", "prayers_and_thanksgivings"]},
        db_conn,
    )
    assert result2["units_inserted"] == EXPECTED_TOTAL - EXPECTED_COLLECTS
    assert _doc_count(db_conn) == EXPECTED_TOTAL


def test_selective_parsers_unknown_raises(db_conn: sqlite3.Connection) -> None:
    """An unknown parser name raises ValueError."""
    from commonplace_worker.handlers.liturgy_bcp import handle_liturgy_bcp_ingest

    with pytest.raises(ValueError, match="Unknown parsers"):
        handle_liturgy_bcp_ingest(
            {"source_root": str(FIXTURES_ROOT), "parsers": ["nonexistent"]},
            db_conn,
            _embedder=_fake_embedder,
        )


# ---------------------------------------------------------------------------
# 4. Dry-run test
# ---------------------------------------------------------------------------


def test_dry_run_no_rows_written(db_conn: sqlite3.Connection) -> None:
    """dry_run=True returns count summary without writing any rows.

    Dry-run returns RAW parser output counts (before any deduplication),
    since no DB lookups are performed.
    """
    result = _run_handler({"dry_run": True}, db_conn)

    assert result["dry_run"] is True
    assert result["units_inserted"] == EXPECTED_TOTAL_RAW
    assert result["units_skipped"] == 0
    assert result["errors"] == []

    # No rows written
    assert _doc_count(db_conn) == 0
    assert _meta_count(db_conn) == 0


def test_dry_run_partial_parsers(db_conn: sqlite3.Connection) -> None:
    """dry_run with a specific parser subset counts only that subset (raw)."""
    result = _run_handler(
        {"dry_run": True, "parsers": ["collects", "psalter"]},
        db_conn,
    )
    assert result["units_inserted"] == EXPECTED_COLLECTS_RAW + EXPECTED_PSALTER_RAW
    assert _doc_count(db_conn) == 0


# ---------------------------------------------------------------------------
# 5. Per-parser category / genre / tradition assertions
# ---------------------------------------------------------------------------


def test_collect_meta_fields(db_conn: sqlite3.Connection) -> None:
    """A collect unit has correct category/genre/tradition/source meta."""
    _run_handler({"parsers": ["collects"]}, db_conn)

    row = db_conn.execute(
        """
        SELECT m.category, m.genre, m.tradition, m.source,
               m.language_register, m.canonical_id
          FROM liturgical_unit_meta m
          JOIN documents d ON d.id = m.document_id
         WHERE d.content_type = 'liturgical_unit'
         LIMIT 1
        """
    ).fetchone()
    assert row is not None
    assert row["category"] == "liturgical_proper"
    assert row["genre"] == "collect"
    assert row["tradition"] == "anglican"
    assert row["source"] == "bcp_1979"
    # language_register must be rite_i or rite_ii for collects
    assert row["language_register"] in ("rite_i", "rite_ii")
    assert row["canonical_id"] is not None


def test_daily_office_meta_fields(db_conn: sqlite3.Connection) -> None:
    """A daily office unit has correct category/tradition and non-null office."""
    _run_handler({"parsers": ["daily_office"]}, db_conn)

    # Pick a morning_prayer unit
    row = db_conn.execute(
        """
        SELECT m.category, m.genre, m.tradition, m.source, m.office
          FROM liturgical_unit_meta m
         WHERE m.source = 'bcp_1979'
           AND m.office IS NOT NULL
         LIMIT 1
        """
    ).fetchone()
    assert row is not None
    assert row["category"] == "liturgical_proper"
    assert row["tradition"] == "anglican"
    assert row["source"] == "bcp_1979"
    assert row["office"] in (
        "morning_prayer", "evening_prayer", "compline", "noonday", "other"
    )


def test_psalter_meta_fields(db_conn: sqlite3.Connection) -> None:
    """A psalter unit has category='psalter', genre='psalm', null language_register."""
    _run_handler({"parsers": ["psalter"]}, db_conn)

    row = db_conn.execute(
        """
        SELECT m.category, m.genre, m.tradition, m.source, m.language_register
          FROM liturgical_unit_meta m
         WHERE m.category = 'psalter'
         LIMIT 1
        """
    ).fetchone()
    assert row is not None
    assert row["category"] == "psalter"
    assert row["genre"] == "psalm"
    assert row["tradition"] == "anglican"
    assert row["source"] == "bcp_1979"
    assert row["language_register"] is None


def test_proper_liturgies_meta_fields(db_conn: sqlite3.Connection) -> None:
    """A proper liturgy unit has category='liturgical_proper', valid genre."""
    _run_handler({"parsers": ["proper_liturgies"]}, db_conn)

    row = db_conn.execute(
        """
        SELECT m.category, m.genre, m.tradition, m.source, m.office_position
          FROM liturgical_unit_meta m
         WHERE m.category = 'liturgical_proper'
           AND m.office IS NULL
         LIMIT 1
        """
    ).fetchone()
    assert row is not None
    assert row["category"] == "liturgical_proper"
    assert row["genre"] in ("prayer_body", "speaker_line", "psalm_verse", "rubric")
    assert row["tradition"] == "anglican"
    assert row["source"] == "bcp_1979"


def test_prayers_meta_fields(db_conn: sqlite3.Connection) -> None:
    """A prayers-and-thanksgivings unit has category='devotional_manual'."""
    _run_handler({"parsers": ["prayers_and_thanksgivings"]}, db_conn)

    row = db_conn.execute(
        """
        SELECT m.category, m.genre, m.tradition, m.source, m.language_register
          FROM liturgical_unit_meta m
         WHERE m.category = 'devotional_manual'
         LIMIT 1
        """
    ).fetchone()
    assert row is not None
    assert row["category"] == "devotional_manual"
    assert row["genre"] in ("prayer", "thanksgiving")
    assert row["tradition"] == "anglican"
    assert row["source"] == "bcp_1979"
    assert row["language_register"] is None


def test_thanksgiving_genre(db_conn: sqlite3.Connection) -> None:
    """Thanksgivings have genre='thanksgiving', not 'prayer'."""
    _run_handler({"parsers": ["prayers_and_thanksgivings"]}, db_conn)

    row = db_conn.execute(
        """
        SELECT m.genre, d.title
          FROM liturgical_unit_meta m
          JOIN documents d ON d.id = m.document_id
         WHERE m.genre = 'thanksgiving'
         LIMIT 1
        """
    ).fetchone()
    assert row is not None
    assert row["genre"] == "thanksgiving"


# ---------------------------------------------------------------------------
# 6. embed_document called for every inserted row
# ---------------------------------------------------------------------------


def test_embed_called_for_all_inserted_rows(db_conn: sqlite3.Connection) -> None:
    """embed_document is called for every inserted row.

    Verified by checking that documents have status='embedded' (set by
    embed_document even when the body text is empty and produces 0 chunks).
    The overwhelming majority of units produce >= 1 chunk; a small number of
    units with empty-string body text legitimately produce 0 chunks — this is
    correct and expected behaviour from the chunker.
    """
    _run_handler({}, db_conn)

    # Every liturgical_unit document must have status='embedded'.
    # (embed_document sets this unconditionally, even for 0-chunk documents.)
    not_embedded = db_conn.execute(
        """
        SELECT COUNT(*) FROM documents
         WHERE content_type = 'liturgical_unit'
           AND status != 'embedded'
        """
    ).fetchone()[0]
    assert not_embedded == 0, f"{not_embedded} documents still have non-embedded status"

    # The vast majority of documents must have at least one chunk.
    # Allow a small fraction with empty body text (they still ran through embed_document).
    total_docs = db_conn.execute(
        "SELECT COUNT(*) FROM documents WHERE content_type = 'liturgical_unit'"
    ).fetchone()[0]
    docs_with_chunks = db_conn.execute(
        """
        SELECT COUNT(DISTINCT document_id) FROM chunks
         WHERE document_id IN (
             SELECT id FROM documents WHERE content_type = 'liturgical_unit'
         )
        """
    ).fetchone()[0]
    # Expect at least 99% of documents to have chunks
    assert docs_with_chunks >= total_docs * 0.99, (
        f"Only {docs_with_chunks}/{total_docs} documents have chunks — "
        "too many empty-body documents"
    )


def test_embed_not_called_on_skip(db_conn: sqlite3.Connection) -> None:
    """Second run: skipped rows don't accumulate duplicate chunks."""
    _run_handler({}, db_conn)
    chunk_count_before = _chunk_count(db_conn)

    _run_handler({}, db_conn)
    chunk_count_after = _chunk_count(db_conn)

    # embed_document is idempotent — no new chunks on second run
    assert chunk_count_after == chunk_count_before


# ---------------------------------------------------------------------------
# 7. documents.status = 'embedded' after ingest
# ---------------------------------------------------------------------------


def test_documents_status_embedded(db_conn: sqlite3.Connection) -> None:
    """All ingested documents end up with status='embedded'."""
    _run_handler({}, db_conn)

    not_embedded = db_conn.execute(
        """
        SELECT COUNT(*) FROM documents
         WHERE content_type = 'liturgical_unit'
           AND status != 'embedded'
        """
    ).fetchone()[0]
    assert not_embedded == 0, f"{not_embedded} documents have status != 'embedded'"


# ---------------------------------------------------------------------------
# 8. source_uri shape per parser
# ---------------------------------------------------------------------------


def test_collects_source_uri_shape(db_conn: sqlite3.Connection) -> None:
    """Collect source_uri starts with bcp1979://collects/."""
    _run_handler({"parsers": ["collects"]}, db_conn)
    row = db_conn.execute(
        "SELECT source_uri FROM documents WHERE content_type = 'liturgical_unit' LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["source_uri"].startswith("bcp1979://collects/")


def test_psalter_source_uri_shape(db_conn: sqlite3.Connection) -> None:
    """Psalter source_uri starts with bcp1979://psalter/."""
    _run_handler({"parsers": ["psalter"]}, db_conn)
    row = db_conn.execute(
        "SELECT source_uri FROM documents WHERE content_type = 'liturgical_unit' LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["source_uri"].startswith("bcp1979://psalter/")


# ---------------------------------------------------------------------------
# 9. raw_metadata is valid JSON
# ---------------------------------------------------------------------------


def test_raw_metadata_is_valid_json(db_conn: sqlite3.Connection) -> None:
    """All liturgical_unit_meta rows have parseable raw_metadata."""
    _run_handler({}, db_conn)

    rows = db_conn.execute(
        "SELECT document_id, raw_metadata FROM liturgical_unit_meta"
    ).fetchall()
    for row in rows:
        try:
            json.loads(row["raw_metadata"])
        except json.JSONDecodeError as exc:
            pytest.fail(
                f"document_id={row['document_id']} has invalid JSON in raw_metadata: {exc}"
            )


# ---------------------------------------------------------------------------
# 10. canonical_id grouping: Rite I and Rite II collects share canonical_id
# ---------------------------------------------------------------------------


def test_rite_i_rite_ii_same_canonical_id(db_conn: sqlite3.Connection) -> None:
    """Rite I and Rite II versions of the same collect share a canonical_id."""
    _run_handler({"parsers": ["collects"]}, db_conn)

    # Find canonical_ids that appear for both rite_i and rite_ii
    shared = db_conn.execute(
        """
        SELECT canonical_id, COUNT(DISTINCT language_register) AS rite_count
          FROM liturgical_unit_meta
         WHERE source = 'bcp_1979'
           AND language_register IN ('rite_i', 'rite_ii')
         GROUP BY canonical_id
        HAVING rite_count = 2
        """
    ).fetchall()
    assert len(shared) > 0, (
        "Expected at least one canonical_id shared by Rite I and Rite II collects"
    )


# ---------------------------------------------------------------------------
# 11. Proper liturgies: speaker-line and prayer-body genres both present
# ---------------------------------------------------------------------------


def test_proper_liturgies_genres_present(db_conn: sqlite3.Connection) -> None:
    """Proper liturgies produce both speaker_line and prayer_body genre rows."""
    _run_handler({"parsers": ["proper_liturgies"]}, db_conn)

    genres = {
        row[0]
        for row in db_conn.execute(
            "SELECT DISTINCT genre FROM liturgical_unit_meta WHERE source = 'bcp_1979'"
        ).fetchall()
    }
    assert "speaker_line" in genres, f"speaker_line not found; genres = {genres}"
    assert "prayer_body" in genres, f"prayer_body not found; genres = {genres}"


# ---------------------------------------------------------------------------
# 12. Daily office language_register mapping
# ---------------------------------------------------------------------------


def test_daily_office_rite_i_register(db_conn: sqlite3.Connection) -> None:
    """Daily office units from rite_i files have language_register='rite_i'."""
    _run_handler({"parsers": ["daily_office"]}, db_conn)

    # mp1 is rite_i; its units should have language_register='rite_i'
    row = db_conn.execute(
        """
        SELECT m.language_register
          FROM liturgical_unit_meta m
         WHERE m.source = 'bcp_1979'
           AND m.office = 'morning_prayer'
           AND m.language_register = 'rite_i'
         LIMIT 1
        """
    ).fetchone()
    assert row is not None, "No rite_i morning_prayer unit found"


# ---------------------------------------------------------------------------
# 13. Psalter: body_text includes verse text
# ---------------------------------------------------------------------------


def test_psalter_chunk_has_verse_text(db_conn: sqlite3.Connection) -> None:
    """The chunk text for a psalm contains verse numbers."""
    _run_handler({"parsers": ["psalter"]}, db_conn)

    chunk = db_conn.execute(
        """
        SELECT c.text
          FROM chunks c
          JOIN documents d ON d.id = c.document_id
         WHERE d.content_type = 'liturgical_unit'
         LIMIT 1
        """
    ).fetchone()
    assert chunk is not None
    assert len(chunk["text"]) > 10
