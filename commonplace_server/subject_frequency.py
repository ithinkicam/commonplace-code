"""Theological subject frequency reporting for the feast table.

report(conn, ...) returns a snapshot of how often each theological subject
appears across feasts, split into controlled vocabulary vs _other:* tags.

Decoupled from FastMCP so it can be unit-tested directly.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


def report(
    conn: sqlite3.Connection,
    *,
    include_controlled: bool = True,
    include_other: bool = True,
    min_count: int = 1,
) -> dict[str, Any]:
    """Return a theological subject frequency report from the feast table.

    Parameters
    ----------
    conn:
        Open SQLite connection (must already be migrated).
    include_controlled:
        When False, the ``controlled`` list in the output is always empty.
    include_other:
        When False, the ``other`` list in the output is always empty.
    min_count:
        Subjects with fewer than this many feasts are excluded from output.

    Returns
    -------
    ``{"controlled": [...], "other": [...]}`` where each item is
    ``{"subject": str, "count": int, "feasts": [str, ...]}``.
    Both lists are sorted by count descending, ties broken by subject ascending.
    Feast names within each item are sorted alphabetically for stability.
    Feasts with NULL, empty, or malformed JSON in ``theological_subjects`` are
    skipped (with a warning log for malformed JSON); they never contribute to counts.
    """
    rows = conn.execute(
        "SELECT id, primary_name, theological_subjects FROM feast"
    ).fetchall()

    # Accumulate: subject -> {count, feasts (set for dedup)}
    controlled_acc: dict[str, dict[str, Any]] = {}
    other_acc: dict[str, dict[str, Any]] = {}

    for row in rows:
        raw = row["theological_subjects"] if isinstance(row, sqlite3.Row) else row[2]
        primary_name = row["primary_name"] if isinstance(row, sqlite3.Row) else row[1]

        if not raw:
            continue

        try:
            subjects = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.warning(
                "feast id=%s has malformed JSON in theological_subjects; skipping",
                row["id"] if isinstance(row, sqlite3.Row) else row[0],
            )
            continue

        if not isinstance(subjects, list):
            logger.warning(
                "feast id=%s theological_subjects is not a JSON array; skipping",
                row["id"] if isinstance(row, sqlite3.Row) else row[0],
            )
            continue

        for subject in subjects:
            if not isinstance(subject, str) or not subject:
                continue

            acc = other_acc if subject.startswith("_other:") else controlled_acc

            if subject not in acc:
                acc[subject] = {"count": 0, "feasts": set()}
            acc[subject]["count"] += 1
            acc[subject]["feasts"].add(primary_name)

    def _build_list(acc: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for subject, data in acc.items():
            if data["count"] < min_count:
                continue
            result.append(
                {
                    "subject": subject,
                    "count": data["count"],
                    "feasts": sorted(data["feasts"]),
                }
            )
        # Sort by count descending, ties broken by subject ascending
        result.sort(key=lambda x: (-x["count"], x["subject"]))
        return result

    controlled: list[dict[str, Any]] = _build_list(controlled_acc) if include_controlled else []
    other: list[dict[str, Any]] = _build_list(other_acc) if include_other else []

    return {"controlled": controlled, "other": other}
