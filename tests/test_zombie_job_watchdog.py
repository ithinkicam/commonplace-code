"""Tests for scripts/zombie_job_watchdog.py.

Runs against an in-memory sqlite DB that mirrors the ``job_queue`` shape
written by ``commonplace_db/migrations/0001_init.sql`` (see
``SELECT sql FROM sqlite_master WHERE name='job_queue'`` in the live DB).
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).parent.parent
WATCHDOG_PATH = REPO_ROOT / "scripts" / "zombie_job_watchdog.py"


def _load_watchdog() -> ModuleType:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        "zombie_job_watchdog_under_test", WATCHDOG_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def watchdog() -> ModuleType:
    return _load_watchdog()


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE job_queue (
            id              INTEGER PRIMARY KEY,
            kind            TEXT    NOT NULL,
            payload         TEXT    NOT NULL DEFAULT '{}',
            status          TEXT    NOT NULL DEFAULT 'queued'
                            CHECK(status IN ('queued','running','complete','failed','cancelled')),
            attempts        INTEGER NOT NULL DEFAULT 0,
            error           TEXT,
            created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            started_at      TEXT,
            completed_at    TEXT
        );
        """
    )
    return conn


def _insert_job(
    db: sqlite3.Connection,
    *,
    kind: str,
    status: str,
    started_at: str | None,
    payload: str = '{"x": 1}',
) -> int:
    cur = db.execute(
        "INSERT INTO job_queue (kind, status, payload, started_at) VALUES (?, ?, ?, ?)",
        (kind, status, payload, started_at),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


NOW = datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)


