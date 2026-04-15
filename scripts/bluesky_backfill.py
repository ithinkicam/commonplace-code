#!/usr/bin/env python3
"""bluesky_backfill.py — ingest or count all Bluesky posts for the authenticated user.

Usage
-----
    python scripts/bluesky_backfill.py [--dry-run]

Options
-------
--dry-run   Count posts that would be ingested without writing to the database.
            Authenticates and pages through the feed but inserts nothing.

Exit codes
----------
  0  success
  1  fatal error (auth failure, API error, etc.)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure repo root on sys.path when run directly
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
_log = logging.getLogger("bluesky_backfill")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _count_posts_dry_run(handle: str, client: object) -> int:
    """Page through the author feed and count posts without ingesting.

    Returns the number of original posts (reposts excluded).
    """
    from atproto import models  # type: ignore[import-untyped]

    count = 0
    cursor = None
    page_num = 0
    PAGE_LIMIT = 100

    while True:
        page_num += 1
        _log.info("Fetching page %d (cursor=%s)…", page_num, cursor or "start")

        try:
            resp = client.get_author_feed(  # type: ignore[attr-defined]
                actor=handle, cursor=cursor, limit=PAGE_LIMIT
            )
        except Exception as exc:
            msg = str(exc)
            if "rate" in msg.lower() or "429" in msg:
                _log.error("Rate limit hit on page %d: %s", page_num, exc)
                _log.error("Back off and retry later.  Partial count so far: %d", count)
                sys.exit(1)
            _log.error("Feed fetch failed on page %d: %s", page_num, exc)
            sys.exit(1)

        feed = getattr(resp, "feed", []) or []
        if not feed:
            break

        for item in feed:
            reason = getattr(item, "reason", None)
            if isinstance(reason, models.AppBskyFeedDefs.ReasonRepost):
                continue
            post = getattr(item, "post", None)
            if post is None:
                continue
            record = getattr(post, "record", None)
            text = getattr(record, "text", "") or "" if record else ""
            if not text.strip():
                continue
            count += 1

        cursor = getattr(resp, "cursor", None)
        if not cursor:
            break

    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest all Bluesky posts into the Commonplace DB."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count posts without ingesting. No DB writes.",
    )
    args = parser.parse_args(argv)

    t0 = time.monotonic()

    # Auth
    _log.info("Authenticating with Bluesky…")
    try:
        from commonplace_worker.bluesky_auth import (  # noqa: PLC0415
            create_session,
            get_authenticated_client,
        )

        session = create_session()
        handle: str = session["handle"]
        client = get_authenticated_client()
        _log.info("Authenticated as %s", handle)
    except Exception as exc:
        _log.error("Authentication failed: %s", exc)
        _log.error("Check or rotate the app password in the keychain.")
        return 1

    if args.dry_run:
        _log.info("--- DRY RUN — no data will be written ---")
        post_count = _count_posts_dry_run(handle, client)
        elapsed = time.monotonic() - t0

        print("\n--- Bluesky backfill dry-run summary ---")
        print(f"  handle:        {handle}")
        print(f"  posts_found:   {post_count}")
        print(f"  would_ingest:  {post_count}")
        print(f"  elapsed_s:     {elapsed:.1f}")
        print("(dry-run: no rows written)")
        return 0

    # Real ingest
    _log.info("Starting backfill ingest (this may take a while)…")
    from commonplace_db.db import connect, migrate  # noqa: PLC0415
    from commonplace_worker.handlers.bluesky import handle_bluesky_ingest  # noqa: PLC0415

    conn = connect()
    migrate(conn)

    try:
        result = handle_bluesky_ingest(
            {"mode": "backfill"},
            conn,
            _client=client,
        )
    except Exception as exc:
        _log.error("Backfill failed: %s", exc)
        return 1

    elapsed = time.monotonic() - t0

    print("\n--- Bluesky backfill summary ---")
    print(f"  handle:         {handle}")
    print(f"  posts_fetched:  {result['posts_fetched']}")
    print(f"  posts_new:      {result['posts_new']}")
    print(f"  posts_skipped:  {result['posts_skipped']}")
    print(f"  elapsed_ms:     {result['elapsed_ms']:.0f}")
    print(f"  total_elapsed_s: {elapsed:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
