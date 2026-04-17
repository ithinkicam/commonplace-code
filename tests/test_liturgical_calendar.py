"""Tests for commonplace_server.liturgical_calendar (Phase 0.4 stub)."""

from __future__ import annotations

import sqlite3
from datetime import date

from commonplace_server.liturgical_calendar import (
    movable_feasts_for_year,
    resolve,
    resolve_fixed_date,
    resolve_movable_date,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREATE_FEAST = """
CREATE TABLE feast (
    id                             INTEGER PRIMARY KEY,
    primary_name                   TEXT NOT NULL,
    alternate_names                TEXT,
    tradition                      TEXT NOT NULL,
    calendar_type                  TEXT NOT NULL,
    date_rule                      TEXT NOT NULL,
    precedence                     TEXT NOT NULL,
    theological_subjects           TEXT,
    cross_tradition_equivalent_id  INTEGER,
    created_at                     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                     TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _make_conn(seed_rows: list[dict] | None = None) -> sqlite3.Connection:  # type: ignore[type-arg]
    """Return an in-memory SQLite connection with the feast table.

    Optionally inserts *seed_rows* (dicts with feast column values).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_FEAST)
    if seed_rows:
        for row in seed_rows:
            conn.execute(
                "INSERT INTO feast"
                " (primary_name, tradition, calendar_type, date_rule, precedence)"
                " VALUES (:primary_name, :tradition, :calendar_type, :date_rule, :precedence)",
                row,
            )
    conn.commit()
    return conn


def _empty_conn() -> sqlite3.Connection:
    """Return an in-memory connection with an empty feast table."""
    return _make_conn()


def _no_table_conn() -> sqlite3.Connection:
    """Return an in-memory connection with NO feast table at all."""
    return sqlite3.connect(":memory:")


# ---------------------------------------------------------------------------
# movable_feasts_for_year
# ---------------------------------------------------------------------------


class TestMovableFeastsForYear:
    """Anglican (Western) 2025 — both calendars coincide."""

    def test_easter_2025_anglican(self) -> None:
        feasts = movable_feasts_for_year(2025, "anglican")
        assert feasts["easter"] == date(2025, 4, 20)

    def test_ash_wednesday_2025_anglican(self) -> None:
        feasts = movable_feasts_for_year(2025, "anglican")
        assert feasts["ash_wednesday"] == date(2025, 3, 5)

    def test_septuagesima_2025_anglican(self) -> None:
        feasts = movable_feasts_for_year(2025, "anglican")
        assert feasts["septuagesima"] == date(2025, 2, 16)

    def test_palm_sunday_2025_anglican(self) -> None:
        feasts = movable_feasts_for_year(2025, "anglican")
        assert feasts["palm_sunday"] == date(2025, 4, 13)

    def test_ascension_2025_anglican(self) -> None:
        feasts = movable_feasts_for_year(2025, "anglican")
        assert feasts["ascension"] == date(2025, 5, 29)

    def test_pentecost_2025_anglican(self) -> None:
        feasts = movable_feasts_for_year(2025, "anglican")
        assert feasts["pentecost"] == date(2025, 6, 8)

    def test_trinity_sunday_2025_anglican(self) -> None:
        feasts = movable_feasts_for_year(2025, "anglican")
        assert feasts["trinity_sunday"] == date(2025, 6, 15)

    def test_easter_2025_orthodox_coincides(self) -> None:
        """In 2025 both Eastern and Western Easter fall on 2025-04-20."""
        feasts = movable_feasts_for_year(2025, "byzantine")
        assert feasts["easter"] == date(2025, 4, 20)

    def test_easter_2026_anglican(self) -> None:
        feasts = movable_feasts_for_year(2026, "anglican")
        assert feasts["easter"] == date(2026, 4, 5)

    def test_easter_2026_orthodox_differs(self) -> None:
        """In 2026 Orthodox Easter (2026-04-12) differs from Western (2026-04-05)."""
        feasts = movable_feasts_for_year(2026, "byzantine")
        assert feasts["easter"] == date(2026, 4, 12)

    def test_default_tradition_is_anglican(self) -> None:
        assert movable_feasts_for_year(2026) == movable_feasts_for_year(2026, "anglican")

    def test_returns_all_seven_slugs(self) -> None:
        expected_slugs = {
            "septuagesima",
            "ash_wednesday",
            "palm_sunday",
            "easter",
            "ascension",
            "pentecost",
            "trinity_sunday",
        }
        assert set(movable_feasts_for_year(2025).keys()) == expected_slugs


