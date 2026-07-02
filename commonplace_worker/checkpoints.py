"""Stage-level job checkpointing for the worker.

Handlers invoke a :class:`Checkpointer` to record progress through
named stages (``audio_downloaded``, ``transcribed``, ``summarized`` …).
On the next attempt of the same ``job_id``, stages marked ``complete``
are skipped and their stored outputs (paths to durable scratch files,
document ids, content hashes) are replayed.

Design notes
------------
* **Handler API, no handler signature change.** The worker injects the
  reserved key ``_job_id`` into every payload before dispatching; the
  handler builds a :class:`Checkpointer` with ``for_payload(conn, payload,
  attempt)``. Payloads missing ``_job_id`` (e.g. tests that call
  handlers directly) get a no-op checkpointer — all stages read as
  incomplete and all writes are suppressed.
* **Feature flag.** ``COMMONPLACE_STAGE_CHECKPOINTS=0`` disables
  checkpointing at the module level; ``is_complete`` always returns
  ``False`` and writes become no-ops. This is the clean kill switch if
  the feature misbehaves in production.
* **Idempotent writes.** ``complete(stage, output)`` uses
  ``INSERT … ON CONFLICT DO UPDATE`` so replaying a stage is safe.
  Output payload uses COALESCE — once recorded, output is preserved
  even if a later ``start`` call lacks it.
* **Durable scratch cache.** Handlers that produce files they want to
  survive a crash (downloaded audio, Whisper transcripts) should write
  them under :func:`stage_cache_dir`. The directory is cleared when the
  job completes via :func:`purge_for_job`.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _is_enabled() -> bool:
    return os.environ.get("COMMONPLACE_STAGE_CHECKPOINTS", "1") != "0"


def _stage_cache_root() -> Path:
    """Return the root directory for per-job durable scratch files."""
    root = os.environ.get("COMMONPLACE_STAGE_CACHE_DIR")
    if root:
        return Path(root).expanduser()
    return Path.home() / "commonplace" / ".stage_cache"


def stage_cache_dir(job_id: int) -> Path:
    """Return (and create) the durable scratch directory for ``job_id``.

    Files written here survive a worker crash so the next attempt of the
    same job can re-use them. Cleaned up by :func:`purge_for_job` when
    the job completes.
    """
    path = _stage_cache_root() / str(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


class Checkpointer:
    """Handler-facing stage checkpoint recorder.

    Construct via :func:`for_payload`; handlers do not construct this
    directly. When ``job_id`` is ``None`` (e.g. tests calling a handler
    without going through ``poll_once``) all methods are no-ops and
    ``is_complete`` always returns ``False`` — i.e. the handler runs
    every stage from scratch, which is exactly the pre-feature behaviour.
    """

    def __init__(
        self, conn: sqlite3.Connection, job_id: int | None, attempt: int
    ) -> None:
        self._conn = conn
        self._job_id = job_id
        self._attempt = attempt

    def enabled(self) -> bool:
        return self._job_id is not None and _is_enabled()

    def is_complete(self, stage: str) -> bool:
        """Was this stage marked complete on a prior attempt?"""
        if not self.enabled():
            return False
        row = self._conn.execute(
            "SELECT 1 FROM job_stage_checkpoints "
            "WHERE job_id = ? AND stage = ? AND status = 'complete' LIMIT 1",
            (self._job_id, stage),
        ).fetchone()
        return row is not None

    def get_output(self, stage: str) -> dict[str, Any] | None:
        """Return the stored output for a completed stage, or None."""
        if not self.enabled():
            return None
        row = self._conn.execute(
            "SELECT payload FROM job_stage_checkpoints "
            "WHERE job_id = ? AND stage = ? AND status = 'complete'",
            (self._job_id, stage),
        ).fetchone()
        if row is None or row["payload"] is None:
            return None
        try:
            output = json.loads(row["payload"])
        except json.JSONDecodeError:
            logger.warning(
                "checkpoint payload for job=%s stage=%s is malformed; ignoring",
                self._job_id, stage,
            )
            return None
        return output if isinstance(output, dict) else None

    def start(self, stage: str) -> None:
        """Record that a stage has begun executing.

        Idempotent against completed stages — if the stage is already
        ``complete`` from a prior attempt, calling ``start`` is a no-op.
        This guards against a handler that doesn't gate on
        :meth:`is_complete` and would otherwise overwrite a good
        completion record with a fresh ``started`` status, losing the
        stored payload.
        """
        if self.is_complete(stage):
            return
        self._upsert(stage, status="started", payload=None)

    def complete(
        self, stage: str, output: dict[str, Any] | None = None
    ) -> None:
        """Record that a stage finished successfully.

        ``output`` is JSON-serialised and persisted so a resumed attempt
        can read it back from :meth:`get_output`.
        """
        payload = json.dumps(output) if output is not None else None
        self._upsert(stage, status="complete", payload=payload)

    def fail(self, stage: str, error: str | None = None) -> None:
        """Record that a stage failed (diagnostic only; not used for gating)."""
        payload = json.dumps({"error": error}) if error else None
        self._upsert(stage, status="failed", payload=payload)

    def _upsert(self, stage: str, status: str, payload: str | None) -> None:
        if not self.enabled():
            return
        # Payload uses COALESCE so a later 'start' call doesn't clobber the
        # output stored by an earlier 'complete' call in the same attempt.
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO job_stage_checkpoints
                    (job_id, stage, status, payload, attempt, updated_at)
                VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                ON CONFLICT(job_id, stage) DO UPDATE SET
                    status = excluded.status,
                    payload = COALESCE(excluded.payload, job_stage_checkpoints.payload),
                    attempt = excluded.attempt,
                    updated_at = excluded.updated_at
                """,
                (self._job_id, stage, status, payload, self._attempt),
            )


