"""Video metadata ingest handlers for Phase 5b (movies and TV shows).

handle_ingest_movie(payload, conn)   — handler for 'ingest_movie' jobs
handle_ingest_tv(payload, conn)      — handler for 'ingest_tv' jobs

Both handlers:
  1. Parse the filesystem_path filename via video_filename.parse()
  2. Search TMDB for the title + year
  3. Pick the best match (year within ±1)
  4. Fetch full TMDB details
  5. Insert or update a documents row (content_type='movie'|'tv_show')
  6. Embed the plot summary via commonplace_server.pipeline.embed_document
  7. Return idempotently (skip if filesystem_path already has plot IS NOT NULL)

Job payloads:
  {"path": "/abs/path/to/movie/dir_or_file"}
  {"path": "/abs/path/to/tv/season/dir"}
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from commonplace_worker.handlers.video_filename import parse as parse_filename

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class VideoDriveNotMounted(RuntimeError):
    """Raised when /Volumes/Expansion/ is not mounted."""


# ---------------------------------------------------------------------------
# Public handlers
# ---------------------------------------------------------------------------


def handle_ingest_movie(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _tmdb_search=None,
    _tmdb_details=None,
) -> dict[str, Any]:
    """Worker handler for 'ingest_movie' jobs.

    Parameters
    ----------
    payload:
        Must contain ``path`` — absolute path to the movie directory or file.
    conn:
        Open SQLite connection with migrations applied.
    _tmdb_search:
        Injectable for testing; defaults to commonplace_server.tmdb.search_movie.
    _tmdb_details:
        Injectable for testing; defaults to commonplace_server.tmdb.get_movie_details.

    Returns
    -------
    dict with keys: document_id, action (inserted|updated|skipped), elapsed_ms.
    """
    return _handle_video(
        payload=payload,
        conn=conn,
        is_tv=False,
        content_type="movie",
        _tmdb_search=_tmdb_search,
        _tmdb_details=_tmdb_details,
    )


def handle_ingest_tv(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _tmdb_search=None,
    _tmdb_details=None,
) -> dict[str, Any]:
    """Worker handler for 'ingest_tv' jobs.

    Parameters
    ----------
    payload:
        Must contain ``path`` — absolute path to the TV show directory.
    conn:
        Open SQLite connection with migrations applied.
    _tmdb_search:
        Injectable for testing; defaults to commonplace_server.tmdb.search_tv.
    _tmdb_details:
        Injectable for testing; defaults to commonplace_server.tmdb.get_tv_details.

    Returns
    -------
    dict with keys: document_id, action (inserted|updated|skipped), elapsed_ms.
    """
    return _handle_video(
        payload=payload,
        conn=conn,
        is_tv=True,
        content_type="tv_show",
        _tmdb_search=_tmdb_search,
        _tmdb_details=_tmdb_details,
    )


# ---------------------------------------------------------------------------
# Shared implementation
# ---------------------------------------------------------------------------


def _handle_video(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    is_tv: bool,
    content_type: str,
    _tmdb_search=None,
    _tmdb_details=None,
) -> dict[str, Any]:
    t0 = time.monotonic()

    path_str = payload.get("path")
    if not isinstance(path_str, str) or not path_str:
        raise ValueError(f"video ingest payload missing 'path': {payload!r}")

    fs_path = Path(path_str)
    _check_drive_mounted(fs_path)

    if not fs_path.exists():
        raise FileNotFoundError(f"video path not found: {fs_path}")

    # Idempotency: if filesystem_path already has a row with plot, skip
    existing = conn.execute(
        "SELECT id, plot FROM documents WHERE filesystem_path = ?",
        (str(fs_path),),
    ).fetchone()
    if existing is not None and existing["plot"] is not None:
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "skipping already-enriched %s (document_id=%d)", fs_path.name, existing["id"]
        )
        return {
            "document_id": existing["id"],
            "action": "skipped",
            "elapsed_ms": elapsed_ms,
        }

    # Parse filename
    try:
        parsed = parse_filename(fs_path.name, is_tv=is_tv)
    except ValueError as exc:
        raise ValueError(f"could not parse video filename: {exc}") from exc

    title = parsed["title"]
    year = parsed.get("year")

    logger.debug("parsed: title=%r year=%r is_tv=%r path=%s", title, year, is_tv, fs_path)

    # TMDB enrichment
    tmdb_result = _do_tmdb_search(
        title=title,
        year=year,
        is_tv=is_tv,
        _tmdb_search=_tmdb_search,
    )
    details = _do_tmdb_details(
        tmdb_result=tmdb_result,
        is_tv=is_tv,
        _tmdb_details=_tmdb_details,
    )

    # Build document fields from TMDB details (or parsed fallback)
    doc_fields = _build_doc_fields(
        fs_path=fs_path,
        parsed=parsed,
        is_tv=is_tv,
        tmdb_result=tmdb_result,
        details=details,
    )

    # Insert or update
    if existing is not None:
        # Row exists but no plot — update with enrichment data
        doc_id = existing["id"]
        _update_document(conn, doc_id, doc_fields)
        action = "updated"
    else:
        doc_id = _insert_document(conn, content_type, doc_fields)
        action = "inserted"

    # Embed plot summary if we have one
    plot: str | None = doc_fields.get("plot")
    if plot:
        _embed_plot(conn, doc_id, plot, title)

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "video ingested: document_id=%d action=%s elapsed_ms=%.0f path=%s",
        doc_id,
        action,
        elapsed_ms,
        fs_path,
    )
    return {"document_id": doc_id, "action": action, "elapsed_ms": elapsed_ms}


# ---------------------------------------------------------------------------
# Drive mount check
# ---------------------------------------------------------------------------


def _check_drive_mounted(path: Path) -> None:
    """Raise VideoDriveNotMounted if /Volumes/Expansion/ is absent."""
    expansion = Path("/Volumes/Expansion")
    if "/Volumes/Expansion" in str(path) and not expansion.exists():
        raise VideoDriveNotMounted(
            "/Volumes/Expansion/ is not mounted — cannot ingest video. "
            "Attach the external drive and retry."
        )


# ---------------------------------------------------------------------------
# TMDB helpers
# ---------------------------------------------------------------------------


def _do_tmdb_search(
    title: str,
    year: int | None,
    *,
    is_tv: bool,
    _tmdb_search=None,
) -> dict[str, Any] | None:
    """Run TMDB search and apply year-proximity filter."""
    from commonplace_server.tmdb import (
        pick_best_movie_match,
        pick_best_tv_match,
        search_movie,
        search_tv,
    )

    if _tmdb_search is not None:
        raw_result = _tmdb_search(title, year)
    elif is_tv:
        raw_result = search_tv(title, year)
    else:
        raw_result = search_movie(title, year)

    if raw_result is None:
        return None

    if is_tv:
        return pick_best_tv_match(raw_result, year)
    return pick_best_movie_match(raw_result, year)


def _do_tmdb_details(
    tmdb_result: dict[str, Any] | None,
    *,
    is_tv: bool,
    _tmdb_details=None,
) -> dict[str, Any] | None:
    """Fetch full TMDB details for the matched result."""
    if tmdb_result is None:
        return None

    tmdb_id: int | None = tmdb_result.get("id")
    if tmdb_id is None:
        return None

    from commonplace_server.tmdb import get_movie_details, get_tv_details

    if _tmdb_details is not None:
        return _tmdb_details(tmdb_id)
    if is_tv:
        return get_tv_details(tmdb_id)
    return get_movie_details(tmdb_id)


# ---------------------------------------------------------------------------
# Document field builder
# ---------------------------------------------------------------------------


def _build_doc_fields(
    fs_path: Path,
    parsed: dict[str, Any],
    *,
    is_tv: bool,
    tmdb_result: dict[str, Any] | None,
    details: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a flat dict of document column values from parsed + TMDB data."""
    title: str = parsed["title"]
    year: int | None = parsed.get("year")

    # Prefer TMDB title over parsed title (canonical spelling)
    if details is not None:
        title = details.get("name") or title if is_tv else details.get("title") or title

    # Release year from TMDB
    if details is not None and year is None:
        if is_tv:
            fad = details.get("first_air_date", "") or ""
            if fad:
                with contextlib.suppress(ValueError, IndexError):
                    year = int(fad[:4])
        else:
            rd = details.get("release_date", "") or ""
            if rd:
                with contextlib.suppress(ValueError, IndexError):
                    year = int(rd[:4])

    # Plot
    plot: str | None = None
    if details is not None:
        plot = details.get("overview") or None

    # Genres: JSON array string
    genres: str | None = None
    if details is not None:
        genre_list = details.get("genres", []) or []
        names = [g.get("name", "") for g in genre_list if g.get("name")]
        if names:
            genres = json.dumps(names)

    # Director (movies only)
    director: str | None = None
    if details is not None and not is_tv:
        director = details.get("director")

    # Season count (TV only)
    season_count: int | None = None
    if details is not None and is_tv:
        season_count = details.get("number_of_seasons")

    # TMDB id
    tmdb_id: int | None = None
    if tmdb_result is not None:
        tmdb_id = tmdb_result.get("id")

    return {
        "title": title,
        "filesystem_path": str(fs_path),
        "media_type": "tv_show" if is_tv else "movie",
        "release_year": year,
        "plot": plot,
        "genres": genres,
        "director": director,
        "season_count": season_count,
        "tmdb_id": tmdb_id,
    }


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------


