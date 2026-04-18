"""Semantic search across all Commonplace content types.

Embeds a query, runs KNN against the sqlite-vec chunk_vectors table,
joins back to chunks and documents, applies optional filters, and
returns ranked results.
"""

from __future__ import annotations

import re as _re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date as _date

from dateutil.easter import EASTER_ORTHODOX, EASTER_WESTERN, easter

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

# Regex for easter-offset date_rules, e.g. "easter+0", "easter-46"
_EASTER_RULE_RE = _re.compile(r"^easter([+-]\d+)$")


# ---------------------------------------------------------------------------
# Internal calendar helpers
# ---------------------------------------------------------------------------


def _resolve_feast_date(date_rule: str, year: int, tradition: str | None) -> _date | None:
    """Resolve a feast ``date_rule`` to a concrete :class:`~datetime.date` for *year*.

    Handles:
    - Fixed rules: ``"MM-DD"`` strings.
    - Movable rules: ``"easter±N"`` offsets (Western Easter for anglican/shared,
      Orthodox for byzantine).

    Returns ``None`` if the rule cannot be resolved.
    """
    # Fixed feast: "MM-DD"
    if len(date_rule) == 5 and date_rule[2] == "-":
        try:
            month, day = int(date_rule[:2]), int(date_rule[3:])
            return _date(year, month, day)
        except ValueError:
            return None

    # Movable feast: "easter+N" / "easter-N"
    m = _EASTER_RULE_RE.match(date_rule)
    if m:
        from datetime import timedelta

        method = EASTER_ORTHODOX if tradition == "byzantine" else EASTER_WESTERN
        easter_date: _date = easter(year, method=method)  # type: ignore[arg-type]
        offset = int(m.group(1))
        return easter_date + timedelta(days=offset)

    return None


