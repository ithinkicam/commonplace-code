"""Kindle highlights ingest handler.

handle_kindle_ingest(payload, conn) is the worker handler for 'ingest_kindle' jobs.
It scrapes the user's Kindle notebook and inserts:
  - One documents row per book (content_type='kindle_book')
  - One documents row per highlight (content_type='kindle_highlight'), embedded.

Modes
-----
  {"mode": "full"}               — scan library + all highlights
  {"mode": "book", "asin": "…"} — highlights for one book only

Idempotency
-----------
Uses INSERT OR IGNORE against the (content_type, source_id) UNIQUE index
from migration 0003. Re-running is safe.

Loud alerts
-----------
On KindleStructureChanged, the handler:
  1. Writes ERROR to stderr with "KINDLE_SELECTOR_BROKEN: <selector>"
  2. Writes an alert file to ~/commonplace/alerts/kindle-broken-YYYY-MM-DD.txt
  3. Marks the job failed with full context.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ALERTS_DIR = Path.home() / "commonplace" / "alerts"


# ---------------------------------------------------------------------------
# Alert writer
# ---------------------------------------------------------------------------


def _write_alert(selector: str, url: str, page_snippet: str) -> Path:
    """Write a kindle-broken alert file to ~/commonplace/alerts/ and return the path."""
    _ALERTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.date.today().isoformat()
    alert_path = _ALERTS_DIR / f"kindle-broken-{date_str}.txt"

    content = (
        f"KINDLE SELECTOR BROKEN — {date_str}\n"
        f"{'=' * 60}\n"
        f"Selector: {selector}\n"
        f"URL: {url}\n"
        f"\nPage snippet (first 2000 chars):\n{page_snippet[:2000]}\n"
    )
    alert_path.write_text(content)
    return alert_path


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _upsert_book(conn: sqlite3.Connection, book_asin: str, title: str, authors: str) -> int | None:
    """Insert a kindle_book document row if not already present. Returns document_id or None if exists."""
    source_id = book_asin
    existing = conn.execute(
        "SELECT id FROM documents WHERE content_type = 'kindle_book' AND source_id = ?",
        (source_id,),
    ).fetchone()
    if existing is not None:
        return int(existing["id"])

    with conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO documents
                (content_type, source_uri, source_id, title, author, status)
            VALUES ('kindle_book', ?, ?, ?, ?, 'embedded')
            """,
            (f"amazon-asin:{book_asin}", source_id, title, authors),
        )
    if cursor.lastrowid and cursor.rowcount > 0:
        return int(cursor.lastrowid)

    # Row existed (IGNORE path)
    row = conn.execute(
        "SELECT id FROM documents WHERE content_type = 'kindle_book' AND source_id = ?",
        (source_id,),
    ).fetchone()
    return int(row["id"]) if row else None


