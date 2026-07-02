#!/usr/bin/env python3
"""Watch the Notion Therapy parent page and enqueue changed session pages."""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import sys
import tempfile
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
logger = logging.getLogger("notion_therapy_watch")


def _acquire_singleton_lock(lock_name: str) -> int:
    lock_path = Path(tempfile.gettempdir()) / f"{lock_name}.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.info(
            "another %s is already running (lock=%s); exiting cleanly",
            lock_name,
            lock_path,
        )
        os.close(fd)
        sys.exit(0)
    return fd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List changes without enqueuing.")
    parser.add_argument("--parent-page-id", default=None, help="Override Therapy parent page ID.")
    parser.add_argument("--limit-pages", type=int, default=None, help="Cap pages scanned.")
    args = parser.parse_args(argv)

    _acquire_singleton_lock("commonplace_notion_therapy_watch")

    from commonplace_db.db import connect, migrate
    from commonplace_worker.notion import NotionConfigError
    from commonplace_worker.therapy_watcher import run_watch

    conn = connect()
    migrate(conn)

    try:
        result = run_watch(
            conn,
            parent_page_id=args.parent_page_id,
            dry_run=args.dry_run,
            limit_pages=args.limit_pages,
        )
    except NotionConfigError as exc:
        print("status: blocked_on_notion_token", file=sys.stderr)
        print(f"error: {exc}", file=sys.stderr)
        print(
            "action: security add-generic-password -U -a commonplace "
            "-s commonplace_notion_token -w '<notion-integration-token>'",
            file=sys.stderr,
        )
        return 0

    print("\n--- Notion Therapy watcher summary ---")
    print(f"  pages_found:       {result.pages_found}")
    print(f"  would_enqueue:     {result.enqueued}" if result.dry_run else f"  enqueued:          {result.enqueued}")
    print(f"  skipped:           {result.skipped}")
    print(f"  skipped_in_flight: {result.skipped_in_flight}")
    print(f"  dry_run:           {result.dry_run}")
    print(f"  elapsed_ms:        {result.elapsed_ms:.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