# ---------------------------------------------------------------------------
# resolve_fixed_date
# ---------------------------------------------------------------------------


class TestResolveFixedDate:
    def test_empty_table_returns_empty_list(self) -> None:
        conn = _empty_conn()
        result = resolve_fixed_date(conn, date(2025, 4, 20))
        assert result == []

    def test_no_feast_table_returns_empty_list(self) -> None:
        conn = _no_table_conn()
        result = resolve_fixed_date(conn, date(2025, 4, 20))
        assert result == []

    def test_returns_matching_fixed_feast(self) -> None:
        conn = _make_conn(
            [
                {
                    "primary_name": "The Annunciation",
                    "tradition": "anglican",
                    "calendar_type": "fixed",
                    "date_rule": "03-25",
                    "precedence": "principal_feast",
                },
            ]
        )
        result = resolve_fixed_date(conn, date(2025, 3, 25))
        assert len(result) == 1
        assert result[0]["primary_name"] == "The Annunciation"

    def test_non_matching_date_returns_empty(self) -> None:
        conn = _make_conn(
            [
                {
                    "primary_name": "The Annunciation",
                    "tradition": "anglican",
                    "calendar_type": "fixed",
                    "date_rule": "03-25",
                    "precedence": "principal_feast",
                },
            ]
        )
        result = resolve_fixed_date(conn, date(2025, 3, 26))
        assert result == []

    def test_tradition_filter_includes_matching(self) -> None:
        conn = _make_conn(
            [
                {
                    "primary_name": "Anglican Feast",
                    "tradition": "anglican",
                    "calendar_type": "fixed",
                    "date_rule": "06-24",
                    "precedence": "holy_day",
                },
                {
                    "primary_name": "Byzantine Feast",
                    "tradition": "byzantine",
                    "calendar_type": "fixed",
                    "date_rule": "06-24",
                    "precedence": "holy_day",
                },
            ]
        )
        result = resolve_fixed_date(conn, date(2025, 6, 24), tradition="byzantine")
        assert len(result) == 1
        assert result[0]["primary_name"] == "Byzantine Feast"

    def test_tradition_filter_excludes_non_matching(self) -> None:
        conn = _make_conn(
            [
                {
                    "primary_name": "Anglican Feast",
                    "tradition": "anglican",
                    "calendar_type": "fixed",
                    "date_rule": "06-24",
                    "precedence": "holy_day",
                },
            ]
        )
        result = resolve_fixed_date(conn, date(2025, 6, 24), tradition="byzantine")
        assert result == []

    def test_no_tradition_filter_returns_all_matching(self) -> None:
        conn = _make_conn(
            [
                {
                    "primary_name": "Anglican Feast",
                    "tradition": "anglican",
                    "calendar_type": "fixed",
                    "date_rule": "06-24",
                    "precedence": "holy_day",
                },
                {
                    "primary_name": "Byzantine Feast",
                    "tradition": "byzantine",
                    "calendar_type": "fixed",
                    "date_rule": "06-24",
                    "precedence": "holy_day",
                },
                {
                    "primary_name": "Different Date",
                    "tradition": "anglican",
                    "calendar_type": "fixed",
                    "date_rule": "06-25",
                    "precedence": "holy_day",
                },
            ]
        )
        result = resolve_fixed_date(conn, date(2025, 6, 24))
        assert len(result) == 2

    def test_result_is_plain_dict(self) -> None:
        conn = _make_conn(
            [
                {
                    "primary_name": "All Saints",
                    "tradition": "anglican",
                    "calendar_type": "fixed",
                    "date_rule": "11-01",
                    "precedence": "principal_feast",
                },
            ]
        )
        result = resolve_fixed_date(conn, date(2025, 11, 1))
        assert isinstance(result[0], dict)


# ---------------------------------------------------------------------------
# resolve_movable_date
# ---------------------------------------------------------------------------


