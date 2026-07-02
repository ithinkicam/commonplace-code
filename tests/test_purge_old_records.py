"""Tests for commonplace_db/maintenance.py::purge_old_records.

Runs against an in-memory DB with the real migrations applied so the
job_queue / scheduled_runs / surface_invocations shapes match production.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from commonplace_db import connect, migrate
from commonplace_db.maintenance import TERMINAL_JOB_STATUSES, purge_old_records


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


def _ts_days_ago(days: int) -> str:
    """Return an ISO-Z timestamp *days* before now (matches worker format)."""
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_job(
    db: sqlite3.Connection,
    *,
    status: str,
    completed_at: str | None,
    created_at: str | None = None,
) -> int:
    cur = db.execute(
        "INSERT INTO job_queue (kind, status, created_at, completed_at) "
        "VALUES ('ingest_article', ?, COALESCE(?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')), ?)",
        (status, created_at, completed_at),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _insert_scheduled_run(db: sqlite3.Connection, *, completed_at: str) -> int:
    cur = db.execute(
        "INSERT INTO scheduled_runs (name, status, completed_at) VALUES ('nightly', 'ok', ?)",
        (completed_at,),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _job_ids(db: sqlite3.Connection) -> set[int]:
    return {row[0] for row in db.execute("SELECT id FROM job_queue")}


class TestJobQueuePurge:
    def test_old_terminal_rows_purged(self, db: sqlite3.Connection) -> None:
        for status in TERMINAL_JOB_STATUSES:
            _insert_job(db, status=status, completed_at=_ts_days_ago(120))
        counts = purge_old_records(db, days=90)
        assert counts["job_queue"] == len(TERMINAL_JOB_STATUSES)
        assert _job_ids(db) == set()

    def test_recent_terminal_rows_kept(self, db: sqlite3.Connection) -> None:
        kept = _insert_job(db, status="complete", completed_at=_ts_days_ago(5))
        purged = _insert_job(db, status="complete", completed_at=_ts_days_ago(120))
        counts = purge_old_records(db, days=90)
        assert counts["job_queue"] == 1
        assert _job_ids(db) == {kept}
        assert purged not in _job_ids(db)

    def test_old_non_terminal_rows_kept(self, db: sqlite3.Connection) -> None:
        # queued/running rows are never purged, however old — a stuck row is
        # the zombie watchdog's problem, not housekeeping's.
        queued = _insert_job(
            db, status="queued", completed_at=None, created_at=_ts_days_ago(365)
        )
        running = _insert_job(
            db, status="running", completed_at=None, created_at=_ts_days_ago(365)
        )
        counts = purge_old_records(db, days=90)
        assert counts["job_queue"] == 0
        assert _job_ids(db) == {queued, running}

    def test_terminal_row_without_completed_at_uses_created_at(
        self, db: sqlite3.Connection
    ) -> None:
        # e.g. a cancelled job that never ran — falls back to created_at.
        _insert_job(
            db, status="cancelled", completed_at=None, created_at=_ts_days_ago(120)
        )
        counts = purge_old_records(db, days=90)
        assert counts["job_queue"] == 1


class TestScheduledRunsPurge:
    def test_old_rows_purged_recent_kept(self, db: sqlite3.Connection) -> None:
        _insert_scheduled_run(db, completed_at=_ts_days_ago(120))
        kept = _insert_scheduled_run(db, completed_at=_ts_days_ago(5))
        counts = purge_old_records(db, days=90)
        assert counts["scheduled_runs"] == 1
        rows = {row[0] for row in db.execute("SELECT id FROM scheduled_runs")}
        assert rows == {kept}


class TestSurfaceInvocationsRetained:
    def test_surface_invocations_never_touched(self, db: sqlite3.Connection) -> None:
        # surface_invocations is the long-term quality log — purge must
        # leave it alone regardless of age.
        db.execute(
            "INSERT INTO surface_invocations "
            "(seed, mode, requested_limit, similarity_floor, recency_bias, "
            " judge_status, elapsed_ms, created_at) "
            "VALUES ('seed', 'ambient', 5, 0.5, 0, 'success', 12.5, ?)",
            (_ts_days_ago(1000),),
        )
        counts = purge_old_records(db, days=90)
        assert "surface_invocations" not in counts
        n = db.execute("SELECT COUNT(*) FROM surface_invocations").fetchone()[0]
        assert n == 1
