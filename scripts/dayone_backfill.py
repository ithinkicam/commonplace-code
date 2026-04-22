#!/usr/bin/env python3
"""Day One ingest driver.

Reads from the local Day One sqlite store and upserts entries into the
commonplace DB via the ``handle_dayone_ingest`` handler. Idempotent —
unchanged entries are skipped via content_hash; edited entries are
re-embedded.

Usage
-----
    python scripts/dayone_backfill.py [options]

Options
-------
--dry-run            Count entries that would be processed without writing.
--since ISO8601      Only entries modified at or after this timestamp
                     (passed through as ``{"mode": "since", "iso": ...}``
                     to the handler). Default: full backfill.
--dayone-db PATH     Override the Day One sqlite path (tests / alt profiles).
                     Falls back to ``COMMONPLACE_DAYONE_DB_PATH`` env var.

Invoked by launchd agent ``com.commonplace.dayone-backfill`` every hour.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger("dayone_backfill")


def _count_dry_run(dayone_db_path: Path | None, since: str | None) -> int:
    from commonplace_worker.handlers.dayone import (
        _default_dayone_db_path,
        _fetch_entries,
        _iso_to_core_data,
    )

    path = dayone_db_path or _default_dayone_db_path()
    since_core = _iso_to_core_data(since) if since else None
    entries = _fetch_entries(path, since_core_data=since_core)
    return len(entries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--since", type=str, default=None)
    parser.add_argument("--dayone-db", type=str, default=None)
    args = parser.parse_args(argv)

    dayone_path: Path | None = Path(args.dayone_db) if args.dayone_db else None

    if args.dry_run:
        try:
            n = _count_dry_run(dayone_path, args.since)
        except FileNotFoundError as exc:
            logger.error("%s", exc)
            return 2
        print("\n--- Day One backfill dry-run summary ---")
        print(f"  entries_found: {n}")
        print(f"  since: {args.since or '(full backfill)'}")
        print("  (dry-run: no rows written)")
        return 0

    from commonplace_db.db import connect, migrate
    from commonplace_worker.handlers.dayone import handle_dayone_ingest

    conn = connect()
    migrate(conn)

    payload: dict = {"mode": "backfill"}
    if args.since:
        payload = {"mode": "since", "iso": args.since}

    try:
        result = handle_dayone_ingest(
            payload,
            conn,
            _dayone_db_path=dayone_path,
        )
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2

    print("\n--- Day One backfill summary ---")
    print(f"  inserted: {result['inserted']}")
    print(f"  updated:  {result['updated']}")
    print(f"  skipped:  {result['skipped']}")
    print(f"  elapsed_ms: {result['elapsed_ms']:.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