class TestResolveMovableDate:
    def test_empty_table_returns_empty_list(self) -> None:
        conn = _empty_conn()
        result = resolve_movable_date(conn, date(2025, 4, 20))
        assert result == []

    def test_no_feast_table_returns_empty_list(self) -> None:
        conn = _no_table_conn()
        result = resolve_movable_date(conn, date(2025, 4, 20))
        assert result == []

    def test_easter_plus_zero(self) -> None:
        """easter+0 row matches 2025-04-20 (Western Easter 2025)."""
        conn = _make_conn(
            [
                {
                    "primary_name": "Easter Day",
                    "tradition": "anglican",
                    "calendar_type": "movable",
                    "date_rule": "easter+0",
                    "precedence": "principal_feast",
                },
            ]
        )
        result = resolve_movable_date(conn, date(2025, 4, 20))
        assert len(result) == 1
        assert result[0]["primary_name"] == "Easter Day"

    def test_easter_minus_46(self) -> None:
        """easter-46 row matches 2025-03-05 (Ash Wednesday 2025)."""
        conn = _make_conn(
            [
                {
                    "primary_name": "Ash Wednesday",
                    "tradition": "anglican",
                    "calendar_type": "movable",
                    "date_rule": "easter-46",
                    "precedence": "holy_day",
                },
            ]
        )
        result = resolve_movable_date(conn, date(2025, 3, 5))
        assert len(result) == 1
        assert result[0]["primary_name"] == "Ash Wednesday"

    def test_non_matching_movable_date_returns_empty(self) -> None:
        conn = _make_conn(
            [
                {
                    "primary_name": "Easter Day",
                    "tradition": "anglican",
                    "calendar_type": "movable",
                    "date_rule": "easter+0",
                    "precedence": "principal_feast",
                },
            ]
        )
        result = resolve_movable_date(conn, date(2025, 4, 21))
        assert result == []

    def test_tradition_filter_movable(self) -> None:
        conn = _make_conn(
            [
                {
                    "primary_name": "Anglican Easter",
                    "tradition": "anglican",
                    "calendar_type": "movable",
                    "date_rule": "easter+0",
                    "precedence": "principal_feast",
                },
                {
                    "primary_name": "Byzantine Pascha",
                    "tradition": "byzantine",
                    "calendar_type": "movable",
                    "date_rule": "easter+0",
                    "precedence": "principal_feast",
                },
            ]
        )
        # 2025: both Easters coincide on 2025-04-20
        result = resolve_movable_date(conn, date(2025, 4, 20), tradition="anglican")
        assert len(result) == 1
        assert result[0]["primary_name"] == "Anglican Easter"


# ---------------------------------------------------------------------------
# resolve (combined)
# ---------------------------------------------------------------------------


class TestResolve:
    def test_concatenates_fixed_and_movable(self) -> None:
        """resolve() should return both a fixed and a movable feast."""
        conn = _make_conn(
            [
                # fixed feast on 2025-04-20
                {
                    "primary_name": "Fixed on April 20",
                    "tradition": "anglican",
                    "calendar_type": "fixed",
                    "date_rule": "04-20",
                    "precedence": "lesser_commemoration",
                },
                # Easter 2025 = 2025-04-20
                {
                    "primary_name": "Easter Day",
                    "tradition": "anglican",
                    "calendar_type": "movable",
                    "date_rule": "easter+0",
                    "precedence": "principal_feast",
                },
                # unrelated row
                {
                    "primary_name": "All Saints",
                    "tradition": "anglican",
                    "calendar_type": "fixed",
                    "date_rule": "11-01",
                    "precedence": "principal_feast",
                },
            ]
        )
        result = resolve(conn, date(2025, 4, 20))
        names = {r["primary_name"] for r in result}
        assert "Fixed on April 20" in names
        assert "Easter Day" in names
        assert "All Saints" not in names

    def test_tradition_filter_narrows_combined(self) -> None:
        conn = _make_conn(
            [
                {
                    "primary_name": "Anglican Fixed",
                    "tradition": "anglican",
                    "calendar_type": "fixed",
                    "date_rule": "04-20",
                    "precedence": "holy_day",
                },
                {
                    "primary_name": "Byzantine Fixed",
                    "tradition": "byzantine",
                    "calendar_type": "fixed",
                    "date_rule": "04-20",
                    "precedence": "holy_day",
                },
                {
                    "primary_name": "Anglican Movable",
                    "tradition": "anglican",
                    "calendar_type": "movable",
                    "date_rule": "easter+0",
                    "precedence": "principal_feast",
                },
            ]
        )
        result = resolve(conn, date(2025, 4, 20), tradition="anglican")
        names = {r["primary_name"] for r in result}
        assert "Anglican Fixed" in names
        assert "Anglican Movable" in names
        assert "Byzantine Fixed" not in names

    def test_empty_table_returns_empty(self) -> None:
        conn = _empty_conn()
        assert resolve(conn, date(2025, 4, 20)) == []