def for_payload(
    conn: sqlite3.Connection, payload: dict[str, Any], attempt: int
) -> Checkpointer:
    """Construct a :class:`Checkpointer` from a worker payload.

    The worker's ``poll_once`` injects ``_job_id`` into the payload; a
    handler calling ``for_payload(conn, payload, attempt)`` gets an
    enabled checkpointer in production and a no-op one in tests that
    build payloads directly.
    """
    raw = payload.get("_job_id")
    job_id = int(raw) if isinstance(raw, int) else None
    return Checkpointer(conn, job_id, attempt)


def purge_for_job(conn: sqlite3.Connection, job_id: int) -> None:
    """Delete all checkpoint rows and the scratch directory for ``job_id``.

    Called when a job completes successfully. Safe to call more than
    once; the scratch directory removal suppresses ``FileNotFoundError``.
    """
    with conn:
        conn.execute(
            "DELETE FROM job_stage_checkpoints WHERE job_id = ?", (job_id,)
        )
    scratch = _stage_cache_root() / str(job_id)
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)


def purge_old_checkpoints(conn: sqlite3.Connection, days: int = 7) -> int:
    """Remove checkpoint rows older than ``days`` for terminal jobs.

    "Terminal" means the parent job is ``complete``, ``failed``, or
    ``cancelled`` — still-queued or still-running jobs keep their rows.
    Returns the number of deleted checkpoint rows. Safe to call from a
    janitor; uses a single DELETE driven by a correlated subquery.
    """
    cutoff = f"-{days} day"
    with conn:
        cursor = conn.execute(
            """
            DELETE FROM job_stage_checkpoints
             WHERE job_id IN (
                     SELECT id FROM job_queue
                      WHERE status IN ('complete','failed','cancelled')
                        AND COALESCE(completed_at, created_at) <
                            strftime('%Y-%m-%dT%H:%M:%SZ','now', ?)
                   )
            """,
            (cutoff,),
        )
        deleted = cursor.rowcount
    return int(deleted) if deleted and deleted > 0 else 0
