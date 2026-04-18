"""Tests for commonplace_server.liturgical_calendar (Phase 0.4 stub + Phase 4.5 precedence)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

import pytest

from commonplace_server.liturgical_calendar import (
    SUNDAY_OVERRIDE_FIXED_DATES,
    apply_precedence,
    movable_feasts_for_year,
    precedence_rank,
    resolve,
    resolve_fixed_date,
    resolve_movable_date,
    resolve_with_precedence,
    season_info_for_year,
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


# ---------------------------------------------------------------------------
# Helpers for Phase 4.5 tests
# ---------------------------------------------------------------------------

def _make_feast(
    primary_name: str,
    precedence: str,
    calendar_type: str = "fixed",
    date_rule: str = "01-01",
    tradition: str = "anglican",
) -> dict:
    """Return a minimal feast dict suitable for precedence tests."""
    return {
        "primary_name": primary_name,
        "precedence": precedence,
        "calendar_type": calendar_type,
        "date_rule": date_rule,
        "tradition": tradition,
    }


def _si_2026() -> object:
    """Return SeasonInfo for 2026 (Easter = April 5)."""
    return season_info_for_year(2026, "anglican")


def _si_2025() -> object:
    """Return SeasonInfo for 2025 (Easter = April 20)."""
    return season_info_for_year(2025, "anglican")


# ---------------------------------------------------------------------------
# Phase 4.5 fixture DB — loads feasts.yaml into a tmp SQLite DB
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def feast_db():
    """Session-scoped SQLite DB loaded with feasts.yaml via feast_import.

    Yields the open connection; cleans up the tmp file at teardown.
    """
    import commonplace_db
    from commonplace_db.feast_schema import validate_feasts
    from scripts.feast_import import _run_import

    repo_root = Path(__file__).parent.parent
    feasts_path = repo_root / "commonplace_db" / "seed" / "feasts.yaml"
    subjects_path = repo_root / "commonplace_db" / "seed" / "theological_subjects.yaml"

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_db = f.name

    try:
        conn = commonplace_db.connect(tmp_db)
        commonplace_db.migrate(conn)
        entries = validate_feasts(feasts_path, subjects_path)
        _run_import(conn, entries, dry_run=False, ignore_missing_cross_refs=True)
        yield conn
        conn.close()
    finally:
        os.unlink(tmp_db)


# ---------------------------------------------------------------------------
# Pure precedence tests (no DB, 10 tests)
# ---------------------------------------------------------------------------


class TestPrecedenceRank:
    """Unit tests for precedence_rank() — 10 scenarios covering all levels."""

    def test_principal_feast_rank_is_lowest_int(self) -> None:
        """principal_feast gets rank 1 (highest priority)."""
        feast = _make_feast("Easter Day", "principal_feast", "movable", "easter+0")
        si = _si_2026()
        assert precedence_rank(feast, date(2026, 4, 5), si) == 1

    def test_holy_day_rank(self) -> None:
        """Normal holy_day (not a Sunday-override) gets rank 4."""
        feast = _make_feast("Ash Wednesday", "holy_day", "movable", "easter-46")
        si = _si_2026()
        assert precedence_rank(feast, date(2026, 2, 18), si) == 4

    def test_lesser_commemoration_rank(self) -> None:
        feast = _make_feast("Some Saint", "lesser_commemoration")
        si = _si_2026()
        assert precedence_rank(feast, date(2026, 3, 10), si) == 5

    def test_ferial_rank(self) -> None:
        feast = _make_feast("Some Ferial", "ferial")
        si = _si_2026()
        assert precedence_rank(feast, date(2026, 3, 10), si) == 6

    def test_sunday_override_holy_name_on_sunday(self) -> None:
        """Holy Name (01-01) on a Sunday gets rank 2 (above plain Sundays)."""
        feast = _make_feast("The Holy Name", "holy_day", "fixed", "01-01")
        si = season_info_for_year(2023, "anglican")  # Jan 1, 2023 is a Sunday
        assert precedence_rank(feast, date(2023, 1, 1), si) == 2

    def test_holy_name_on_weekday_is_plain_holy_day(self) -> None:
        """Holy Name (01-01) on a Thursday does NOT get Sunday-override rank."""
        feast = _make_feast("The Holy Name", "holy_day", "fixed", "01-01")
        si = _si_2026()  # Jan 1, 2026 is Thursday
        assert precedence_rank(feast, date(2026, 1, 1), si) == 4

    def test_principal_beats_holy_day(self) -> None:
        """apply_precedence picks principal_feast over holy_day."""
        easter_feast = _make_feast("Easter Day", "principal_feast", "movable", "easter+0")
        some_fixed = _make_feast("Some Fixed", "holy_day", "fixed", "04-05")
        si = _si_2026()
        winner = apply_precedence([some_fixed, easter_feast], date(2026, 4, 5), si)
        assert winner is not None
        assert winner["primary_name"] == "Easter Day"

    def test_principal_beats_lesser_commemoration(self) -> None:
        principal = _make_feast("Ascension Day", "principal_feast")
        lesser = _make_feast("Some Monk", "lesser_commemoration")
        si = _si_2026()
        winner = apply_precedence([lesser, principal], date(2026, 5, 14), si)
        assert winner is not None
        assert winner["primary_name"] == "Ascension Day"

    def test_holy_day_beats_lesser_commemoration(self) -> None:
        holy = _make_feast("St Peter", "holy_day", "fixed", "06-29")
        lesser = _make_feast("Random Monk", "lesser_commemoration", "fixed", "06-29")
        si = _si_2026()
        winner = apply_precedence([lesser, holy], date(2026, 6, 29), si)
        assert winner is not None
        assert winner["primary_name"] == "St Peter"

    def test_tie_between_holy_days_earlier_date_rule_wins(self) -> None:
        """When two holy_days collide, the one with the lexically earlier date_rule wins."""
        feast_a = _make_feast("Feast A", "holy_day", "fixed", "03-19")
        feast_b = _make_feast("Feast B", "holy_day", "fixed", "04-25")
        si = _si_2026()
        winner = apply_precedence([feast_b, feast_a], date(2026, 3, 19), si)
        assert winner is not None
        assert winner["primary_name"] == "Feast A"

    def test_apply_precedence_empty_returns_none(self) -> None:
        si = _si_2026()
        assert apply_precedence([], date(2026, 1, 15), si) is None


# ---------------------------------------------------------------------------
# Transfer rule tests (3+ concrete scenarios)
# ---------------------------------------------------------------------------


class TestTransferRules:
    """Concrete transfer scenarios verified against LFF 2024 rules."""

    def test_holy_day_in_easter_week_transfers(self, feast_db: sqlite3.Connection) -> None:
        """St Mark the Evangelist (04-25) falls in Easter Week 2025 and must transfer.

        Easter 2025 = April 20; Easter Week = April 20–26.
        St Mark (April 25) transfers to week after 2nd Sunday of Easter (April 27).
        Expected: St Mark lands on April 28 (Monday after 2nd Sunday = April 27).
        """
        calendar = resolve_with_precedence(2025, "anglican", feast_db)
        # Original date must NOT hold St Mark
        orig = date(2025, 4, 25)
        if orig in calendar:
            assert calendar[orig]["primary_name"] != "Saint Mark the Evangelist"
        # St Mark must appear on April 28 (first open Monday after 2nd Sunday)
        transferred = date(2025, 4, 28)
        assert transferred in calendar
        assert calendar[transferred]["primary_name"] == "Saint Mark the Evangelist"

    def test_lesser_commemoration_blocked_by_lenten_sunday(
        self, feast_db: sqlite3.Connection
    ) -> None:
        """Vincent de Paul (03-15) falls on a Sunday in Lent 2026 and must transfer.

        March 15, 2026 is the Fourth Sunday in Lent.  Vincent de Paul (lesser
        commemoration) is blocked and should transfer to a weekday after March 15.
        """
        calendar = resolve_with_precedence(2026, "anglican", feast_db)
        # Sunday slot must belong to the Lenten Sunday (not Vincent de Paul)
        lenten_sunday = date(2026, 3, 15)
        assert lenten_sunday in calendar
        assert "Vincent" not in calendar[lenten_sunday].get("primary_name", "")
        # Vincent de Paul (or Vincent de Paul and Louise de Marillac) must appear
        # on some weekday after March 15 (transferred from the blocked Sunday)
        vdp_dates = [
            d
            for d, feast in calendar.items()
            if "Vincent" in feast.get("primary_name", "")
            and d > lenten_sunday
        ]
        assert vdp_dates, (
            "Vincent de Paul must appear somewhere after the blocked Lenten Sunday"
        )

    def test_lesser_commemoration_in_holy_week_transfers(
        self, feast_db: sqlite3.Connection
    ) -> None:
        """Lesser commemorations during Holy Week transfer out of Holy Week.

        Holy Week 2026 = March 29 – April 4.  Mary of Egypt (April 1, lesser
        commemoration) should move to after Easter Week.
        """
        calendar = resolve_with_precedence(2026, "anglican", feast_db)
        holy_week_date = date(2026, 4, 1)  # Mary of Egypt canonical date
        # Must NOT be on Holy Week date as Mary of Egypt
        if holy_week_date in calendar:
            assert calendar[holy_week_date]["primary_name"] != "Mary of Egypt"
        # Mary of Egypt must appear somewhere AFTER Easter Week (April 4, 2026)
        easter_week_end = date(2026, 4, 11)  # Saturday after Easter (April 5 + 6)
        mary_dates = [
            d for d, f in calendar.items()
            if "Mary of Egypt" in f.get("primary_name", "")
        ]
        assert mary_dates, "Mary of Egypt must appear somewhere in the calendar"
        assert all(d > easter_week_end for d in mary_dates)

    def test_holy_day_on_sunday_transfers_to_monday(
        self, feast_db: sqlite3.Connection
    ) -> None:
        """A non-Sunday-override holy_day falling on Sunday moves to Monday.

        The Conversion of Saint Paul (01-25) is a holy_day.  Check a year
        where 01-25 falls on a Sunday (2026: January 25 = Sunday).
        """
        # Jan 25, 2026 = Sunday
        target_date = date(2026, 1, 25)
        assert target_date.weekday() == 6, "Jan 25 2026 must be a Sunday for this test"
        calendar = resolve_with_precedence(2026, "anglican", feast_db)
        # Conversion of St Paul should NOT be on the Sunday
        if target_date in calendar:
            assert "Conversion" not in calendar[target_date].get("primary_name", "")
        # It should appear on Monday Jan 26
        monday = date(2026, 1, 26)
        assert monday in calendar
        assert "Conversion" in calendar[monday].get("primary_name", "")


# ---------------------------------------------------------------------------
# Season-info unit tests
# ---------------------------------------------------------------------------


class TestSeasonInfo:
    def test_2025_easter(self) -> None:
        si = _si_2025()
        assert si.easter_date == date(2025, 4, 20)

    def test_2025_ash_wednesday(self) -> None:
        si = _si_2025()
        assert si.ash_wednesday == date(2025, 3, 5)

    def test_2025_second_sunday_of_easter(self) -> None:
        si = _si_2025()
        assert si.second_sunday_of_easter == date(2025, 4, 27)

    def test_2026_holy_saturday(self) -> None:
        si = _si_2026()
        assert si.holy_saturday == date(2026, 4, 4)

    def test_sunday_override_dates_constant(self) -> None:
        """The three Sunday-override dates are the expected fixed dates."""
        assert frozenset({"01-01", "02-02", "08-06"}) == SUNDAY_OVERRIDE_FIXED_DATES


# ---------------------------------------------------------------------------
# 20-date lectionarypage.net cross-check fixtures (§8.7 DoD)
# ---------------------------------------------------------------------------
#
# Sources checked against https://www.lectionarypage.net/
# (Calendar pages for 2025 and 2026).
#
# For each fixture: (date, expected_primary_name_substring, precedence, year)
# "substring" matching used because some names differ slightly between
# feasts.yaml and lectionarypage display.
#
# Transfer cases are marked with (T).
#
# Mismatches vs. lectionarypage.net:
#   - Lectionarypage sometimes lists "The Conversion of Saint Paul" on Jan 26
#     when Jan 25 falls on a Sunday — matches our transfer logic.
#   - All Saints' Day 2026 lands on a Sunday (Nov 1) and is a principal feast;
#     lectionarypage keeps it on Nov 1 (no transfer) — matches our logic.
#
CROSS_CHECK_FIXTURES = [
    # 2026 principal feasts
    (date(2026, 1, 6),   "Epiphany",            "principal_feast", 2026),
    (date(2026, 4, 5),   "Easter",              "principal_feast", 2026),
    (date(2026, 5, 14),  "Ascension",           "principal_feast", 2026),
    (date(2026, 5, 24),  "Pentecost",           "principal_feast", 2026),
    (date(2026, 5, 31),  "Trinity",             "principal_feast", 2026),
    (date(2026, 11, 1),  "All Saints",          "principal_feast", 2026),
    (date(2026, 12, 25), "Christmas",           "principal_feast", 2026),
    # 2026 holy days on non-Sunday weekdays
    (date(2026, 1, 1),   "Holy Name",           "holy_day",        2026),
    (date(2026, 2, 2),   "Presentation",        "holy_day",        2026),
    (date(2026, 2, 18),  "Ash Wednesday",       "holy_day",        2026),
    (date(2026, 3, 19),  "Joseph",              "holy_day",        2026),
    (date(2026, 3, 25),  "Annunciation",        "holy_day",        2026),
    (date(2026, 6, 24),  "John the Baptist",    "holy_day",        2026),
    (date(2026, 8, 6),   "Transfiguration",     "holy_day",        2026),
    (date(2026, 9, 14),  "Holy Cross",          "holy_day",        2026),
    # 2026 lesser commemoration + transfer case (T)
    (date(2026, 3, 16),  "Vincent de Paul",     "lesser_commemoration", 2026),  # (T) blocked by Lenten Sunday 03-15
    # 2025 transfer cases
    (date(2025, 4, 28),  "Saint Mark",          "holy_day",        2025),  # (T) from Easter Week 04-25
    # 2025 Sundays
    (date(2025, 4, 20),  "Easter",              "principal_feast", 2025),
    (date(2025, 5, 29),  "Ascension",           "principal_feast", 2025),
    (date(2025, 6, 8),   "Pentecost",           "principal_feast", 2025),
]


class TestLectionaryPageCrossCheck:
    """Verify resolve_with_precedence against lectionarypage.net for 20 dates.

    All 20 are expected to pass.  Any fixture that diverges from lectionarypage
    is annotated with the rationale in CROSS_CHECK_FIXTURES above.
    """

    @pytest.fixture(scope="class")
    def calendars(self, feast_db: sqlite3.Connection) -> dict[int, dict[date, dict]]:
        cal_2025 = resolve_with_precedence(2025, "anglican", feast_db)
        cal_2026 = resolve_with_precedence(2026, "anglican", feast_db)
        return {2025: cal_2025, 2026: cal_2026}

    @pytest.mark.parametrize(
        "target_date,expected_name_fragment,expected_precedence,year",
        CROSS_CHECK_FIXTURES,
    )
    def test_cross_check_fixture(
        self,
        target_date: date,
        expected_name_fragment: str,
        expected_precedence: str,
        year: int,
        calendars: dict,
    ) -> None:
        calendar = calendars[year]
        assert target_date in calendar, (
            f"Expected a feast on {target_date} containing '{expected_name_fragment}', "
            f"but date is absent from the calendar"
        )
        winner = calendar[target_date]
        assert expected_name_fragment.lower() in winner["primary_name"].lower(), (
            f"On {target_date}: expected name containing '{expected_name_fragment}', "
            f"got '{winner['primary_name']}'"
        )
        assert winner["precedence"] == expected_precedence, (
            f"On {target_date} ('{winner['primary_name']}'): "
            f"expected precedence '{expected_precedence}', got '{winner['precedence']}'"
        )
