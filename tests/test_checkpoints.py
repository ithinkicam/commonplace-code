"""Tests for commonplace_worker.checkpoints — stage-level job checkpointing."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from commonplace_db.db import connect, migrate
from commonplace_worker.checkpoints import (
    Checkpointer,
    for_payload,
    purge_for_job,
    purge_old_checkpoints,
    stage_cache_dir,
)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(":memory:")
    migrate(c)
    return c


def _insert_job(conn: sqlite3.Connection, kind: str = "ingest_article") -> int:
    """Insert a job_queue row and return its id."""
    cur = conn.execute(
        "INSERT INTO job_queue (kind, payload) VALUES (?, '{}')", (kind,)
    )
    conn.commit()
    return int(cur.lastrowid or 0)


# ---------------------------------------------------------------------------
# Checkpointer basics
# ---------------------------------------------------------------------------


def test_no_op_when_job_id_missing(conn: sqlite3.Connection) -> None:
    """Tests that build payloads directly get a silent no-op checkpointer."""
    ckpt = for_payload(conn, {}, attempt=1)
    assert not ckpt.enabled()
    assert not ckpt.is_complete("foo")
    assert ckpt.get_output("foo") is None
    # Writes must not raise.
    ckpt.start("foo")
    ckpt.complete("foo", {"x": 1})
    # Nothing should have been persisted.
    row = conn.execute("SELECT COUNT(*) AS n FROM job_stage_checkpoints").fetchone()
    assert row["n"] == 0


def test_complete_then_is_complete_roundtrip(conn: sqlite3.Connection) -> None:
    job_id = _insert_job(conn)
    ckpt = for_payload(conn, {"_job_id": job_id}, attempt=1)
    assert ckpt.enabled()
    assert not ckpt.is_complete("stage_a")

    ckpt.complete("stage_a", {"result": "ok"})
    assert ckpt.is_complete("stage_a")
    assert ckpt.get_output("stage_a") == {"result": "ok"}


def test_upsert_preserves_output_when_restart_called(
    conn: sqlite3.Connection,
) -> None:
    """Calling start() after complete() must not clobber the stored output."""
    job_id = _insert_job(conn)
    ckpt = for_payload(conn, {"_job_id": job_id}, attempt=1)

    ckpt.complete("stage_a", {"path": "/tmp/foo"})
    # A buggy retry that re-calls start should not wipe the payload.
    ckpt.start("stage_a")
    assert ckpt.get_output("stage_a") == {"path": "/tmp/foo"}


def test_get_output_returns_none_for_started_but_not_complete(
    conn: sqlite3.Connection,
) -> None:
    job_id = _insert_job(conn)
    ckpt = for_payload(conn, {"_job_id": job_id}, attempt=1)
    ckpt.start("stage_a")
    assert ckpt.get_output("stage_a") is None
    assert not ckpt.is_complete("stage_a")


def test_feature_flag_disables_writes(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _insert_job(conn)
    monkeypatch.setenv("COMMONPLACE_STAGE_CHECKPOINTS", "0")
    ckpt = for_payload(conn, {"_job_id": job_id}, attempt=1)
    ckpt.complete("stage_a", {"x": 1})
    # Re-check directly — even though job_id is present, writes must be off.
    row = conn.execute("SELECT COUNT(*) AS n FROM job_stage_checkpoints").fetchone()
    assert row["n"] == 0
    assert not ckpt.is_complete("stage_a")


def test_malformed_payload_is_ignored(conn: sqlite3.Connection) -> None:
    """A malformed payload in the table (shouldn't happen, but defensive)
    must not crash get_output — it returns None."""
    job_id = _insert_job(conn)
    conn.execute(
        "INSERT INTO job_stage_checkpoints "
        "(job_id, stage, status, payload, attempt) "
        "VALUES (?, 'stage_a', 'complete', 'not json', 1)",
        (job_id,),
    )
    conn.commit()
    ckpt = for_payload(conn, {"_job_id": job_id}, attempt=1)
    assert ckpt.is_complete("stage_a")  # still marked complete
    assert ckpt.get_output("stage_a") is None


# ---------------------------------------------------------------------------
# purge_for_job + scratch cache
# ---------------------------------------------------------------------------


def test_stage_cache_dir_creates_per_job_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMMONPLACE_STAGE_CACHE_DIR", str(tmp_path))
    path = stage_cache_dir(42)
    assert path == tmp_path / "42"
    assert path.is_dir()


def test_purge_for_job_removes_rows_and_scratch_dir(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMMONPLACE_STAGE_CACHE_DIR", str(tmp_path))
    job_id = _insert_job(conn)
    ckpt = for_payload(conn, {"_job_id": job_id}, attempt=1)
    ckpt.complete("stage_a", {"x": 1})

    scratch = stage_cache_dir(job_id)
    (scratch / "audio.wav").write_text("fake audio")

    purge_for_job(conn, job_id)

    row = conn.execute(
        "SELECT COUNT(*) AS n FROM job_stage_checkpoints WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert row["n"] == 0
    assert not scratch.exists()


# ---------------------------------------------------------------------------
# purge_old_checkpoints (janitor)
# ---------------------------------------------------------------------------


def test_purge_old_keeps_live_jobs_and_drops_stale_terminals(
    conn: sqlite3.Connection,
) -> None:
    live_id = _insert_job(conn)  # status defaults to 'queued'
    old_complete_id = _insert_job(conn)
    recent_failed_id = _insert_job(conn)

    conn.execute(
        "UPDATE job_queue SET status='complete', "
        "completed_at=strftime('%Y-%m-%dT%H:%M:%SZ','now','-30 day') "
        "WHERE id = ?",
        (old_complete_id,),
    )
    conn.execute(
        "UPDATE job_queue SET status='failed', "
        "completed_at=strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 hour') "
        "WHERE id = ?",
        (recent_failed_id,),
    )
    conn.commit()

    for jid in (live_id, old_complete_id, recent_failed_id):
        for_payload(conn, {"_job_id": jid}, attempt=1).complete("stage_a", None)

    deleted = purge_old_checkpoints(conn, days=7)
    assert deleted == 1

    remaining = {
        row["job_id"]
        for row in conn.execute("SELECT job_id FROM job_stage_checkpoints").fetchall()
    }
    assert remaining == {live_id, recent_failed_id}


# ---------------------------------------------------------------------------
# Direct Checkpointer construction path
# ---------------------------------------------------------------------------


def test_checkpointer_none_job_id_is_noop(conn: sqlite3.Connection) -> None:
    ckpt = Checkpointer(conn, job_id=None, attempt=1)
    ckpt.complete("foo", {"x": 1})
    assert not ckpt.enabled()
    row = conn.execute("SELECT COUNT(*) AS n FROM job_stage_checkpoints").fetchone()
    assert row["n"] == 0
