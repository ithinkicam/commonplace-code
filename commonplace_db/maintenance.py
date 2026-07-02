"""Housekeeping for unbounded operational tables.

Nothing else ever deletes from ``job_queue`` or ``scheduled_runs``, so both
grow forever. :func:`purge_old_records` trims rows that are past their useful
diagnostic life. It is invoked periodically by the zombie-job watchdog
(``scripts/zombie_job_watchdog.py``, launchd ``StartInterval``) — the delete
is idempotent and indexed, so frequent runs are near-zero load.
"""

from __future__ import annotations

import logging
import sqlite3

_log = logging.getLogger(__name__)

# job_queue statuses that mean the row will never be picked up again
# (see the CHECK constraint in migrations/0001_initial.sql).
TERMINAL_JOB_STATUSES: tuple[str, ...] = ("complete", "failed", "cancelled")


def purge_old_records(conn: sqlite3.Connection, days: int = 90) -> dict[str, int]:
    """Delete stale operational rows older than *days* days.

    Purged:
    - ``job_queue`` rows in a terminal state (complete/failed/cancelled)
      whose ``completed_at`` (falling back to ``created_at``) is older than
      the cutoff. Non-terminal rows are never touched, however old — a stuck
      queued/running row is the zombie watchdog's problem, not ours.
    - ``scheduled_runs`` rows whose ``completed_at`` is older than the cutoff.

    NOTE: ``surface_invocations`` is intentionally excluded — it is retained
    forever as the long-term quality-tracking log.

    Commits on success and returns per-table delete counts, e.g.
    ``{"job_queue": 12, "scheduled_runs": 3}``.
    """
    cutoff_modifier = f"-{int(days)} days"
    placeholders = ",".join("?" for _ in TERMINAL_JOB_STATUSES)
    with conn:
        jobs = conn.execute(
            f"""
            DELETE FROM job_queue
             WHERE status IN ({placeholders})
               AND COALESCE(completed_at, created_at)
                   < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)
            """,
            (*TERMINAL_JOB_STATUSES, cutoff_modifier),
        ).rowcount
        runs = conn.execute(
            """
            DELETE FROM scheduled_runs
             WHERE completed_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)
            """,
            (cutoff_modifier,),
        ).rowcount

    counts = {"job_queue": jobs, "scheduled_runs": runs}
    if jobs or runs:
        _log.info("purged old records: %s", counts)
    return counts
