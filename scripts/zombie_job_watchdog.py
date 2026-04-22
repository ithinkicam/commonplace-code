#!/usr/bin/env python3
"""Zombie job watchdog — mark long-running jobs as failed.

Runs on a timer via launchd ``StartInterval``. For any job in ``running``
state whose ``started_at`` exceeds a per-kind threshold, marks it ``failed``
with a ``zombie detected ...`` error. Prevents the queue from getting
permanently wedged when a worker crash or an external hang (e.g. Google
Drive File Stream ``open()`` hanging for 20+ min) leaves rows in running
state forever — three such zombies accumulated over 64–85 hours before
manual clearing on 2026-04-22 and motivated this watchdog.

Deliberately does NOT auto-requeue. Operator decides whether the job is
worth retrying after reading the error context; blind auto-requeue risks
infinite loops for genuinely-broken inputs (e.g. a deleted file, a 4xx
external API, a content_hash UNIQUE collision).
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Per-kind stale thresholds, in seconds. Tuned generously — we're catching
# hours-long hangs, not minute-level slowness. Kinds not listed fall back to
# DEFAULT_THRESHOLD. Thresholds reflect p99-ish legitimate completion times
# based on observed queue behavior (see handler modules for actual work).
KIND_THRESHOLDS: dict[str, int] = {
    "ingest_library": 90 * 60,
    "ingest_audiobook": 15 * 60,
    "ingest_book_enrichment": 15 * 60,
    "ingest_movie": 15 * 60,
    "ingest_tv": 15 * 60,
    "ingest_article": 15 * 60,
    "ingest_podcast": 30 * 60,
    "ingest_youtube": 30 * 60,
    "ingest_image_url": 10 * 60,
    "ingest_pdf": 60 * 60,
    "ingest_capture": 10 * 60,
    "ingest_bluesky_url": 5 * 60,
    "ingest_liturgy_bcp": 60 * 60,
    "ingest_liturgy_lff": 60 * 60,
    "classify_book": 5 * 60,
    "generate_book_note": 10 * 60,
    "regenerate_profile": 30 * 60,
}
DEFAULT_THRESHOLD_SECONDS = 30 * 60

DB_PATH_ENV = "COMMONPLACE_DB_PATH"


def threshold_for_kind(kind: str) -> int:
    """Return the stale threshold for a job kind, or the default."""
    return KIND_THRESHOLDS.get(kind, DEFAULT_THRESHOLD_SECONDS)


def _parse_started_at(raw: str | None) -> datetime | None:
    """Parse ``job_queue.started_at`` strings written by the worker.

    Handler code writes either ``strftime('%Y-%m-%dT%H:%M:%SZ', 'now')`` or
    ``strftime('%Y-%m-%dT%H:%M:%fZ', 'now')`` (millisecond variant). Both
    become aware UTC datetimes after replacing the trailing ``Z``.
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def find_zombies(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    """Return running jobs whose age exceeds their kind's threshold.

    A row with status=running and unparseable/missing started_at is also
    flagged as a zombie — it's anomalous either way.
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, kind, started_at, attempts, "
        "substr(payload, 1, 200) AS payload_head "
        "FROM job_queue WHERE status='running'"
    ).fetchall()

    zombies: list[dict] = []
    for r in rows:
        ts = _parse_started_at(r["started_at"])
        kind = r["kind"]
        threshold = threshold_for_kind(kind)
        if ts is None:
            zombies.append(
                {
                    "id": r["id"],
                    "kind": kind,
                    "payload_head": r["payload_head"],
                    "age_seconds": None,
                    "threshold_seconds": threshold,
                    "reason": "no-parseable-started_at",
                }
            )
            continue
        age = int((now - ts).total_seconds())
        if age > threshold:
            zombies.append(
                {
                    "id": r["id"],
                    "kind": kind,
                    "payload_head": r["payload_head"],
                    "age_seconds": age,
                    "threshold_seconds": threshold,
                    "reason": "stale",
                }
            )
    return zombies


def fail_zombie(
    conn: sqlite3.Connection,
    zombie: dict,
    now: datetime,
) -> None:
    """Mark one zombie job as failed with a diagnostic error.

    The WHERE clause re-checks ``status='running'`` so a concurrent worker
    update that legitimately completed the job doesn't get clobbered.
    """
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if zombie["reason"] == "no-parseable-started_at":
        msg = (
            f"zombie detected {stamp} — status=running with no parseable "
            f"started_at; killed by watchdog"
        )
    else:
        msg = (
            f"zombie detected {stamp} — running for {zombie['age_seconds']}s "
            f"(>{zombie['threshold_seconds']}s threshold for kind={zombie['kind']}); "
            f"killed by watchdog"
        )
    conn.execute(
        "UPDATE job_queue SET status='failed', completed_at=?, error=? "
        "WHERE id=? AND status='running'",
        (stamp, msg, zombie["id"]),
    )


def run_watchdog(
    db_path: str,
    now: datetime | None = None,
    dry_run: bool = False,
) -> tuple[int, list[dict]]:
    """Run one pass of the watchdog. Returns ``(failed_count, zombies)``."""
    if now is None:
        now = datetime.now(UTC)
    conn = sqlite3.connect(db_path)
    try:
        zombies = find_zombies(conn, now)
        if not zombies or dry_run:
            return 0, zombies
        with conn:
            for z in zombies:
                fail_zombie(conn, z, now)
        return len(zombies), zombies
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List zombies without mutating job_queue.",
    )
    parser.add_argument(
        "--db-path",
        default=os.environ.get(DB_PATH_ENV),
        help=f"Path to library.db (default: ${DB_PATH_ENV} env var).",
    )
    args = parser.parse_args(argv)

    if not args.db_path:
        logger.error(
            "DB path required — set %s env var or pass --db-path", DB_PATH_ENV
        )
        return 2

    now = datetime.now(UTC)
    count, zombies = run_watchdog(args.db_path, now=now, dry_run=args.dry_run)

    for z in zombies:
        logger.warning(
            "zombie: id=%s kind=%s age=%ss threshold=%ss reason=%s payload=%s",
            z["id"],
            z["kind"],
            z["age_seconds"],
            z["threshold_seconds"],
            z["reason"],
            (z.get("payload_head") or "")[:80],
        )

    if args.dry_run:
        logger.info("dry-run: %d zombie(s) would be marked failed", len(zombies))
    elif zombies:
        logger.info("marked %d zombie(s) failed", count)
    else:
        logger.info("no zombies (running jobs all within per-kind thresholds)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
