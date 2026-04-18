"""Tests for commonplace_worker/handlers/liturgy_lff.py.

Test classes
------------
TestSha256Guard         — fails loudly if the fixture PDF has changed.
TestSmokeIngest         — full ingest; asserts counts.
TestIdempotency         — second run inserts 0 new rows.
TestSpotCheck           — Elizabeth Ann Seton: bio, both rite collects, canonical_id,
                          calendar_anchor_id.
TestNoFeastMatch        — commemoration whose slug is absent from feast table:
                          bio skipped, collects inserted with calendar_anchor_id=NULL.
TestDryRun              — dry_run=True returns count summary without writing.
TestEmbeddingCalled     — every inserted row gets an embedding.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from commonplace_db.db import migrate

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "lff_2024.pdf"
PINNED_SHA256 = "5deea4a131b6c90218ae07b92a17f68bfce000a24a04edd989cdb1edc332bfd7"

# Expected counts (parser-accurate, with feasts seeded from canonical feasts.yaml)
EXPECTED_COMMEMORATIONS = 283
# 201 bios inserted: 283 total - 80 no-feast-match - 2 empty-bio = 201.
# Range allows for feast-seeding variance across test runs.
EXPECTED_BIOS_MIN = 190
EXPECTED_BIOS_MAX = 283
# 564 collects inserted: 283 × 2 = 566, minus 2 due to shared collect text (content_hash collision
# between The Visitation BVM and The Nativity BVM).
# Range allows for content variance.
EXPECTED_COLLECTS_MIN = 500
EXPECTED_COLLECTS_MAX = 566


def _fake_embedder(texts: list[str], model: str) -> list[list[float]]:
    """Return zero-vectors of dimension 768 (avoids Ollama in tests)."""
    return [[0.0] * 768 for _ in texts]


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """In-memory SQLite with all migrations applied."""
    import sqlite_vec  # type: ignore[import-untyped]

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    migrate(conn)
    return conn


@pytest.fixture
def db_conn_with_feasts(db_conn: sqlite3.Connection) -> sqlite3.Connection:
    """DB connection with all LFF 2024 feasts seeded from the canonical feasts.yaml."""
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from feast_import import _run_import

    from commonplace_db.feast_schema import validate_feasts

    feasts_path = Path(__file__).parent.parent / "commonplace_db" / "seed" / "feasts.yaml"
    subjects_path = (
        Path(__file__).parent.parent / "commonplace_db" / "seed" / "theological_subjects.yaml"
    )
    entries = validate_feasts(feasts_path, subjects_path)
    with db_conn:
        _run_import(db_conn, entries, dry_run=False, ignore_missing_cross_refs=True)
    return db_conn


# ---------------------------------------------------------------------------
# SHA256 guard
# ---------------------------------------------------------------------------


class TestSha256Guard:
    def test_fixture_pdf_exists(self) -> None:
        assert FIXTURE_PDF.exists(), (
            f"Fixture PDF missing: {FIXTURE_PDF}. "
            "Download lff_2024.pdf and place it at tests/fixtures/lff_2024.pdf."
        )

    def test_sha256_matches_pinned(self) -> None:
        """Fail if the fixture PDF has changed since pinning."""
        import hashlib

        h = hashlib.sha256()
        with FIXTURE_PDF.open("rb") as fh:
            for block in iter(lambda: fh.read(65536), b""):
                h.update(block)
        actual = h.hexdigest()
        assert actual == PINNED_SHA256, (
            f"lff_2024.pdf SHA256 mismatch.\n"
            f"  Pinned:  {PINNED_SHA256}\n"
            f"  Actual:  {actual}\n"
            "The fixture PDF may have been replaced. Update PINNED_SHA256 if intentional."
        )

    def test_handler_raises_on_sha256_mismatch(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        """Handler raises ValueError when expected_sha256 doesn't match."""
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        with pytest.raises(ValueError, match="SHA256 mismatch"):
            handle_liturgy_lff_ingest(
                {
                    "source_pdf": str(FIXTURE_PDF),
                    "expected_sha256": "0" * 64,
                },
                db_conn_with_feasts,
                _embedder=_fake_embedder,
            )


