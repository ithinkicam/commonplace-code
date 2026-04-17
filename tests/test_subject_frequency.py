"""Tests for the `subject_frequency` MCP tool and its pure report helper."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import commonplace_db
from commonplace_server import subject_frequency

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_conn(tmp_path: Path) -> sqlite3.Connection:
    db_file = tmp_path / "subfreq.db"
    conn = commonplace_db.connect(str(db_file))
    commonplace_db.migrate(conn)
    return conn


def _insert_feast(
    conn: sqlite3.Connection,
    *,
    primary_name: str,
    theological_subjects: list[str] | None = None,
    tradition: str = "anglican",
    calendar_type: str = "fixed",
    date_rule: str = "01-01",
    precedence: str = "lesser_commemoration",
) -> int:
    subjects_json: str | None = (
        json.dumps(theological_subjects) if theological_subjects is not None else None
    )
    cur = conn.execute(
        """
        INSERT INTO feast (primary_name, tradition, calendar_type, date_rule, precedence,
                           theological_subjects)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (primary_name, tradition, calendar_type, date_rule, precedence, subjects_json),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _insert_feast_raw_subjects(
    conn: sqlite3.Connection,
    *,
    primary_name: str,
    theological_subjects_raw: str | None,
) -> int:
    """Insert a feast with a raw (possibly malformed) theological_subjects value."""
    cur = conn.execute(
        """
        INSERT INTO feast (primary_name, tradition, calendar_type, date_rule, precedence,
                           theological_subjects)
        VALUES (?, 'anglican', 'fixed', '01-01', 'lesser_commemoration', ?)
        """,
        (primary_name, theological_subjects_raw),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def test_subject_frequency_is_registered() -> None:
    from commonplace_server.server import mcp, subject_frequency

    assert callable(subject_frequency)
    tool_names = set(mcp._tool_manager._tools.keys())
    assert "subject_frequency" in tool_names, tool_names


# ---------------------------------------------------------------------------
# Empty feast table
# ---------------------------------------------------------------------------


def test_report_empty_db(tmp_path: Path) -> None:
    conn = _fresh_conn(tmp_path)
    try:
        result = subject_frequency.report(conn)
    finally:
        conn.close()

    assert result == {"controlled": [], "other": []}


# ---------------------------------------------------------------------------
# Mixed controlled + _other: tags
# ---------------------------------------------------------------------------


def test_report_mixed_subjects(tmp_path: Path) -> None:
    conn = _fresh_conn(tmp_path)
    try:
        _insert_feast(
            conn,
            primary_name="Dormition",
            theological_subjects=["theotokos", "death", "_other:kenosis"],
        )
        _insert_feast(
            conn,
            primary_name="Nativity of Mary",
            theological_subjects=["theotokos", "_other:kenosis"],
        )
        _insert_feast(
            conn,
            primary_name="Annunciation",
            theological_subjects=["theotokos"],
        )

        result = subject_frequency.report(conn)
    finally:
        conn.close()

    # theotokos appears 3 times, death once — both in controlled
    controlled = {item["subject"]: item for item in result["controlled"]}
    assert "theotokos" in controlled
    assert controlled["theotokos"]["count"] == 3
    assert sorted(controlled["theotokos"]["feasts"]) == [
        "Annunciation",
        "Dormition",
        "Nativity of Mary",
    ]
    assert "death" in controlled
    assert controlled["death"]["count"] == 1
    assert controlled["death"]["feasts"] == ["Dormition"]

    # _other:kenosis appears on 2 feasts
    other = {item["subject"]: item for item in result["other"]}
    assert "_other:kenosis" in other
    assert other["_other:kenosis"]["count"] == 2
    assert sorted(other["_other:kenosis"]["feasts"]) == ["Dormition", "Nativity of Mary"]


# ---------------------------------------------------------------------------
# min_count filter
# ---------------------------------------------------------------------------


def test_report_min_count_filters_singles(tmp_path: Path) -> None:
    conn = _fresh_conn(tmp_path)
    try:
        _insert_feast(
            conn,
            primary_name="Feast A",
            theological_subjects=["common", "rare", "_other:once"],
        )
        _insert_feast(
            conn,
            primary_name="Feast B",
            theological_subjects=["common"],
        )

        result = subject_frequency.report(conn, min_count=2)
    finally:
        conn.close()

    controlled_subjects = {item["subject"] for item in result["controlled"]}
    assert "common" in controlled_subjects
    assert "rare" not in controlled_subjects

    other_subjects = {item["subject"] for item in result["other"]}
    assert "_other:once" not in other_subjects


# ---------------------------------------------------------------------------
# include_controlled=False
# ---------------------------------------------------------------------------


def test_report_exclude_controlled(tmp_path: Path) -> None:
    conn = _fresh_conn(tmp_path)
    try:
        _insert_feast(
            conn,
            primary_name="Feast X",
            theological_subjects=["theotokos", "_other:kenosis"],
        )

        result = subject_frequency.report(conn, include_controlled=False)
    finally:
        conn.close()

    assert result["controlled"] == []
    other_subjects = {item["subject"] for item in result["other"]}
    assert "_other:kenosis" in other_subjects


# ---------------------------------------------------------------------------
# include_other=False
# ---------------------------------------------------------------------------


def test_report_exclude_other(tmp_path: Path) -> None:
    conn = _fresh_conn(tmp_path)
    try:
        _insert_feast(
            conn,
            primary_name="Feast Y",
            theological_subjects=["theotokos", "_other:kenosis"],
        )

        result = subject_frequency.report(conn, include_other=False)
    finally:
        conn.close()

    assert result["other"] == []
    controlled_subjects = {item["subject"] for item in result["controlled"]}
    assert "theotokos" in controlled_subjects


# ---------------------------------------------------------------------------
# NULL or empty theological_subjects — should not crash or contribute
# ---------------------------------------------------------------------------


def test_report_null_subjects_skipped(tmp_path: Path) -> None:
    conn = _fresh_conn(tmp_path)
    try:
        _insert_feast_raw_subjects(
            conn,
            primary_name="Null Feast",
            theological_subjects_raw=None,
        )
        _insert_feast_raw_subjects(
            conn,
            primary_name="Empty Feast",
            theological_subjects_raw="",
        )

        result = subject_frequency.report(conn)
    finally:
        conn.close()

    assert result == {"controlled": [], "other": []}


# ---------------------------------------------------------------------------
# Malformed JSON — skip cleanly with warning
# ---------------------------------------------------------------------------


def test_report_malformed_json_skipped(tmp_path: Path) -> None:
    conn = _fresh_conn(tmp_path)
    try:
        _insert_feast_raw_subjects(
            conn,
            primary_name="Bad JSON Feast",
            theological_subjects_raw="not-valid-json[[[",
        )
        _insert_feast(
            conn,
            primary_name="Good Feast",
            theological_subjects=["theotokos"],
        )

        result = subject_frequency.report(conn)
    finally:
        conn.close()

    # The good feast is counted; the bad JSON feast is skipped cleanly
    controlled_subjects = {item["subject"] for item in result["controlled"]}
    assert "theotokos" in controlled_subjects
    assert result["other"] == []


# ---------------------------------------------------------------------------
# Sort order: descending count, ties broken by subject ascending
# ---------------------------------------------------------------------------


def test_report_sort_order(tmp_path: Path) -> None:
    conn = _fresh_conn(tmp_path)
    try:
        # "alpha" appears 2x, "zebra" appears 2x, "middle" appears 3x
        _insert_feast(
            conn,
            primary_name="F1",
            theological_subjects=["alpha", "zebra", "middle"],
        )
        _insert_feast(
            conn,
            primary_name="F2",
            theological_subjects=["alpha", "zebra", "middle"],
        )
        _insert_feast(
            conn,
            primary_name="F3",
            theological_subjects=["middle"],
        )

        result = subject_frequency.report(conn)
    finally:
        conn.close()

    controlled = result["controlled"]
    subjects_in_order = [item["subject"] for item in controlled]
    # middle=3 first, then alpha=2 before zebra=2 (alphabetical tie-break)
    assert subjects_in_order == ["middle", "alpha", "zebra"]


# ---------------------------------------------------------------------------
# Feast names within each item are deduped and sorted alphabetically
# ---------------------------------------------------------------------------


def test_report_feast_names_deduped_and_sorted(tmp_path: Path) -> None:
    conn = _fresh_conn(tmp_path)
    try:
        # Same subject appears multiple times in one feast's list (shouldn't happen
        # in practice but we should handle it gracefully — dedup by feast name)
        _insert_feast(
            conn,
            primary_name="Zebra Feast",
            theological_subjects=["shared"],
        )
        _insert_feast(
            conn,
            primary_name="Alpha Feast",
            theological_subjects=["shared"],
        )
        _insert_feast(
            conn,
            primary_name="Middle Feast",
            theological_subjects=["shared"],
        )

        result = subject_frequency.report(conn)
    finally:
        conn.close()

    controlled = {item["subject"]: item for item in result["controlled"]}
    assert controlled["shared"]["feasts"] == ["Alpha Feast", "Middle Feast", "Zebra Feast"]


# ---------------------------------------------------------------------------
# Tool passthrough via server
# ---------------------------------------------------------------------------


def test_subject_frequency_returns_report(tmp_path: Path) -> None:
    db_file = str(tmp_path / "tool.db")
    os.environ["COMMONPLACE_DB_PATH"] = db_file
    try:
        conn = commonplace_db.connect(db_file)
        commonplace_db.migrate(conn)
        _insert_feast(
            conn,
            primary_name="Annunciation",
            theological_subjects=["theotokos", "_other:kenosis"],
        )
        conn.close()

        from commonplace_server.server import subject_frequency

        result = subject_frequency()
        controlled_subjects = {item["subject"] for item in result["controlled"]}
        other_subjects = {item["subject"] for item in result["other"]}
        assert "theotokos" in controlled_subjects
        assert "_other:kenosis" in other_subjects
    finally:
        del os.environ["COMMONPLACE_DB_PATH"]
