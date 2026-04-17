"""Liturgical calendar resolver — stub module (Phase 0.4).

Provides movable-feast date math and fixed-date lookup against the ``feast``
table.  Precedence ordering (LFF 2022 ladder) is Phase 4.5 and intentionally
absent here.

Public API
----------
movable_feasts_for_year(year, tradition) -> dict[str, date]
resolve_fixed_date(conn, date, tradition) -> list[dict]
resolve_movable_date(conn, date, tradition) -> list[dict]
resolve(conn, date, tradition) -> list[dict]
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from typing import Literal

from dateutil.easter import EASTER_ORTHODOX, EASTER_WESTERN, easter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MOVABLE_OFFSETS: dict[str, int] = {
    "septuagesima": -63,
    "ash_wednesday": -46,
    "palm_sunday": -7,
    "easter": 0,
    "ascension": 39,
    "pentecost": 49,
    "trinity_sunday": 56,
}

# Matches date_rule values like "easter+0", "easter-46", "easter+39"
_MOVABLE_RULE_RE = re.compile(r"^easter([+-]\d+)$")

# ---------------------------------------------------------------------------
# Movable-feast math
# ---------------------------------------------------------------------------


def movable_feasts_for_year(
    year: int,
    tradition: Literal["anglican", "byzantine"] = "anglican",
) -> dict[str, date]:
    """Return a mapping of feast slug -> computed date for *year*.

    Slugs: ``septuagesima``, ``ash_wednesday``, ``palm_sunday``, ``easter``,
    ``ascension``, ``pentecost``, ``trinity_sunday``.

    Uses Western Easter for ``"anglican"`` and Orthodox Easter for
    ``"byzantine"``.
    """
    method = EASTER_WESTERN if tradition == "anglican" else EASTER_ORTHODOX
    easter_date: date = easter(year, method=method)  # type: ignore[arg-type]
    return {
        slug: easter_date + timedelta(days=offset)
        for slug, offset in _MOVABLE_OFFSETS.items()
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_row_factory(conn: sqlite3.Connection) -> None:
    """Set ``conn.row_factory`` to ``sqlite3.Row`` if not already set."""
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    """Convert a list of ``sqlite3.Row`` objects to plain dicts."""
    return [dict(row) for row in rows]


def _feast_table_exists(conn: sqlite3.Connection) -> bool:
    """Return True if the ``feast`` table exists in the database."""
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feast'"
    )
    return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Public resolvers
# ---------------------------------------------------------------------------


def resolve_fixed_date(
    conn: sqlite3.Connection,
    query_date: date,
    tradition: str | None = None,
) -> list[dict]:
    """Return feast rows whose ``date_rule`` matches *query_date*'s MM-DD.

    Queries ``feast`` where ``calendar_type = 'fixed'`` and
    ``date_rule = '<MM-DD>'`` (zero-padded).  Optional *tradition* filter.
    Returns ``[]`` if the table is empty or no rows match.
    """
    _ensure_row_factory(conn)
    if not _feast_table_exists(conn):
        return []

    mm_dd = query_date.strftime("%m-%d")

    if tradition is not None:
        rows = conn.execute(
            "SELECT * FROM feast"
            " WHERE calendar_type = 'fixed'"
            "   AND date_rule = ?"
            "   AND tradition = ?",
            (mm_dd, tradition),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM feast"
            " WHERE calendar_type = 'fixed'"
            "   AND date_rule = ?",
            (mm_dd,),
        ).fetchall()

    return _rows_to_dicts(rows)


def resolve_movable_date(
    conn: sqlite3.Connection,
    query_date: date,
    tradition: str | None = None,
) -> list[dict]:
    """Return feast rows whose ``date_rule`` resolves to *query_date*.

    Queries ``feast`` where ``calendar_type = 'movable'`` and tests each row's
    ``date_rule`` (of the form ``easter±N``) against the Easter date for
    *query_date*'s year.  Optional *tradition* filter.
    Returns ``[]`` if the table is empty or no rows match.
    """
    _ensure_row_factory(conn)
    if not _feast_table_exists(conn):
        return []

    if tradition is not None:
        candidates = conn.execute(
            "SELECT * FROM feast"
            " WHERE calendar_type = 'movable'"
            "   AND tradition = ?",
            (tradition,),
        ).fetchall()
    else:
        candidates = conn.execute(
            "SELECT * FROM feast WHERE calendar_type = 'movable'"
        ).fetchall()

    if not candidates:
        return []

    # Compute Easter for the query year (use tradition to pick method when
    # filtering; default to Western when no tradition given).
    method = EASTER_ORTHODOX if tradition == "byzantine" else EASTER_WESTERN
    easter_date: date = easter(query_date.year, method=method)  # type: ignore[arg-type]

    matched: list[dict] = []
    for row in candidates:
        rule: str = dict(row)["date_rule"]
        m = _MOVABLE_RULE_RE.match(rule)
        if m is None:
            continue  # malformed rule — skip
        offset = int(m.group(1))
        if easter_date + timedelta(days=offset) == query_date:
            matched.append(dict(row))

    return matched


def resolve(
    conn: sqlite3.Connection,
    query_date: date,
    tradition: str | None = None,
) -> list[dict]:
    """Return all observances for *query_date* — fixed and movable combined.

    Concatenates ``resolve_fixed_date`` and ``resolve_movable_date`` results
    with no precedence ordering.  Precedence is implemented in task 4.5.
    """
    return resolve_fixed_date(conn, query_date, tradition) + resolve_movable_date(
        conn, query_date, tradition
    )