def _upsert_highlight(
    conn: sqlite3.Connection,
    asin: str,
    location: str | None,
    text: str,
    note: str | None,
    authors: str,
    book_title: str,
    embedder: Any = None,
) -> tuple[int | None, bool]:
    """Insert a kindle_highlight document row if not already present.

    Returns (document_id, is_new). Calls embed_document on new highlights.
    """
    loc_key = location or hashlib.sha256(text.encode()).hexdigest()[:12]
    source_id = f"{asin}#{loc_key}"
    content_hash = hashlib.sha256((text + (note or "")).encode()).hexdigest()

    existing = conn.execute(
        "SELECT id FROM documents WHERE content_type = 'kindle_highlight' AND source_id = ?",
        (source_id,),
    ).fetchone()
    if existing is not None:
        return int(existing["id"]), False

    short_title = (text[:60] + "…") if len(text) > 60 else text

    with conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO documents
                (content_type, source_uri, source_id, title, author, content_hash, status)
            VALUES ('kindle_highlight', ?, ?, ?, ?, ?, 'pending')
            """,
            (
                f"amazon-asin:{asin}#{loc_key}",
                source_id,
                short_title,
                authors,
                content_hash,
            ),
        )

    if not cursor.lastrowid or cursor.rowcount == 0:
        row = conn.execute(
            "SELECT id FROM documents WHERE content_type = 'kindle_highlight' AND source_id = ?",
            (source_id,),
        ).fetchone()
        return (int(row["id"]) if row else None), False

    document_id = int(cursor.lastrowid)

    # Embed the highlight text
    from commonplace_server.pipeline import embed_document

    embed_text = text + ("\n\n" + note if note else "")
    embed_kwargs: dict[str, Any] = {}
    if embedder is not None:
        embed_kwargs["_embedder"] = embedder

    try:
        embed_document(document_id, embed_text, conn, **embed_kwargs)
    except Exception as exc:
        logger.error("embed_document failed for highlight source_id=%s: %s", source_id, exc)
        with conn:
            conn.execute(
                "UPDATE documents SET status = 'failed', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
                (document_id,),
            )

    return document_id, True


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_kindle_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _scraper_fetch_library: Any = None,
    _scraper_fetch_highlights: Any = None,
    _embedder: Any = None,
    _cookies: Any = None,
) -> dict[str, Any]:
    """Worker handler for 'ingest_kindle' jobs.

    Parameters
    ----------
    payload:
        {"mode": "full"} or {"mode": "book", "asin": "<ASIN>"}.
    conn:
        Open SQLite connection with migrations applied.
    _scraper_fetch_library / _scraper_fetch_highlights:
        Test seams — replace with mocks in tests.
    _embedder:
        Embedder override for tests.
    _cookies:
        httpx.Cookies override for tests.

    Returns
    -------
    dict with keys: books_processed, highlights_new, highlights_skipped, status.
    """
    from commonplace_worker.kindle_scraper import (
        KindleCapExceeded,
        KindleCookiesMissing,
        KindleSessionExpired,
        KindleStructureChanged,
        load_cookies_from_keychain,
    )
    from commonplace_worker.kindle_scraper import (
        fetch_highlights as _default_fetch_highlights,
    )
    from commonplace_worker.kindle_scraper import (
        fetch_library as _default_fetch_library,
    )

    fetch_library_fn = _scraper_fetch_library or _default_fetch_library
    fetch_highlights_fn = _scraper_fetch_highlights or _default_fetch_highlights

    mode = payload.get("mode", "full")
    target_asin: str | None = payload.get("asin")

    if mode not in ("full", "book"):
        raise ValueError(f"Unknown mode {mode!r}. Expected 'full' or 'book'.")
    if mode == "book" and not target_asin:
        raise ValueError("mode='book' requires 'asin' in payload.")

    # Load cookies (or use injected for tests)
    try:
        cookies = _cookies if _cookies is not None else load_cookies_from_keychain()
    except KindleCookiesMissing as exc:
        logger.error("blocked_on_cookies: %s", exc)
        return {
            "status": "blocked_on_cookies",
            "message": str(exc),
            "action": "Run: make kindle-cookies-install COOKIES=~/Downloads/amazon-cookies.json",
            "books_processed": 0,
            "highlights_new": 0,
            "highlights_skipped": 0,
        }

    # Fetch library
    books_processed = 0
    highlights_new = 0
    highlights_skipped = 0

    try:
        if mode == "full":
            books = fetch_library_fn(_cookies=cookies)
        else:
            # For book mode we still need book metadata — fetch library to find the book
            all_books = fetch_library_fn(_cookies=cookies)
            books = [b for b in all_books if b.asin == target_asin]
            if not books:
                # Construct a minimal book record
                from commonplace_worker.kindle_scraper import KindleBook
                assert target_asin is not None  # checked above via "mode == 'book' and not target_asin"
                books = [KindleBook(asin=target_asin, title="", authors="", cover_url=None)]

    except KindleSessionExpired as exc:
        logger.error("blocked_on_session_rot: %s", exc)
        return {
            "status": "blocked_on_session_rot",
            "message": str(exc),
            "books_processed": 0,
            "highlights_new": 0,
            "highlights_skipped": 0,
        }
    except KindleStructureChanged as exc:
        _handle_structure_changed(exc, "", "")
        return {
            "status": "failed",
            "message": str(exc),
            "books_processed": 0,
            "highlights_new": 0,
            "highlights_skipped": 0,
        }
    except KindleCapExceeded as exc:
        logger.error("Request cap exceeded fetching library: %s", exc)
        return {
            "status": "failed",
            "message": str(exc),
            "books_processed": 0,
            "highlights_new": 0,
            "highlights_skipped": 0,
        }

    for book in books:
        # Upsert book-level document
        _upsert_book(conn, book.asin, book.title, book.authors)
        books_processed += 1

        # Fetch highlights for this book
        try:
            highlight_kwargs: dict[str, Any] = {"_cookies": cookies}
            highlights = fetch_highlights_fn(book.asin, **highlight_kwargs)
        except KindleSessionExpired as exc:
            logger.error("Session expired fetching highlights for asin=%s: %s", book.asin, exc)
            return {
                "status": "blocked_on_session_rot",
                "message": str(exc),
                "books_processed": books_processed,
                "highlights_new": highlights_new,
                "highlights_skipped": highlights_skipped,
            }
        except KindleStructureChanged as exc:
            url = f"https://read.amazon.com/notebook?asin={book.asin}&contentLimitState=&"
            _handle_structure_changed(exc, url, "")
            return {
                "status": "failed",
                "message": str(exc),
                "books_processed": books_processed,
                "highlights_new": highlights_new,
                "highlights_skipped": highlights_skipped,
            }
        except KindleCapExceeded as exc:
            logger.error("Cap exceeded fetching highlights for asin=%s: %s", book.asin, exc)
            return {
                "status": "partial_cap_exceeded",
                "message": str(exc),
                "books_processed": books_processed,
                "highlights_new": highlights_new,
                "highlights_skipped": highlights_skipped,
            }

        for hl in highlights:
            _doc_id, is_new = _upsert_highlight(
                conn=conn,
                asin=book.asin,
                location=hl.location,
                text=hl.text,
                note=hl.note,
                authors=book.authors,
                book_title=book.title,
                embedder=_embedder,
            )
            if is_new:
                highlights_new += 1
            else:
                highlights_skipped += 1

    logger.info(
        "kindle ingest complete: books=%d highlights_new=%d highlights_skipped=%d",
        books_processed,
        highlights_new,
        highlights_skipped,
    )
    return {
        "status": "complete",
        "books_processed": books_processed,
        "highlights_new": highlights_new,
        "highlights_skipped": highlights_skipped,
    }


def _handle_structure_changed(exc: Exception, url: str, page_html: str) -> None:
    """Write loud alerts to stderr and alert file on KindleStructureChanged."""
    selector_info = str(exc)
    # Extract selector from message for stderr line
    stderr_line = f"KINDLE_SELECTOR_BROKEN: {selector_info}"
    print(stderr_line, file=sys.stderr, flush=True)
    logger.error(stderr_line)

    try:
        alert_path = _write_alert(
            selector=selector_info,
            url=url or "unknown",
            page_snippet=page_html or "(no page content available)",
        )
        logger.error("Alert written to: %s", alert_path)
    except Exception as alert_exc:
        logger.error("Failed to write alert file: %s", alert_exc)
