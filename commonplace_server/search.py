"""Semantic search across all Commonplace content types.

Embeds a query, runs KNN against the sqlite-vec chunk_vectors table,
joins back to chunks and documents, applies optional filters, and
returns ranked results.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    """A single search hit returned by :func:`search`."""

    score: float
    document_id: int
    content_type: str
    source_id: str | None
    source_uri: str | None
    title: str | None
    chunk_text: str
    created_at: str


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_LIMIT = 50
_DEFAULT_LIMIT = 10

# When filters are applied post-KNN, we fetch extra candidates so that
# after filtering we still have enough results.  The multiplier controls
# how many extra rows to retrieve from the vec0 table.
_KNN_OVERFETCH_MULTIPLIER = 5


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search(
    conn: sqlite3.Connection,
    query_embedding: bytes,
    *,
    content_type: str | None = None,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[SearchResult]:
    """Semantic search across all embedded content.

    Parameters
    ----------
    conn:
        An open SQLite connection with sqlite-vec loaded and migrations applied.
    query_embedding:
        The query vector as a packed little-endian float32 blob
        (use :func:`commonplace_server.embedding.pack_vector`).
    content_type:
        Filter to a single content type (e.g. ``"book"``, ``"bluesky"``).
    source:
        Free-text substring match against ``documents.source_uri``.
    date_from:
        ISO 8601 date string; only include documents created on or after this date.
    date_to:
        ISO 8601 date string; only include documents created on or before this date.
    limit:
        Maximum number of results to return (default 10, max 50).

    Returns
    -------
    List of :class:`SearchResult` ordered by ascending distance (best match first).
    """
    limit = max(1, min(limit, _MAX_LIMIT))

    has_filters = any([content_type, source, date_from, date_to])
    knn_limit = limit * _KNN_OVERFETCH_MULTIPLIER if has_filters else limit

    # Step 1: KNN search against vec0 table
    knn_rows = conn.execute(
        "SELECT chunk_id, distance FROM chunk_vectors "
        "WHERE embedding MATCH ? "
        "ORDER BY distance "
        "LIMIT ?",
        (query_embedding, knn_limit),
    ).fetchall()

    if not knn_rows:
        return []

    # Build a mapping of chunk_id -> distance for later use
    chunk_distances: dict[int, float] = {
        row["chunk_id"]: row["distance"] for row in knn_rows
    }
    chunk_ids = list(chunk_distances.keys())

    # Step 2: Join chunks -> documents with optional filters
    placeholders = ",".join("?" * len(chunk_ids))
    sql = (
        "SELECT c.id AS chunk_id, c.text AS chunk_text, "
        "       d.id AS document_id, d.content_type, d.source_id, "
        "       d.source_uri, d.title, d.created_at "
        "FROM chunks c "
        "JOIN documents d ON c.document_id = d.id "
        f"WHERE c.id IN ({placeholders})"
    )
    params: list[object] = list(chunk_ids)

    if content_type:
        sql += " AND d.content_type = ?"
        params.append(content_type)
    if source:
        sql += " AND d.source_uri LIKE ?"
        params.append(f"%{source}%")
    if date_from:
        sql += " AND d.created_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND d.created_at <= ?"
        params.append(date_to + "T23:59:59Z" if len(date_to) == 10 else date_to)

    joined_rows = conn.execute(sql, params).fetchall()

    # Step 3: Build results, attach distance scores, sort, and limit
    results: list[SearchResult] = []
    for row in joined_rows:
        results.append(
            SearchResult(
                score=chunk_distances[row["chunk_id"]],
                document_id=row["document_id"],
                content_type=row["content_type"],
                source_id=row["source_id"],
                source_uri=row["source_uri"],
                title=row["title"],
                chunk_text=row["chunk_text"],
                created_at=row["created_at"],
            )
        )

    results.sort(key=lambda r: r.score)
    return results[:limit]


def results_to_dicts(results: list[SearchResult]) -> list[dict[str, object]]:
    """Convert a list of SearchResult to a list of plain dicts for JSON serialisation."""
    return [asdict(r) for r in results]