def _feast_ids_in_calendar_range(
    conn: sqlite3.Connection,
    calendar_from: _date,
    calendar_to: _date,
    year: int,
    tradition: str | None,
) -> list[int]:
    """Return feast IDs whose resolved date for *year* falls in [calendar_from, calendar_to]."""
    rows = conn.execute("SELECT id, date_rule, tradition FROM feast").fetchall()
    matching: list[int] = []
    for row in rows:
        row_dict = dict(row) if not isinstance(row, dict) else row
        feast_tradition = row_dict.get("tradition")
        resolved = _resolve_feast_date(
            row_dict["date_rule"],
            year,
            feast_tradition,
        )
        if resolved is not None and calendar_from <= resolved <= calendar_to:
            matching.append(row_dict["id"])
    return matching


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
    calendar_year: int | None = None,
    category: str | None = None,
    genre: str | None = None,
    tradition: str | None = None,
    feast_name: str | None = None,
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
        When set to ``"liturgical_unit"``, ``date_from``/``date_to`` are
        interpreted as calendar dates and matched against resolved feast dates
        rather than ``documents.created_at`` (Option A overload — see note below).
    source:
        Free-text substring match against ``documents.source_uri``.
    date_from:
        ISO 8601 date (``YYYY-MM-DD``).  For non-liturgical content: lower bound
        on ``documents.created_at``.  For ``content_type="liturgical_unit"``:
        lower bound on the feast's resolved calendar date (Option A overload).
    date_to:
        ISO 8601 date (``YYYY-MM-DD``).  Same dual semantics as *date_from*.

        NOTE (Option A overload): when ``content_type="liturgical_unit"`` is
        also set, ``date_from``/``date_to`` are reinterpreted as liturgical
        calendar bounds and resolved against feast ``date_rule`` values.  For
        all other content types the original ``created_at`` semantics apply.
        This overload avoids bloating the parameter list; the trade-off is
        that the combination ``content_type="liturgical_unit"`` +
        ``date_from``/``date_to`` cannot simultaneously filter ``created_at``.
    calendar_year:
        Year used when resolving movable feasts under the liturgical calendar
        overload (default: current year).  Ignored for non-liturgical queries.
    category:
        Equality filter on ``liturgical_unit_meta.category``
        (e.g. ``"liturgical_proper"``, ``"devotional_manual"``, ``"psalter"``).
        Only matches ``liturgical_unit`` documents.
    genre:
        Equality filter on ``liturgical_unit_meta.genre``
        (e.g. ``"collect"``, ``"canticle"``, ``"prayer"``, ``"psalm_verse"``).
        Only matches ``liturgical_unit`` documents.
    tradition:
        Equality filter on ``liturgical_unit_meta.tradition``
        (``"anglican"``, ``"byzantine"``, ``"roman"``, ``"shared"``).
        Only matches ``liturgical_unit`` documents.
    feast_name:
        Case-insensitive substring match against ``feast.primary_name``.
        Triggers a JOIN through ``liturgical_unit_meta.calendar_anchor_id``.
        Only matches ``liturgical_unit`` documents that have a feast anchor.
    limit:
        Maximum number of results to return (default 10, max 50).

    Returns
    -------
    List of :class:`SearchResult` ordered by ascending distance (best match first).
    """
    import datetime

    limit = max(1, min(limit, _MAX_LIMIT))

    # Determine if any liturgical-specific filters are active
    has_liturgical_filters = any([category, genre, tradition, feast_name])

    # Determine if the date range should be treated as a liturgical calendar
    # range (Option A overload: applies when content_type=="liturgical_unit").
    use_calendar_date_range = (
        content_type == "liturgical_unit" and (date_from or date_to)
    )

    has_filters = any(
        [content_type, source, date_from, date_to, has_liturgical_filters]
    )
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

    # Step 2: Resolve liturgical calendar range to a list of feast IDs (if needed).
    # We do this before building the SQL so that we can embed the IDs as params.
    calendar_feast_ids: list[int] | None = None
    if use_calendar_date_range:
        year = calendar_year if calendar_year is not None else datetime.date.today().year
        cal_from = _date.fromisoformat(date_from) if date_from else _date(year, 1, 1)
        cal_to = _date.fromisoformat(date_to) if date_to else _date(year, 12, 31)
        calendar_feast_ids = _feast_ids_in_calendar_range(
            conn, cal_from, cal_to, year, tradition
        )
        # If no feasts fall in range, no results are possible for this filter.
        if not calendar_feast_ids:
            return []

    # Step 3: Join chunks -> documents with optional filters.
    #
    # Liturgical meta JOINs are conditional — we only JOIN liturgical_unit_meta
    # when at least one liturgical filter is active, to avoid excluding
    # non-liturgical documents from results that have no liturgical filters.
    placeholders = ",".join("?" * len(chunk_ids))
    sql = (
        "SELECT c.id AS chunk_id, c.text AS chunk_text, "
        "       d.id AS document_id, d.content_type, d.source_id, "
        "       d.source_uri, d.title, d.created_at "
        "FROM chunks c "
        "JOIN documents d ON c.document_id = d.id"
    )

    # Conditionally add JOINs for liturgical meta filters.
    need_meta_join = has_liturgical_filters or use_calendar_date_range
    need_feast_join = feast_name is not None or (
        use_calendar_date_range and calendar_feast_ids is not None
    )

    if need_meta_join:
        sql += " JOIN liturgical_unit_meta lum ON lum.document_id = d.id"
    if need_feast_join:
        sql += " JOIN feast f ON f.id = lum.calendar_anchor_id"

    sql += f" WHERE c.id IN ({placeholders})"
    params: list[object] = list(chunk_ids)

    # Standard document-level filters
    if content_type:
        sql += " AND d.content_type = ?"
        params.append(content_type)
    if source:
        sql += " AND d.source_uri LIKE ?"
        params.append(f"%{source}%")

    # Date filters: Option A overload.
    # When content_type=="liturgical_unit" and date_from/date_to are set, we
    # already resolved calendar_feast_ids above; filter on those instead of
    # created_at.  For all other content types, use the original created_at
    # semantics.
    if use_calendar_date_range and calendar_feast_ids is not None:
        # calendar_feast_ids already guaranteed non-empty (early return above)
        feast_placeholders = ",".join("?" * len(calendar_feast_ids))
        sql += f" AND lum.calendar_anchor_id IN ({feast_placeholders})"
        params.extend(calendar_feast_ids)
    else:
        if date_from:
            sql += " AND d.created_at >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND d.created_at <= ?"
            params.append(date_to + "T23:59:59Z" if len(date_to) == 10 else date_to)

    # Liturgical meta equality filters
    if category:
        sql += " AND lum.category = ?"
        params.append(category)
    if genre:
        sql += " AND lum.genre = ?"
        params.append(genre)
    if tradition:
        sql += " AND lum.tradition = ?"
        params.append(tradition)
    if feast_name:
        sql += " AND f.primary_name LIKE ?"
        params.append(f"%{feast_name}%")

    joined_rows = conn.execute(sql, params).fetchall()

    # Step 4: Build results, attach distance scores, sort, and limit
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
