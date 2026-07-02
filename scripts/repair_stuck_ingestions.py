#!/usr/bin/env python3
"""Repair zero-chunk documents left in ``status='ingesting'``.

This is a narrow operational cleanup for interrupted ingest attempts:

1. Find documents stuck in ``ingesting`` with no chunks.
2. Match job-backed documents to failed job payloads and enqueue fresh jobs.
3. Delete the partial document rows so normal idempotency checks stop skipping
   them.
4. Delete orphan ``chunk_vectors`` rows left by sqlite-vec's lack of FK cascade.

Bluesky backfill rows are cleaned up here, but the existing
``scripts/bluesky_backfill.py`` should be run afterward to recreate them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import commonplace_db  # noqa: E402
from commonplace_server.jobs import submit  # noqa: E402

logger = logging.getLogger("repair_stuck_ingestions")

TARGET_CONTENT_TYPES = {
    "book",
    "bluesky_post",
    "conversation_summary",
    "therapy_session",
}

JOB_KIND_BY_CONTENT_TYPE = {
    "book": "ingest_library",
    "conversation_summary": "ingest_conversation_summary",
    "therapy_session": "ingest_therapy_session",
}


@dataclass(frozen=True)
class PartialDocument:
    id: int
    content_type: str
    source_uri: str | None
    source_id: str | None
    raw_path: str | None
    content_hash: str | None
    title: str | None
    created_at: str


@dataclass(frozen=True)
class RepairPlan:
    partial_docs: list[PartialDocument]
    jobs_to_enqueue: list[tuple[str, dict[str, Any], int]]
    unmatched_docs: list[PartialDocument]
    bluesky_docs: list[PartialDocument]
    orphan_vector_ids: list[int]


def _load_partial_docs(conn: sqlite3.Connection, min_age_minutes: int) -> list[PartialDocument]:
    rows = conn.execute(
        f"""
        SELECT d.id, d.content_type, d.source_uri, d.source_id, d.raw_path,
               d.content_hash, d.title, d.created_at
          FROM documents d
         WHERE d.status = 'ingesting'
           AND d.content_type IN ({",".join("?" for _ in TARGET_CONTENT_TYPES)})
           AND d.created_at <= strftime(
                   '%Y-%m-%dT%H:%M:%SZ', 'now', ?
               )
           AND NOT EXISTS (
                   SELECT 1 FROM chunks c WHERE c.document_id = d.id
               )
         ORDER BY d.content_type, d.id
        """,
        (*sorted(TARGET_CONTENT_TYPES), f"-{min_age_minutes} minutes"),
    ).fetchall()
    return [
        PartialDocument(
            id=int(row["id"]),
            content_type=str(row["content_type"]),
            source_uri=row["source_uri"],
            source_id=row["source_id"],
            raw_path=row["raw_path"],
            content_hash=row["content_hash"],
            title=row["title"],
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]


def _load_failed_jobs(conn: sqlite3.Connection) -> list[tuple[int, str, dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT id, kind, payload
          FROM job_queue
         WHERE status = 'failed'
           AND kind IN (
               'ingest_library',
               'ingest_conversation_summary',
               'ingest_therapy_session'
           )
         ORDER BY id DESC
        """
    ).fetchall()
    jobs: list[tuple[int, str, dict[str, Any]]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            jobs.append((int(row["id"]), str(row["kind"]), payload))
    return jobs


def _conversation_doc_date(doc: PartialDocument) -> str | None:
    for value in (doc.raw_path, doc.created_at):
        if not value:
            continue
        match = re.search(r"\d{4}-\d{2}-\d{2}", value)
        if match:
            return match.group(0)
    return None


def _conversation_content_hash(
    payload: dict[str, Any],
    *,
    fallback_date: str | None = None,
) -> str | None:
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    summary = summary.strip()
    platform = payload.get("platform")
    platform = platform.strip().lower() if isinstance(platform, str) else "claude"
    conversation_date = payload.get("conversation_date")
    if not isinstance(conversation_date, str) or not conversation_date.strip():
        conversation_date = fallback_date
    if not isinstance(conversation_date, str) or not conversation_date.strip():
        return None
    source_url_raw = payload.get("source_url")
    source_url = (
        source_url_raw.strip() or None if isinstance(source_url_raw, str) else None
    )
    model_raw = payload.get("model")
    model = model_raw.strip() or None if isinstance(model_raw, str) else None
    topics_raw = payload.get("topics")
    topics: list[str] = []
    if isinstance(topics_raw, list):
        for item in topics_raw:
            if isinstance(item, str):
                topic = item.strip()
                if topic and topic not in topics:
                    topics.append(topic)
    blob = json.dumps(
        {
            "summary": summary,
            "platform": platform,
            "conversation_date": conversation_date.strip(),
            "source_url": source_url,
            "model": model,
            "topics": topics,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def _matching_job_payload(
    doc: PartialDocument,
    failed_jobs: list[tuple[int, str, dict[str, Any]]],
) -> tuple[str, dict[str, Any], int] | None:
    if doc.content_type == "book":
        expected_path = doc.raw_path or doc.source_uri
        for job_id, kind, payload in failed_jobs:
            if kind == "ingest_library" and payload.get("path") == expected_path:
                return kind, {"path": payload["path"]}, job_id
        return None

    if doc.content_type == "conversation_summary":
        fallback_date = _conversation_doc_date(doc)
        for job_id, kind, payload in failed_jobs:
            if kind != "ingest_conversation_summary":
                continue
            payload_hash = _conversation_content_hash(
                payload,
                fallback_date=fallback_date,
            )
            source_url = payload.get("source_url")
            if payload_hash and payload_hash == doc.content_hash:
                return kind, dict(payload), job_id
            if isinstance(source_url, str) and source_url and source_url == doc.source_uri:
                return kind, dict(payload), job_id
        return None

    if doc.content_type == "therapy_session":
        notion_page_id = doc.source_id
        for job_id, kind, payload in failed_jobs:
            if (
                kind == "ingest_therapy_session"
                and payload.get("notion_page_id") == notion_page_id
            ):
                return kind, {"notion_page_id": payload["notion_page_id"]}, job_id
        return None

    return None


def _load_orphan_vector_ids(conn: sqlite3.Connection) -> list[int]:
    return [
        int(row["chunk_id"])
        for row in conn.execute(
            """
            SELECT v.chunk_id
              FROM chunk_vectors v
              LEFT JOIN chunks c ON c.id = v.chunk_id
             WHERE c.id IS NULL
             ORDER BY v.chunk_id
            """
        ).fetchall()
    ]


def build_plan(conn: sqlite3.Connection, min_age_minutes: int) -> RepairPlan:
    partial_docs = _load_partial_docs(conn, min_age_minutes)
    failed_jobs = _load_failed_jobs(conn)

    jobs_to_enqueue: list[tuple[str, dict[str, Any], int]] = []
    unmatched_docs: list[PartialDocument] = []
    bluesky_docs: list[PartialDocument] = []

    for doc in partial_docs:
        if doc.content_type == "bluesky_post":
            bluesky_docs.append(doc)
            continue
        match = _matching_job_payload(doc, failed_jobs)
        if match is None:
            unmatched_docs.append(doc)
        else:
            jobs_to_enqueue.append(match)

    return RepairPlan(
        partial_docs=partial_docs,
        jobs_to_enqueue=jobs_to_enqueue,
        unmatched_docs=unmatched_docs,
        bluesky_docs=bluesky_docs,
        orphan_vector_ids=_load_orphan_vector_ids(conn),
    )


def _print_plan(plan: RepairPlan) -> None:
    by_type: dict[str, int] = {}
    for doc in plan.partial_docs:
        by_type[doc.content_type] = by_type.get(doc.content_type, 0) + 1

    print("repair plan")
    print(f"  partial zero-chunk docs: {len(plan.partial_docs)}")
    for content_type in sorted(by_type):
        print(f"    {content_type}: {by_type[content_type]}")
    print(f"  fresh jobs to enqueue: {len(plan.jobs_to_enqueue)}")
    print(f"  bluesky rows to delete before backfill: {len(plan.bluesky_docs)}")
    print(f"  unmatched non-bluesky docs: {len(plan.unmatched_docs)}")
    print(f"  orphan chunk_vectors rows to delete: {len(plan.orphan_vector_ids)}")

    if plan.unmatched_docs:
        print("\nunmatched docs")
        for doc in plan.unmatched_docs:
            print(f"  id={doc.id} type={doc.content_type} title={doc.title!r}")

    if plan.jobs_to_enqueue:
        print("\njobs to enqueue")
        for kind, payload, source_job_id in plan.jobs_to_enqueue:
            display = json.dumps(payload, ensure_ascii=False)
            if len(display) > 180:
                display = display[:177] + "..."
            print(f"  from failed job {source_job_id}: {kind} {display}")


def _create_backup(conn: sqlite3.Connection, db_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"library.db.manual-stuck-ingestion-repair.{timestamp}.bak"
    dst = sqlite3.connect(str(backup_path))
    try:
        conn.backup(dst)
    finally:
        dst.close()
    return backup_path


def apply_plan(
    conn: sqlite3.Connection,
    db_path: Path,
    plan: RepairPlan,
    *,
    backup: bool,
) -> dict[str, Any]:
    if plan.unmatched_docs:
        raise RuntimeError(
            f"refusing to apply with {len(plan.unmatched_docs)} unmatched non-bluesky docs"
        )

    backup_path: Path | None = None
    if backup:
        backup_path = _create_backup(conn, db_path)
        logger.info("created backup %s", backup_path)

    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        for chunk_id in plan.orphan_vector_ids:
            conn.execute("DELETE FROM chunk_vectors WHERE chunk_id = ?", (chunk_id,))

        for doc in plan.partial_docs:
            conn.execute(
                """
                DELETE FROM documents
                 WHERE id = ?
                   AND status = 'ingesting'
                   AND NOT EXISTS (
                       SELECT 1 FROM chunks c WHERE c.document_id = documents.id
                   )
                """,
                (doc.id,),
            )

        enqueued: list[dict[str, Any]] = []
        for kind, payload, source_job_id in plan.jobs_to_enqueue:
            existing = conn.execute(
                """
                SELECT id
                  FROM job_queue
                 WHERE kind = ?
                   AND payload = ?
                   AND status IN ('queued', 'running')
                 LIMIT 1
                """,
                (kind, json.dumps(payload)),
            ).fetchone()
            if existing is not None:
                enqueued.append(
                    {
                        "id": int(existing["id"]),
                        "kind": kind,
                        "source_failed_job_id": source_job_id,
                        "status": "already_pending",
                    }
                )
                continue
            job = submit(conn, kind, payload)
            enqueued.append(
                {
                    "id": job["id"],
                    "kind": kind,
                    "source_failed_job_id": source_job_id,
                    "status": job["status"],
                }
            )

        conn.execute(
            """
            INSERT INTO scheduled_runs (name, status, details, started_at, completed_at)
            VALUES (?, 'success', ?, ?, ?)
            """,
            (
                "manual_stuck_ingestion_repair",
                json.dumps(
                    {
                        "deleted_partial_docs": len(plan.partial_docs),
                        "deleted_bluesky_docs": len(plan.bluesky_docs),
                        "deleted_orphan_vectors": len(plan.orphan_vector_ids),
                        "enqueued_jobs": len(enqueued),
                        "backup_path": str(backup_path) if backup_path else None,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                now_iso,
                now_iso,
            ),
        )

    return {
        "backup_path": str(backup_path) if backup_path else None,
        "deleted_partial_docs": len(plan.partial_docs),
        "deleted_bluesky_docs": len(plan.bluesky_docs),
        "deleted_orphan_vectors": len(plan.orphan_vector_ids),
        "enqueued_jobs": enqueued,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default=commonplace_db.DB_PATH,
        help="Path to library.db.",
    )
    parser.add_argument(
        "--min-age-minutes",
        type=int,
        default=30,
        help="Only repair ingesting zero-chunk docs at least this old.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the repair. Without this flag, prints a dry-run plan.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit dry-run alias; this is the default when --apply is absent.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create a manual sqlite backup before applying.",
    )
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    db_path = Path(args.db_path).expanduser()
    conn = commonplace_db.connect(db_path)
    try:
        commonplace_db.migrate(conn)
        plan = build_plan(conn, args.min_age_minutes)
        _print_plan(plan)
        if not args.apply:
            print("\ndry-run only; rerun with --apply to mutate the database")
            return 0
        result = apply_plan(conn, db_path, plan, backup=not args.no_backup)
    finally:
        conn.close()

    print("\napplied")
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    if plan.bluesky_docs:
        print("\nnext: run scripts/bluesky_backfill.py to recreate deleted Bluesky posts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
