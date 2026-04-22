"""Book enrichment handler.

ingest_book_enrichment(payload, conn) enriches book-typed documents with
public metadata from Open Library (primary) and Google Books (fallback).

Enrichment adds:
  - description: publisher/author description of the book
  - subjects: JSON array of subject/genre strings
  - first_published_year: integer year of first publication
  - isbn: ISBN-13 preferred, ISBN-10 fallback
  - enrichment_source: 'open_library' or 'google_books'
  - enriched_at: ISO 8601 timestamp

After writing metadata, embeds the description via pipeline.embed_document
so the serendipity judge has something to match on even for books with few
or zero highlights.  When neither Open Library nor Google Books returns a
description, a metadata-only fallback string (title + author / narrator /
subjects / content_type) is embedded instead, so the document still gets
chunks + vectors and the judge can still match on it.

Job payload: {"document_id": int, "force": bool (optional, default False)}

Idempotent: skips already-enriched documents unless force=True.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

# Book-like content types eligible for enrichment
ELIGIBLE_CONTENT_TYPES = frozenset({"book", "audiobook", "storygraph_entry", "kindle_book"})


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def ingest_book_enrichment(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Worker handler for 'ingest_book_enrichment' jobs.

    Parameters
    ----------
    payload:
        Must contain ``document_id`` (int).
        Optional ``force`` (bool): re-enrich even if already enriched.
    conn:
        Open SQLite connection with migrations applied.

    Returns
    -------
    dict with keys: document_id, action (enriched|skipped|unenriched), elapsed_ms.
    """
    t0 = time.monotonic()

    document_id = payload.get("document_id")
    if not isinstance(document_id, int):
        raise ValueError(f"ingest_book_enrichment payload missing 'document_id': {payload!r}")

    force: bool = bool(payload.get("force", False))

    # Load document
    row = conn.execute(
        "SELECT id, content_type, title, author, narrator, subjects, "
        "enriched_at, description "
        "FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    if row is None:
        raise ValueError(f"document not found: id={document_id}")

    content_type: str = row["content_type"]
    title: str | None = row["title"]
    author: str | None = row["author"]
    narrator: str | None = row["narrator"]
    existing_subjects_json: str | None = row["subjects"]
    enriched_at: str | None = row["enriched_at"]
    existing_description: str | None = row["description"]

    # Eligibility check
    if content_type not in ELIGIBLE_CONTENT_TYPES:
        logger.info(
            "document %d has ineligible content_type=%r — skipping",
            document_id,
            content_type,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        return {"document_id": document_id, "action": "skipped", "elapsed_ms": elapsed_ms}

    # Idempotency: skip if already enriched with a description, unless force=True
    if enriched_at and existing_description and not force:
        logger.info(
            "document %d already enriched at %s — skipping (use force=True to override)",
            document_id,
            enriched_at,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        return {"document_id": document_id, "action": "skipped", "elapsed_ms": elapsed_ms}

    if not title:
        logger.warning("document %d has no title — cannot enrich", document_id)
        elapsed_ms = (time.monotonic() - t0) * 1000
        return {"document_id": document_id, "action": "skipped", "elapsed_ms": elapsed_ms}

    # Attempt enrichment: Open Library first, Google Books as fallback
    data = _try_open_library(title, author)
    if data is None or not data.get("description"):
        logger.debug(
            "Open Library had no description for document %d (%r) — trying Google Books",
            document_id,
            title,
        )
        gb_data = _try_google_books(title, author)
        if gb_data is not None:
            # Merge: prefer OL metadata where available, GB fills gaps
            if data is None:
                data = gb_data
            else:
                # OL had a result but no description; supplement from GB
                if not data.get("description") and gb_data.get("description"):
                    data["description"] = gb_data["description"]
                if not data.get("subjects") and gb_data.get("subjects"):
                    data["subjects"] = gb_data["subjects"]
                if not data.get("isbn") and gb_data.get("isbn"):
                    data["isbn"] = gb_data["isbn"]
                if not data.get("first_published_year") and gb_data.get("first_published_year"):
                    data["first_published_year"] = gb_data["first_published_year"]
                # Track the actual source of the description
                if gb_data.get("description") and not data.get("_ol_had_description"):
                    data["source"] = "google_books"

    if data is None:
        # No enrichment result at all — still try to embed a metadata-only
        # fallback so the serendipity judge has something to match on.
        fallback = _compose_fallback_embed_text(
            title=title,
            author=author,
            narrator=narrator,
            subjects_json=existing_subjects_json,
            content_type=content_type,
        )
        if fallback:
            _embed_description(conn, document_id, fallback)
            logger.info(
                "no enrichment data for document %d (title=%r); embedded metadata fallback",
                document_id,
                title,
            )
        else:
            logger.warning(
                "no enrichment data and no usable metadata for document %d "
                "(title=%r) — leaving unenriched",
                document_id,
                title,
            )
        elapsed_ms = (time.monotonic() - t0) * 1000
        return {"document_id": document_id, "action": "unenriched", "elapsed_ms": elapsed_ms}

    description: str | None = data.get("description")
    subjects: list[str] = data.get("subjects") or []
    first_published_year: int | None = data.get("first_published_year")
    isbn: str | None = data.get("isbn")
    enrichment_source: str = data.get("source", "open_library")

    # Serialise subjects as JSON array string
    subjects_json: str = json.dumps(subjects, ensure_ascii=False)

    # Write to DB
    _write_enrichment(
        conn,
        document_id=document_id,
        description=description,
        subjects_json=subjects_json,
        first_published_year=first_published_year,
        isbn=isbn,
        enrichment_source=enrichment_source,
    )

    # Embed so the serendipity judge can match on the document.  Prefer the
    # enrichment description when present; otherwise fall back to a composed
    # metadata string (title + author/narrator/subjects/content_type) using
    # the enriched subjects if we got them, or the pre-existing ones.
    if description:
        _embed_description(conn, document_id, description)
    else:
        fallback = _compose_fallback_embed_text(
            title=title,
            author=author,
            narrator=narrator,
            subjects_json=subjects_json if subjects else existing_subjects_json,
            content_type=content_type,
        )
        if fallback:
            _embed_description(conn, document_id, fallback)
            logger.info(
                "enrichment returned no description for document %d (title=%r); "
                "embedded metadata fallback",
                document_id,
                title,
            )

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "enriched document %d (title=%r) from %s in %.0fms",
        document_id,
        title,
        enrichment_source,
        elapsed_ms,
    )
    return {
        "document_id": document_id,
        "action": "enriched",
        "source": enrichment_source,
        "elapsed_ms": elapsed_ms,
    }


# ---------------------------------------------------------------------------
# API wrappers (injectable for tests)
# ---------------------------------------------------------------------------


def _try_open_library(
    title: str,
    author: str | None,
    *,
    _client: Any = None,
) -> dict[str, Any] | None:
    """Call Open Library and return normalised data dict or None."""
    try:
        if _client is not None:
            return _client.get_book_data(title, author)
        from commonplace_server.openlibrary import get_book_data

        return get_book_data(title, author)
    except Exception as exc:
        logger.warning("Open Library call failed for %r: %s", title, exc)
        return None


def _try_google_books(
    title: str,
    author: str | None,
    *,
    _client: Any = None,
) -> dict[str, Any] | None:
    """Call Google Books and return normalised data dict or None."""
    try:
        if _client is not None:
            return _client.get_book_data(title, author)
        from commonplace_server.google_books import get_book_data

        return get_book_data(title, author)
    except Exception as exc:
        logger.warning("Google Books call failed for %r: %s", title, exc)
        return None


# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------


def _write_enrichment(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    description: str | None,
    subjects_json: str,
    first_published_year: int | None,
    isbn: str | None,
    enrichment_source: str,
) -> None:
    """Update documents row with enrichment data and set enriched_at."""
    with conn:
        conn.execute(
            """
            UPDATE documents
               SET description          = ?,
                   subjects             = ?,
                   first_published_year = ?,
                   isbn                 = ?,
                   enrichment_source    = ?,
                   enriched_at          = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                   updated_at           = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
             WHERE id = ?
            """,
            (
                description,
                subjects_json,
                first_published_year,
                isbn,
                enrichment_source,
                document_id,
            ),
        )


def _embed_description(
    conn: sqlite3.Connection,
    document_id: int,
    description: str,
) -> None:
    """Embed the description text via the existing pipeline."""
    try:
        from commonplace_server.pipeline import embed_document

        embed_document(document_id, description, conn)
    except Exception as exc:
        logger.warning(
            "embed_document failed for document %d: %s — enrichment data still saved",
            document_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Fallback embed text composition
# ---------------------------------------------------------------------------


def _compose_fallback_embed_text(
    *,
    title: str | None,
    author: str | None,
    narrator: str | None,
    subjects_json: str | None,
    content_type: str | None,
) -> str | None:
    """Compose a readable fallback string from document metadata.

    Used when enrichment returns no description, so the document still gets
    embedded (and the serendipity judge has something to match on).  Returns
    None only when there is truly no usable metadata (no title) — the caller
    logs and skips in that case.
    """
    if not title or not title.strip():
        return None

    parts: list[str] = [title.strip()]

    if author and author.strip():
        parts.append(f"by {author.strip()}")

    if narrator and narrator.strip():
        parts.append(f"narrated by {narrator.strip()}")

    # subjects is stored as a JSON-encoded array string
    if subjects_json:
        try:
            subjects = json.loads(subjects_json)
        except (ValueError, TypeError):
            subjects = None
        if isinstance(subjects, list):
            cleaned = [str(s).strip() for s in subjects if s and str(s).strip()]
            if cleaned:
                parts.append("Subjects: " + ", ".join(cleaned))

    if content_type and content_type.strip():
        parts.append(f"({content_type.strip()})")

    return ". ".join(parts)