def _ts_minus(minutes: int) -> str:
    """Return an ISO-Z timestamp ``minutes`` before NOW."""
    return (NOW - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestFindZombies:
    def test_fresh_running_job_is_not_a_zombie(
        self, db: sqlite3.Connection, watchdog: ModuleType
    ) -> None:
        _insert_job(db, kind="ingest_library", status="running", started_at=_ts_minus(5))
        zombies = watchdog.find_zombies(db, NOW)
        assert zombies == []

    def test_library_over_90min_is_a_zombie(
        self, db: sqlite3.Connection, watchdog: ModuleType
    ) -> None:
        _insert_job(
            db, kind="ingest_library", status="running", started_at=_ts_minus(95)
        )
        zombies = watchdog.find_zombies(db, NOW)
        assert len(zombies) == 1
        assert zombies[0]["kind"] == "ingest_library"
        assert zombies[0]["age_seconds"] == 95 * 60
        assert zombies[0]["threshold_seconds"] == 90 * 60
        assert zombies[0]["reason"] == "stale"

    def test_enrichment_over_15min_is_a_zombie(
        self, db: sqlite3.Connection, watchdog: ModuleType
    ) -> None:
        _insert_job(
            db,
            kind="ingest_book_enrichment",
            status="running",
            started_at=_ts_minus(20),
        )
        zombies = watchdog.find_zombies(db, NOW)
        assert len(zombies) == 1
        assert zombies[0]["kind"] == "ingest_book_enrichment"

    def test_unknown_kind_uses_default_threshold(
        self, db: sqlite3.Connection, watchdog: ModuleType
    ) -> None:
        # Default is 30 min — 25 min is fine, 35 min is a zombie.
        _insert_job(
            db, kind="some_new_unregistered_kind", status="running", started_at=_ts_minus(25)
        )
        assert watchdog.find_zombies(db, NOW) == []
        _insert_job(
            db, kind="some_new_unregistered_kind", status="running", started_at=_ts_minus(35)
        )
        zombies = watchdog.find_zombies(db, NOW)
        assert len(zombies) == 1
        assert zombies[0]["threshold_seconds"] == 30 * 60

    def test_queued_and_complete_are_not_zombies(
        self, db: sqlite3.Connection, watchdog: ModuleType
    ) -> None:
        # Even a very old queued/complete job is not a zombie; watchdog only
        # cares about `running` rows.
        _insert_job(db, kind="ingest_library", status="queued", started_at=None)
        _insert_job(
            db, kind="ingest_library", status="complete", started_at=_ts_minus(10_000)
        )
        assert watchdog.find_zombies(db, NOW) == []

    def test_running_with_null_started_at_is_anomalous(
        self, db: sqlite3.Connection, watchdog: ModuleType
    ) -> None:
        # Status=running with NULL started_at means the worker set status but
        # never recorded started_at — treat as zombie so operator sees it.
        _insert_job(db, kind="ingest_library", status="running", started_at=None)
        zombies = watchdog.find_zombies(db, NOW)
        assert len(zombies) == 1
        assert zombies[0]["reason"] == "no-parseable-started_at"
        assert zombies[0]["age_seconds"] is None

    def test_millisecond_started_at_parses(
        self, db: sqlite3.Connection, watchdog: ModuleType
    ) -> None:
        # The worker writes both %S and %f variants — both must parse.
        ts_ms = (NOW - timedelta(minutes=95)).strftime("%Y-%m-%dT%H:%M:%S.123Z")
        _insert_job(db, kind="ingest_library", status="running", started_at=ts_ms)
        zombies = watchdog.find_zombies(db, NOW)
        assert len(zombies) == 1


class TestFailZombie:
    def test_marks_status_failed_and_sets_error(
        self, db: sqlite3.Connection, watchdog: ModuleType
    ) -> None:
        job_id = _insert_job(
            db, kind="ingest_library", status="running", started_at=_ts_minus(95)
        )
        zombies = watchdog.find_zombies(db, NOW)
        with db:
            watchdog.fail_zombie(db, zombies[0], NOW)
        row = db.execute(
            "SELECT status, error, completed_at FROM job_queue WHERE id=?", (job_id,)
        ).fetchone()
        assert row["status"] == "failed"
        assert "zombie detected" in row["error"]
        assert "5700s" in row["error"]  # 95 * 60
        assert "ingest_library" in row["error"]
        assert row["completed_at"] is not None

    def test_null_started_at_message(
        self, db: sqlite3.Connection, watchdog: ModuleType
    ) -> None:
        job_id = _insert_job(
            db, kind="ingest_library", status="running", started_at=None
        )
        zombies = watchdog.find_zombies(db, NOW)
        with db:
            watchdog.fail_zombie(db, zombies[0], NOW)
        err = db.execute(
            "SELECT error FROM job_queue WHERE id=?", (job_id,)
        ).fetchone()["error"]
        assert "no parseable started_at" in err

    def test_concurrent_completion_not_clobbered(
        self, db: sqlite3.Connection, watchdog: ModuleType
    ) -> None:
        """If the worker completes the job after find_zombies but before
        fail_zombie, the UPDATE WHERE status='running' guard must skip it."""
        job_id = _insert_job(
            db, kind="ingest_library", status="running", started_at=_ts_minus(95)
        )
        zombies = watchdog.find_zombies(db, NOW)
        # Simulate worker completing between find and fail.
        db.execute(
            "UPDATE job_queue SET status='complete', completed_at=? WHERE id=?",
            (NOW.isoformat(), job_id),
        )
        with db:
            watchdog.fail_zombie(db, zombies[0], NOW)
        row = db.execute(
            "SELECT status, error FROM job_queue WHERE id=?", (job_id,)
        ).fetchone()
        assert row["status"] == "complete"
        assert row["error"] is None


class TestRunWatchdog:
    def test_returns_count_and_zombie_list(
        self, db: sqlite3.Connection, watchdog: ModuleType, tmp_path: Path
    ) -> None:
        # run_watchdog takes a path, so dump the in-memory fixture onto disk
        # for this test.
        disk_path = tmp_path / "lib.db"
        disk_conn = sqlite3.connect(disk_path)
        # Clone the fixture schema.
        for row in db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table'"
        ).fetchall():
            disk_conn.execute(row["sql"])
        disk_conn.execute(
            "INSERT INTO job_queue (kind, status, started_at) VALUES (?, 'running', ?)",
            ("ingest_library", _ts_minus(95)),
        )
        disk_conn.execute(
            "INSERT INTO job_queue (kind, status, started_at) VALUES (?, 'running', ?)",
            ("ingest_library", _ts_minus(5)),
        )
        disk_conn.commit()
        disk_conn.close()

        count, zombies = watchdog.run_watchdog(str(disk_path), now=NOW)
        assert count == 1
        assert len(zombies) == 1

    def test_dry_run_does_not_mutate(
        self, db: sqlite3.Connection, watchdog: ModuleType, tmp_path: Path
    ) -> None:
        disk_path = tmp_path / "lib.db"
        disk_conn = sqlite3.connect(disk_path)
        for row in db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table'"
        ).fetchall():
            disk_conn.execute(row["sql"])
        disk_conn.execute(
            "INSERT INTO job_queue (kind, status, started_at) VALUES (?, 'running', ?)",
            ("ingest_library", _ts_minus(95)),
        )
        disk_conn.commit()
        disk_conn.close()

        count, zombies = watchdog.run_watchdog(
            str(disk_path), now=NOW, dry_run=True
        )
        assert count == 0
        assert len(zombies) == 1
        # Row still in running state
        check = sqlite3.connect(disk_path)
        status = check.execute(
            "SELECT status FROM job_queue LIMIT 1"
        ).fetchone()[0]
        check.close()
        assert status == "running"


class TestThresholdForKind:
    def test_known_kinds_use_explicit_thresholds(
        self, watchdog: ModuleType
    ) -> None:
        assert watchdog.threshold_for_kind("ingest_library") == 90 * 60
        assert watchdog.threshold_for_kind("ingest_book_enrichment") == 15 * 60
        assert watchdog.threshold_for_kind("ingest_bluesky_url") == 5 * 60

    def test_unknown_kind_falls_back_to_default(
        self, watchdog: ModuleType
    ) -> None:
        assert (
            watchdog.threshold_for_kind("brand_new_kind")
            == watchdog.DEFAULT_THRESHOLD_SECONDS
        )
