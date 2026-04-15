"""kindle_backfill.py — one-shot CLI for Kindle highlights backfill.

Usage
-----
    python scripts/kindle_backfill.py [--dry-run] [--since ISO8601] [--book ASIN] [--limit-books N]

Options
-------
  --dry-run            Count books + highlights without ingesting anything.
  --since <iso8601>    Limit to highlights created after this timestamp.
  --book <asin>        Scrape highlights for one book only.
  --limit-books <n>    Cap the number of books to process (useful with --dry-run).

Exit codes
----------
  0   Success (or blocked — check output)
  1   Error

Environment
-----------
  Requires the 'commonplace-kindle/session-cookies' Keychain item to be set.
  If missing, prints actionable instructions and exits 0 with status=blocked_on_cookies.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kindle highlights backfill — imports highlights from read.amazon.com"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Count books + highlights without ingesting (requires live cookies)",
    )
    parser.add_argument(
        "--since",
        metavar="ISO8601",
        default=None,
        help="Limit to highlights created after this timestamp (e.g. 2025-01-01T00:00:00Z)",
    )
    parser.add_argument(
        "--book",
        metavar="ASIN",
        default=None,
        help="Scrape highlights for one book only",
    )
    parser.add_argument(
        "--limit-books",
        metavar="N",
        type=int,
        default=None,
        help="Cap the number of books to process (useful with --dry-run)",
    )
    return parser.parse_args()


def _report(label: str, value: Any) -> None:
    print(f"{label}: {value}")


def main() -> int:
    args = _parse_args()

    from commonplace_worker.kindle_scraper import (
        KindleCapExceeded,
        KindleCookiesMissing,
        KindleSessionExpired,
        KindleStructureChanged,
        _RateLimiter,
        fetch_highlights,
        fetch_library,
        load_cookies_from_keychain,
    )

    t0 = time.monotonic()

    # --- Load cookies ---
    try:
        cookies = load_cookies_from_keychain()
    except KindleCookiesMissing as exc:
        print("status: blocked_on_cookies", file=sys.stderr)
        print(f"error: {exc}", file=sys.stderr)
        print("", file=sys.stderr)
        print("ACTION REQUIRED:", file=sys.stderr)
        print("  1. Install a cookie exporter extension in your browser", file=sys.stderr)
        print("     (e.g. 'Cookie-Editor' for Chrome/Firefox)", file=sys.stderr)
        print("  2. Navigate to https://read.amazon.com/notebook while signed in", file=sys.stderr)
        print("  3. Export all cookies as JSON to ~/Downloads/amazon-cookies.json", file=sys.stderr)
        print("  4. Run: make kindle-cookies-install COOKIES=~/Downloads/amazon-cookies.json", file=sys.stderr)
        print("  5. Then re-run this script", file=sys.stderr)
        _report("status", "blocked_on_cookies")
        return 0

    # Shared rate limiter and request counter for the whole run
    limiter = _RateLimiter()
    request_count: list[int] = [0]

    # --- Fetch library ---
    try:
        books = fetch_library(
            _cookies=cookies,
            _rate_limiter=limiter,
            _request_count=request_count,
        )
    except KindleSessionExpired as exc:
        _report("status", "blocked_on_session_rot")
        _report("error", str(exc))
        print(
            "\nSession cookies appear expired. Re-export from your browser and run:\n"
            "  make kindle-cookies-install COOKIES=~/Downloads/amazon-cookies.json",
            file=sys.stderr,
        )
        return 0
    except KindleStructureChanged as exc:
        _report("status", "failed")
        _report("error", str(exc))
        print("\nAmazon has changed their HTML. Update selectors in kindle_scraper.py.", file=sys.stderr)
        return 1
    except KindleCapExceeded as exc:
        _report("status", "failed")
        _report("error", str(exc))
        return 1

    books_found = len(books)
    _report("books_found", books_found)

    # Filter to single book if --book given
    if args.book:
        books = [b for b in books if b.asin == args.book]
        if not books:
            _report("status", "book_not_found")
            _report("error", f"ASIN {args.book!r} not found in library")
            return 1

    # Apply --limit-books
    if args.limit_books is not None and args.limit_books > 0:
        books = books[:args.limit_books]

    # --- Fetch highlights ---
    highlights_found = 0
    rate_limit_waits = 0
    last_count_before = request_count[0]

    for book in books:
        try:
            hls = fetch_highlights(
                book.asin,
                _cookies=cookies,
                _rate_limiter=limiter,
                _request_count=request_count,
            )
        except KindleSessionExpired as exc:
            _report("status", "blocked_on_session_rot")
            _report("error", str(exc))
            return 0
        except KindleStructureChanged as exc:
            _report("status", "failed")
            _report("error", str(exc))
            return 1
        except KindleCapExceeded as exc:
            _report("status", "partial_cap_exceeded")
            _report("error", str(exc))
            _report("highlights_found", highlights_found)
            _report("rate_limit_waits", rate_limit_waits)
            return 0

        # Apply --since filter
        if args.since:
            hls = [
                h for h in hls
                if h.created_at and h.created_at >= args.since
            ]

        highlights_found += len(hls)
        new_count = request_count[0]
        rate_limit_waits += new_count - last_count_before - 1
        last_count_before = new_count

    _report("highlights_found", highlights_found)
    _report("rate_limit_waits", max(0, rate_limit_waits))

    if args.dry_run:
        _report("new_documents", "(dry-run — nothing written)")
        _report("status", "dry_run_complete")
        elapsed = time.monotonic() - t0
        _report("elapsed_s", f"{elapsed:.1f}")
        return 0

    # --- Real ingest ---
    from commonplace_db.db import connect, migrate

    conn = connect()
    migrate(conn)

    from commonplace_worker.handlers.kindle import handle_kindle_ingest

    payload: dict[str, Any] = (
        {"mode": "book", "asin": args.book} if args.book else {"mode": "full"}
    )

    result = handle_kindle_ingest(payload, conn)

    _report("new_documents", result.get("highlights_new", 0))
    _report("status", result.get("status", "unknown"))

    elapsed = time.monotonic() - t0
    _report("elapsed_s", f"{elapsed:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