def _insert_document(
    conn: sqlite3.Connection,
    content_type: str,
    fields: dict[str, Any],
) -> int:
    """Insert a new documents row and return its id."""
    content_hash = hashlib.sha256(fields["filesystem_path"].encode()).hexdigest()
    with conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO documents
                (content_type, source_uri, title, content_hash, status,
                 filesystem_path, media_type, release_year, plot, genres,
                 director, season_count, tmdb_id)
            VALUES
                (?, ?, ?, ?, 'complete', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                content_type,
                fields["filesystem_path"],
                fields["title"],
                content_hash,
                fields["filesystem_path"],
                fields["media_type"],
                fields["release_year"],
                fields["plot"],
                fields["genres"],
                fields["director"],
                fields["season_count"],
                fields["tmdb_id"],
            ),
        )
    if cur.lastrowid and cur.lastrowid > 0:
        return cur.lastrowid

    # Row already existed (IGNORE fired); fetch its id
    row = conn.execute(
        "SELECT id FROM documents WHERE filesystem_path = ?",
        (fields["filesystem_path"],),
    ).fetchone()
    return row["id"] if row else 0


def _update_document(
    conn: sqlite3.Connection,
    doc_id: int,
    fields: dict[str, Any],
) -> None:
    """Update an existing documents row with enrichment data."""
    with conn:
        conn.execute(
            """
            UPDATE documents
               SET title        = ?,
                   media_type   = ?,
                   release_year = ?,
                   plot         = ?,
                   genres       = ?,
                   director     = ?,
                   season_count = ?,
                   tmdb_id      = ?,
                   status       = 'complete',
                   updated_at   = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
             WHERE id = ?
            """,
            (
                fields["title"],
                fields["media_type"],
                fields["release_year"],
                fields["plot"],
                fields["genres"],
                fields["director"],
                fields["season_count"],
                fields["tmdb_id"],
                doc_id,
            ),
        )


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


def _embed_plot(
    conn: sqlite3.Connection,
    doc_id: int,
    plot: str,
    title: str,
) -> None:
    """Embed the plot summary text for serendipity search."""
    try:
        from commonplace_server.pipeline import embed_document

        embed_document(doc_id, plot, conn)
        logger.debug("embedded plot for document_id=%d title=%r", doc_id, title)
    except Exception as exc:  # noqa: BLE001
        # Embedding failure is non-fatal — the document is still stored
        logger.warning(
            "embedding failed for document_id=%d title=%r: %s", doc_id, title, exc
        )
