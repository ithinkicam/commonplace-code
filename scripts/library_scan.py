#!/usr/bin/env python3
"""Library scan — walk the books folder and enqueue ingest_library jobs.

Usage
-----
    python scripts/library_scan.py [--dry-run] [--since <iso8601>] [--library-path <path>]

Options
-------
--dry-run       List files without enqueuing jobs.
--since DATE    Only process files modified after this ISO-8601 timestamp.
--library-path  Override the default books folder (for testing).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_LIBRARY_PATH = (
    "/Users/cameronlewis/Library/CloudStorage/"
    "GoogleDrive-camlewis35@gmail.com/My Drive/books/"
)

SUPPORTED_SUFFIXES = {".epub", ".pdf", ".mobi", ".azw3"}
SKIP_SUFFIXES = {".chm"}

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan library folder and enqueue ingest jobs.")
    parser.add_argument("--dry-run", action="store_true", help="List files; do not enqueue.")
    parser.add_argument(
        "--since",
        metavar="ISO8601",
        help="Limit to files modified after this timestamp (e.g. 2026-01-01T00:00:00Z).",
    )
    parser.add_argument(
        "--library-path",
        metavar="PATH",
        default=DEFAULT_LIBRARY_PATH,
        help="Override the books folder path.",
    )
    args = parser.parse_args(argv)

    library_path = Path(args.library_path)
    if not library_path.exists():
        logger.error(
            "Library path does not exist: %s — is Google Drive syncing?", library_path
        )
        return 1

    # Parse --since
    since_dt: datetime | None = None
    if args.since:
        try:
            since_dt = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=UTC)
        except ValueError:
            logger.error("--since value %r is not valid ISO-8601", args.since)
            return 1

    # Walk the library.  We stat() once per file here and carry the stat
    # result forward so the fast-path dedupe below doesn't re-stat.
    found: list[tuple[Path, os.stat_result]] = []
    skipped_format: list[tuple[Path, str]] = []
    skipped_since: list[Path] = []

    for p in sorted(library_path.rglob("*")):
        if not p.is_file():
            continue
        suffix = p.suffix.lower()

        if suffix in SKIP_SUFFIXES:
            reason = f"unsupported format {suffix!r} (chm outlier)"
            skipped_format.append((p, reason))
            logger.info("SKIP %s — %s", p.name, reason)
            continue

        if suffix not in SUPPORTED_SUFFIXES:
            reason = f"unsupported format {suffix!r}"
            skipped_format.append((p, reason))
            logger.info("SKIP %s — %s", p.name, reason)
            continue

        st = p.stat()

        if since_dt is not None:
            mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC)
            if mtime <= since_dt:
                skipped_since.append(p)
                continue

        found.append((p, st))

    # Dedupe: prefer a cheap path+size+mtime fast-path to avoid re-hashing
    # (which would re-materialize Google Drive On-Demand files on every run).
    # Fall back to content_hash only when the fast-path misses.
    already_ingested_fast: list[Path] = []   # path+size+mtime match
    already_ingested_hash: list[Path] = []   # content_hash match (stats missing/stale)
    enqueue_list: list[Path] = []

    if not args.dry_run and found:
        import sys
        from pathlib import Path as _Path

        # Add repo root to sys.path so we can import commonplace_db
        repo_root = _Path(__file__).parent.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))

        from commonplace_db.db import connect, migrate
        from commonplace_server.jobs import submit
        from commonplace_worker.handlers.library import _sha256

        conn = connect()
        migrate(conn)

        for book, st in found:
            # Fast-path: path + size + mtime match an existing row.
            row = conn.execute(
                "SELECT id, file_size, file_mtime FROM documents WHERE source_uri = ?",
                (str(book),),
            ).fetchone()
            if (
                row is not None
                and row["file_size"] is not None
                and row["file_mtime"] is not None
                and row["file_size"] == st.st_size
                and row["file_mtime"] == st.st_mtime
            ):
                already_ingested_fast.append(book)
                logger.info(
                    "SKIP %s — already ingested (fast-path, document_id=%d)",
                    book.name,
                    row["id"],
                )
                continue

            # Slow-path fallback: hash the file and look up by content_hash.
            # This still materializes the file, but only for rows with missing
            # or stale stat columns (e.g. pre-migration rows).  Handler owns
            # stat writes; we do NOT retro-fill here.
            content_hash = _sha256(book)
            existing = conn.execute(
                "SELECT id FROM documents WHERE content_hash = ?", (content_hash,)
            ).fetchone()
            if existing is not None:
                already_ingested_hash.append(book)
                logger.info(
                    "SKIP %s — already ingested (content_hash, document_id=%d)",
                    book.name,
                    existing["id"],
                )
            else:
                submit(conn, "ingest_library", {"path": str(book)})
                enqueue_list.append(book)
                logger.info("ENQUEUED %s", book.name)
    else:
        # Dry-run: just list what would be enqueued
        for book, _st in found:
            logger.info("WOULD ENQUEUE %s", book.name)
        enqueue_list = [p for p, _st in found]  # for counting

    # Report
    total_found = len(found)
    total_enqueued = len(enqueue_list) if not args.dry_run else len(found)
    total_skipped_format = len(skipped_format)
    total_skipped_since = len(skipped_since)
    total_skipped_fast = len(already_ingested_fast)
    total_skipped_hash = len(already_ingested_hash)
    # Preserve the existing summary key (total across both dedupe paths) for
    # back-compat with any log scrapers, and add a fast-path breakdown.
    total_skipped_ingested = total_skipped_fast + total_skipped_hash

    print(
        f"\nSummary: found={total_found} "
        f"enqueued={'(dry-run) ' if args.dry_run else ''}{total_enqueued} "
        f"skipped_format={total_skipped_format} "
        f"skipped_since={total_skipped_since} "
        f"skipped_already_ingested={total_skipped_ingested} "
        f"skipped_fast_path={total_skipped_fast} "
        f"skipped_hash={total_skipped_hash}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
