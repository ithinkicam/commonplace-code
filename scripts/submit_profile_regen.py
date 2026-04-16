#!/usr/bin/env python3
"""Submit a regenerate_profile job to the Commonplace worker queue.

Usage
-----
    python scripts/submit_profile_regen.py [--dry-run]

Options
-------
--dry-run   Print what would be submitted without actually enqueuing.

The worker will pick up the job on its next poll and run the full
profile-regeneration pipeline (see commonplace_worker/handlers/profile.py).

To verify the job status after submission::

    from commonplace_db.db import connect, migrate
    from commonplace_server.jobs import status
    conn = connect(); migrate(conn)
    print(status(conn, <job_id>))
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enqueue a regenerate_profile job in the Commonplace worker queue."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be submitted without enqueuing.",
    )
    args = parser.parse_args(argv)

    # Add repo root to sys.path so we can import commonplace packages
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    if args.dry_run:
        logger.info("DRY RUN — would submit: kind=regenerate_profile payload={}")
        print('{"id": null, "status": "dry-run", "kind": "regenerate_profile"}')
        return 0

    from commonplace_db.db import connect, migrate
    from commonplace_server.jobs import submit

    conn = connect()
    migrate(conn)

    result = submit(conn, "regenerate_profile", {})
    logger.info(
        "submitted regenerate_profile job: id=%s status=%s",
        result["id"],
        result["status"],
    )
    print(f"job_id={result['id']} status={result['status']} kind={result['kind']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
