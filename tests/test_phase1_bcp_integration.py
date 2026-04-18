"""Phase 1 BCP 1979 end-to-end integration test (task 1.9).

Exercises the full liturgical-ingest pipeline:
    INSERT job → poll_once (worker claims) → handler runs →
    documents + liturgical_unit_meta + embedding rows populate →
    search_commonplace returns units.

Count note: the DoD in §8.7 targets "≈ 600 ±5%" against a future
production BCP corpus.  Against the committed fixtures the handler
inserts exactly 704 unique units (275 collects + 118 daily_office +
3 psalter + 227 proper_liturgies + 81 prayers_and_thanksgivings).
The psalter fixture ships only 3 sample files, not all ~150 psalms.
We assert the fixture-determined count (704) rather than the DoD
estimate so the test is deterministic.

Source-filter note: the DoD text says ``source='bcp_1979'`` but that
parameter is a LIKE substring match on ``documents.source_uri`` (not
``liturgical_unit_meta.source``).  BCP URIs use ``bcp1979://`` (no
underscore), so we pass ``source="bcp1979"`` to search_commonplace.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import commonplace_db
from commonplace_server.server import search_commonplace
from commonplace_worker.worker import HANDLERS, poll_once

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "bcp_1979"

# Unique units the handler inserts from the committed fixtures (see module
# docstring for breakdown).
EXPECTED_UNIT_COUNT = 704


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def bcp_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[sqlite3.Connection, str]:
    """Point worker + search_commonplace at the same isolated tmp DB."""
    db_file = str(tmp_path / "phase1.db")

    # The worker's handler wrapper calls commonplace_db.connect() with NO args,
    # which falls back to the module-level DB_PATH constant (resolved at import
    # time, NOT re-read from the env var on every call).  We must patch both:
    #   1. The env var — for search_commonplace (reads os.environ each call).
    #   2. commonplace_db.db.DB_PATH — for connect() with no args.
    monkeypatch.setenv("COMMONPLACE_DB_PATH", db_file)
    import commonplace_db.db as _db_mod
    monkeypatch.setattr(_db_mod, "DB_PATH", db_file)

    # Patch embed so no Ollama instance is required in CI.
    # commonplace_server.embedding.embed is called by:
    #   - the handler's pipeline (embed_document → embed)
    #   - search_commonplace (to embed the query vector)
    monkeypatch.setattr(
        "commonplace_server.embedding.embed",
        lambda texts, *a, **kw: [[0.0] * 768 for _ in texts],
    )

    conn = commonplace_db.connect(db_file)
    commonplace_db.migrate(conn)
    return conn, db_file


# ---------------------------------------------------------------------------
# Helper: insert a queued ingest_liturgy_bcp job and return its id
# ---------------------------------------------------------------------------


def _enqueue(conn: sqlite3.Connection) -> int:
    """Insert a queued ingest_liturgy_bcp job; return the new row id."""
    cur = conn.execute(
        "INSERT INTO job_queue (kind, payload, status) VALUES (?, ?, ?)",
        (
            "ingest_liturgy_bcp",
            json.dumps({"source_root": str(FIXTURES_ROOT)}),
            "queued",
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Test 1: DoD gate — full pipeline end-to-end
# ---------------------------------------------------------------------------


def test_phase1_bcp_ingest_end_to_end(
    bcp_env: tuple[sqlite3.Connection, str],
) -> None:
    """Submit an ingest_liturgy_bcp job and verify the DoD criteria."""
    conn, _db_file = bcp_env

    job_id = _enqueue(conn)

    # Worker claims and processes the job.
    processed = poll_once(conn, HANDLERS)
    assert processed == 1, "expected exactly one job to be processed"

    # Job row should be complete with no error.
    row = conn.execute(
        "SELECT status, error, completed_at FROM job_queue WHERE id = ?",
        (job_id,),
    ).fetchone()
    assert row is not None
    assert row["status"] == "complete", f"unexpected job status: {dict(row)}"
    assert row["error"] is None, f"job recorded an error: {row['error']}"
    assert row["completed_at"] is not None

    # liturgical_unit_meta count
    meta_count = conn.execute(
        "SELECT COUNT(*) FROM liturgical_unit_meta WHERE source = 'bcp_1979'",
    ).fetchone()[0]
    assert meta_count == EXPECTED_UNIT_COUNT, (
        f"liturgical_unit_meta count {meta_count} != expected {EXPECTED_UNIT_COUNT}"
    )

    # documents count should match 1:1
    doc_count = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE content_type = 'liturgical_unit'",
    ).fetchone()[0]
    assert doc_count == meta_count, (
        f"documents count {doc_count} != meta count {meta_count} (should be 1:1)"
    )

    # search_commonplace returns plausible results.
    # Note: the DoD text uses source='bcp_1979' but that's a substring match
    # on source_uri.  BCP URIs are bcp1979://… (no underscore), so we use
    # source="bcp1979".
    results = search_commonplace(
        query="grace and mercy",
        content_type="liturgical_unit",
        source="bcp1979",
        limit=3,
    )

    assert len(results["results"]) == 3, (
        f"expected 3 search results, got {len(results['results'])}: {results}"
    )
    for r in results["results"]:
        assert r["content_type"] == "liturgical_unit", (
            f"unexpected content_type: {r['content_type']}"
        )
        assert r["source_uri"].startswith("bcp1979://"), (
            f"unexpected source_uri: {r['source_uri']}"
        )
        assert r["chunk_text"], f"empty chunk_text for result: {r}"


# ---------------------------------------------------------------------------
# Test 2: Idempotency — re-running the handler on a pre-populated DB
# ---------------------------------------------------------------------------


def test_phase1_bcp_ingest_idempotent(
    bcp_env: tuple[sqlite3.Connection, str],
) -> None:
    """Re-submitting the same job must not create duplicate rows."""
    conn, _db_file = bcp_env

    # First run.
    _enqueue(conn)
    processed = poll_once(conn, HANDLERS)
    assert processed == 1

    count_after_first = conn.execute(
        "SELECT COUNT(*) FROM liturgical_unit_meta",
    ).fetchone()[0]
    assert count_after_first == EXPECTED_UNIT_COUNT

    # Second run — same source, different job row.
    _enqueue(conn)
    processed2 = poll_once(conn, HANDLERS)
    assert processed2 == 1

    count_after_second = conn.execute(
        "SELECT COUNT(*) FROM liturgical_unit_meta",
    ).fetchone()[0]
    assert count_after_second == EXPECTED_UNIT_COUNT, (
        f"second ingest changed row count from {count_after_first} "
        f"to {count_after_second} (expected no change)"
    )