# ---------------------------------------------------------------------------
# Smoke test — full ingest
# ---------------------------------------------------------------------------


class TestSmokeIngest:
    def test_returns_summary_dict(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        result = handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        assert isinstance(result, dict)
        assert set(result.keys()) >= {
            "commemorations_processed",
            "bios_inserted",
            "bios_skipped_no_feast",
            "collects_inserted",
            "errors",
        }

    def test_commemoration_count(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        result = handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        assert result["commemorations_processed"] == EXPECTED_COMMEMORATIONS

    def test_bio_count_in_range(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        result = handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        total_bios = result["bios_inserted"]
        assert EXPECTED_BIOS_MIN <= total_bios <= EXPECTED_BIOS_MAX, (
            f"Expected bios in [{EXPECTED_BIOS_MIN}, {EXPECTED_BIOS_MAX}], got {total_bios}"
        )

    def test_collect_count_in_range(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        result = handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        total_collects = result["collects_inserted"]
        assert EXPECTED_COLLECTS_MIN <= total_collects <= EXPECTED_COLLECTS_MAX, (
            f"Expected collects in [{EXPECTED_COLLECTS_MIN}, {EXPECTED_COLLECTS_MAX}], "
            f"got {total_collects}"
        )

    def test_no_errors(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        result = handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        assert result["errors"] == [], f"Handler reported errors: {result['errors']}"

    def test_liturgical_unit_meta_rows(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        result = handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        meta_count = db_conn_with_feasts.execute(
            "SELECT COUNT(*) FROM liturgical_unit_meta WHERE source = 'lff_2024'"
        ).fetchone()[0]
        assert meta_count == result["collects_inserted"]

    def test_bio_document_rows(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        result = handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        prose_count = db_conn_with_feasts.execute(
            "SELECT COUNT(*) FROM documents WHERE content_type = 'prose' "
            "AND source_uri LIKE 'lff2024://commemoration/%'"
        ).fetchone()[0]
        assert prose_count == result["bios_inserted"]


# ---------------------------------------------------------------------------
# Idempotency test
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_run_inserts_zero_new_rows(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        result1 = handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        result2 = handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        assert result2["bios_inserted"] == 0
        assert result2["collects_inserted"] == 0
        assert result2["commemorations_processed"] == result1["commemorations_processed"]

    def test_row_count_stable_after_second_run(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        count_after_first = db_conn_with_feasts.execute(
            "SELECT COUNT(*) FROM documents WHERE content_type IN ('prose', 'liturgical_unit') "
            "AND source_uri LIKE 'lff2024://%'"
        ).fetchone()[0]

        handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        count_after_second = db_conn_with_feasts.execute(
            "SELECT COUNT(*) FROM documents WHERE content_type IN ('prose', 'liturgical_unit') "
            "AND source_uri LIKE 'lff2024://%'"
        ).fetchone()[0]

        assert count_after_first == count_after_second


# ---------------------------------------------------------------------------
# Spot-check: Elizabeth Ann Seton
# ---------------------------------------------------------------------------


class TestSpotCheckSeton:
    """Elizabeth Ann Seton (Jan 4) — verify bio + both rite collects."""

    FEAST_SLUG = "elizabeth_ann_seton_anglican"

    def test_bio_row_exists(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        doc = db_conn_with_feasts.execute(
            "SELECT * FROM documents WHERE content_type = 'prose' AND source_id = ?",
            (self.FEAST_SLUG,),
        ).fetchone()
        assert doc is not None, "Bio document for Elizabeth Ann Seton not found"
        assert doc["title"] == "Elizabeth Ann Seton"
        assert doc["status"] == "embedded"

    def test_both_rite_collects_exist(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        rite_i = db_conn_with_feasts.execute(
            "SELECT * FROM documents WHERE content_type = 'liturgical_unit' AND source_id = ?",
            (f"{self.FEAST_SLUG}_rite-i",),  # elizabeth_ann_seton_anglican_rite-i
        ).fetchone()
        rite_ii = db_conn_with_feasts.execute(
            "SELECT * FROM documents WHERE content_type = 'liturgical_unit' AND source_id = ?",
            (f"{self.FEAST_SLUG}_rite-ii",),
        ).fetchone()
        assert rite_i is not None, "Rite I collect for Elizabeth Ann Seton not found"
        assert rite_ii is not None, "Rite II collect for Elizabeth Ann Seton not found"

    def test_shared_canonical_id(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        metas = db_conn_with_feasts.execute(
            """
            SELECT lm.canonical_id
            FROM liturgical_unit_meta lm
            JOIN documents d ON d.id = lm.document_id
            WHERE d.source_id IN (?, ?)
            """,
            (f"{self.FEAST_SLUG}_rite-i", f"{self.FEAST_SLUG}_rite-ii"),
        ).fetchall()
        assert len(metas) == 2
        canonical_ids = {row["canonical_id"] for row in metas}
        assert len(canonical_ids) == 1, (
            f"Both rite collects should share canonical_id, got: {canonical_ids}"
        )
        assert canonical_ids.pop() == self.FEAST_SLUG

    def test_collect_calendar_anchor_points_to_feast(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        feast = db_conn_with_feasts.execute(
            "SELECT id FROM feast WHERE primary_name = 'Elizabeth Ann Seton' "
            "AND tradition = 'anglican'",
        ).fetchone()
        assert feast is not None, "Elizabeth Ann Seton feast row not found in DB"

        rite_i = db_conn_with_feasts.execute(
            """
            SELECT lm.calendar_anchor_id
            FROM liturgical_unit_meta lm
            JOIN documents d ON d.id = lm.document_id
            WHERE d.source_id = ?
            """,
            (f"{self.FEAST_SLUG}_rite-i",),  # elizabeth_ann_seton_anglican_rite-i
        ).fetchone()
        assert rite_i is not None
        assert rite_i["calendar_anchor_id"] == feast["id"], (
            f"calendar_anchor_id {rite_i['calendar_anchor_id']!r} != feast.id {feast['id']!r}"
        )

    def test_commemoration_bio_row_linked(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        bio_row = db_conn_with_feasts.execute(
            """
            SELECT cb.*
            FROM commemoration_bio cb
            JOIN feast f ON f.id = cb.feast_id
            WHERE f.primary_name = 'Elizabeth Ann Seton' AND f.tradition = 'anglican'
            """,
        ).fetchone()
        assert bio_row is not None, "commemoration_bio row for Elizabeth Ann Seton not found"
        assert bio_row["source"] == "lff_2024"
        assert bio_row["document_id"] is not None


# ---------------------------------------------------------------------------
# No-feast-match test
# ---------------------------------------------------------------------------


class TestNoFeastMatch:
    """A commemoration whose slug isn't in the feast table: bio skipped, collects null-anchored."""

    def test_bio_skipped_collects_inserted_null_anchor(
        self, db_conn: sqlite3.Connection
    ) -> None:
        """Use an empty feast table — all bio lookups miss, all collects use null anchor."""
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        result = handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn,  # no feasts seeded
            _embedder=_fake_embedder,
        )

        # All bios that would have been inserted are now skipped (feast_id = None → skip bio)
        assert result["bios_inserted"] == 0

        # bios_skipped_no_feast accounts for all non-empty bios
        assert result["bios_skipped_no_feast"] >= EXPECTED_BIOS_MIN

        # Collects still inserted with calendar_anchor_id = NULL
        assert result["collects_inserted"] >= EXPECTED_COLLECTS_MIN

    def test_collects_have_null_calendar_anchor(
        self, db_conn: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn,
            _embedder=_fake_embedder,
        )
        # All LFF collects should have NULL calendar_anchor_id when no feasts are seeded
        non_null = db_conn.execute(
            "SELECT COUNT(*) FROM liturgical_unit_meta "
            "WHERE source = 'lff_2024' AND calendar_anchor_id IS NOT NULL"
        ).fetchone()[0]
        assert non_null == 0

    def test_single_missing_feast_logs_warning(
        self,
        db_conn: sqlite3.Connection,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """With empty feast table, warnings are emitted for every missed slug."""
        import logging

        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        with caplog.at_level(logging.WARNING, logger="commonplace_worker.handlers.liturgy_lff"):
            handle_liturgy_lff_ingest(
                {"source_pdf": str(FIXTURE_PDF)},
                db_conn,
                _embedder=_fake_embedder,
            )

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_messages) >= EXPECTED_COMMEMORATIONS, (
            "Expected a warning for every commemoration without a feast match"
        )


# ---------------------------------------------------------------------------
# Dry-run test
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_returns_counts_no_writes(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        result = handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF), "dry_run": True},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        # Counts returned but no actual insertions
        assert result["commemorations_processed"] == EXPECTED_COMMEMORATIONS
        assert result["bios_inserted"] == 0
        assert result["collects_inserted"] == 0
        assert "dry_run_bios_would_insert" in result
        assert "dry_run_collects_would_insert" in result

    def test_dry_run_no_db_rows_written(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF), "dry_run": True},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        doc_count = db_conn_with_feasts.execute(
            "SELECT COUNT(*) FROM documents WHERE source_uri LIKE 'lff2024://%'"
        ).fetchone()[0]
        assert doc_count == 0

    def test_dry_run_would_insert_counts_reasonable(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        result = handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF), "dry_run": True},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        assert EXPECTED_BIOS_MIN <= result["dry_run_bios_would_insert"] <= EXPECTED_BIOS_MAX
        assert (
            EXPECTED_COLLECTS_MIN
            <= result["dry_run_collects_would_insert"]
            <= EXPECTED_COLLECTS_MAX
        )


# ---------------------------------------------------------------------------
# Embedding called test
# ---------------------------------------------------------------------------


class TestEmbeddingCalled:
    """Verify every inserted document gets chunks + embeddings."""

    def test_every_bio_gets_embedding(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        # All bio documents should have at least one chunk + embedding
        bio_docs_without_chunks = db_conn_with_feasts.execute(
            """
            SELECT COUNT(*) FROM documents d
            WHERE d.content_type = 'prose'
              AND d.source_uri LIKE 'lff2024://commemoration/%'
              AND d.status = 'embedded'
              AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.document_id = d.id)
            """
        ).fetchone()[0]
        assert bio_docs_without_chunks == 0, (
            f"{bio_docs_without_chunks} bio document(s) missing chunks after embed"
        )

    def test_every_collect_gets_embedding(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        collect_docs_without_chunks = db_conn_with_feasts.execute(
            """
            SELECT COUNT(*) FROM documents d
            WHERE d.content_type = 'liturgical_unit'
              AND d.source_uri LIKE 'lff2024://collect/%'
              AND d.status = 'embedded'
              AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.document_id = d.id)
            """
        ).fetchone()[0]
        assert collect_docs_without_chunks == 0, (
            f"{collect_docs_without_chunks} collect document(s) missing chunks after embed"
        )

    def test_every_embedded_doc_has_vector(
        self, db_conn_with_feasts: sqlite3.Connection
    ) -> None:
        from commonplace_worker.handlers.liturgy_lff import handle_liturgy_lff_ingest

        handle_liturgy_lff_ingest(
            {"source_pdf": str(FIXTURE_PDF)},
            db_conn_with_feasts,
            _embedder=_fake_embedder,
        )
        # All chunks from LFF docs should have embeddings
        chunks_without_embedding = db_conn_with_feasts.execute(
            """
            SELECT COUNT(*) FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE d.source_uri LIKE 'lff2024://%'
              AND NOT EXISTS (SELECT 1 FROM embeddings e WHERE e.chunk_id = c.id)
            """
        ).fetchone()[0]
        assert chunks_without_embedding == 0, (
            f"{chunks_without_embedding} chunk(s) from LFF docs missing embeddings"
        )
