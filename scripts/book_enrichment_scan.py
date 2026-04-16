#!/usr/bin/env python3
"""Book enrichment scan — enqueue ingest_book_enrichment jobs for eligible documents.

Eligible: content_type IN ('book', 'audiobook', 'storygraph_entry', 'kindle_book')
          AND (enriched_at IS NULL OR description IS NULL)

Usage
-----
    python scripts/book_enrichment_scan.py [options]

Options
-------
--dry-run                Count eligible documents without enqueuing.
--force                  Re-enrich already-enriched documents (passes force=True in payload).
--content-type <TYPE>    Scope to a single content_type (e.g. 'audiobook').
--limit N                Cap the number of jobs enqueued.
--sleep <SECS>           Sleep between enqueue calls (default 0.2s, polite rate).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# Book-like content types eligible for enrichment
ELIGIBLE_CONTENT_TYPES = ("book", "audiobook", "storygraph_entry", "kindle_book")


def _query_eligible(
    conn: object,
    content_type: str | None,
    force: bool,
) -> list[dict]:
    """Return list of {id, content_type, title, author} for eligible documents."""
    import sqlite3

    assert isinstance(conn, sqlite3.Connection)

    if force:
        # Re-enrich everything (still filter by content_type if given)
        if content_type:
            rows = conn.execute(
                """
                SELECT id, content_type, title, author
                  FROM documents
                 WHERE content_type = ?
                 ORDER BY id
                """,
                (content_type,),
            ).fetchall()
        else:
            placeholders = ",".join("?" * len(ELIGIBLE_CONTENT_TYPES))
            rows = conn.execute(
                f"""
                SELECT id, content_type, title, author
                  FROM documents
                 WHERE content_type IN ({placeholders})
                 ORDER BY id
                """,
                ELIGIBLE_CONTENT_TYPES,
            ).fetchall()
    else:
        # Only unenriched (enriched_at IS NULL OR description IS NULL)
        if content_type:
            rows = conn.execute(
                """
                SELECT id, content_type, title, author
                  FROM documents
                 WHERE content_type = ?
                   AND (enriched_at IS NULL OR description IS NULL)
                 ORDER BY id
                """,
                (content_type,),
            ).fetchall()
        else:
            placeholders = ",".join("?" * len(ELIGIBLE_CONTENT_TYPES))
            rows = conn.execute(
                f"""
                SELECT id, content_type, title, author
                  FROM documents
                 WHERE content_type IN ({placeholders})
                   AND (enriched_at IS NULL OR description IS NULL)
                 ORDER BY id
                """,
                ELIGIBLE_CONTENT_TYPES,
            ).fetchall()

    return [dict(r) for r in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enqueue ingest_book_enrichment jobs for eligible book documents."
    )
    parser.add_argument("--dry-run", action="store_true", help="Count eligible docs; do not enqueue.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-enrich already-enriched documents (passes force=True in payload).",
    )
    parser.add_argument(
        "--content-type",
        metavar="TYPE",
        default=None,
        help=f"Scope to one content_type. Choices: {', '.join(ELIGIBLE_CONTENT_TYPES)}",
    )
    parser.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=None,
        help="Cap number of jobs enqueued.",
    )
    parser.add_argument(
        "--sleep",
        metavar="SECS",
        type=float,
        default=0.2,
        help="Sleep between enqueue calls (default 0.2s).",
    )
    args = parser.parse_args(argv)

    # Validate content-type flag
    if args.content_type and args.content_type not in ELIGIBLE_CONTENT_TYPES:
        logger.error(
            "Invalid --content-type %r. Choices: %s",
            args.content_type,
            ", ".join(ELIGIBLE_CONTENT_TYPES),
        )
        return 1

    # Import DB helpers
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from commonplace_db.db import connect, migrate

    conn = connect()
    migrate(conn)

    eligible = _query_eligible(conn, args.content_type, args.force)

    # Summary by content_type for dry-run reporting
    by_type: dict[str, int] = {}
    for doc in eligible:
        ct = doc["content_type"]
        by_type[ct] = by_type.get(ct, 0) + 1

    if args.dry_run:
        print("\nEligible documents by content_type:")
        for ct in ELIGIBLE_CONTENT_TYPES:
            count = by_type.get(ct, 0)
            print(f"  {ct}: {count}")
        print(f"\nTotal eligible: {len(eligible)}")
        if args.limit is not None:
            print(f"Would enqueue: {min(len(eligible), args.limit)} (limit={args.limit})")
        else:
            print(f"Would enqueue: {len(eligible)}")
        if args.force:
            print("(--force: includes already-enriched documents)")
        return 0

    # Real run: enqueue jobs
    from commonplace_server.jobs import submit

    to_process = eligible if args.limit is None else eligible[: args.limit]
    enqueued = 0

    for doc in to_process:
        payload: dict = {"document_id": doc["id"]}
        if args.force:
            payload["force"] = True

        submit(conn, "ingest_book_enrichment", payload)
        enqueued += 1
        logger.info(
            "ENQUEUED document_id=%d content_type=%s title=%r",
            doc["id"],
            doc["content_type"],
            doc["title"],
        )

        if args.sleep > 0:
            time.sleep(args.sleep)

    print(f"\nEnqueued {enqueued} ingest_book_enrichment jobs.")
    if args.force:
        print("(--force: will re-enrich already-enriched documents)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
