"""Liturgical calendar resolver (Phase 0.4 stub + Phase 4.5 precedence).

Provides movable-feast date math, fixed-date lookup against the ``feast``
table, and LFF 2024 precedence ordering with transfer logic.

Public API
----------
movable_feasts_for_year(year, tradition) -> dict[str, date]
resolve_fixed_date(conn, date, tradition) -> list[dict]
resolve_movable_date(conn, date, tradition) -> list[dict]
resolve(conn, date, tradition) -> list[dict]
precedence_rank(feast_dict, resolved_date, season_info) -> int
apply_precedence(feasts_on_date, resolved_date, season_info) -> dict | None
resolve_with_precedence(year, tradition, conn) -> dict[date, dict]

LFF 2024 Precedence Rules (verbatim from "The Calendar of the Church Year", pp. 3–6)
-------------------------------------------------------------------------------------
1. Principal Feasts
   "The Principal Feasts observed in this Church are the following: Easter Day,
   Ascension Day, The Day of Pentecost, Trinity Sunday, All Saints' Day,
   Christmas Day, The Epiphany. These feasts take precedence of any other day
   or observance."

2. Sundays
   "All Sundays of the year are feasts of our Lord Jesus Christ. In addition to
   the dated days listed above [Principal Feasts], only the following feasts,
   appointed on fixed days, take precedence of a Sunday: The Holy Name, The
   Presentation, The Transfiguration."
   "All other Feasts of our Lord, and all other Major Feasts appointed on fixed
   days in the Calendar, when they occur on a Sunday, are normally transferred
   to the first convenient open day within the week."

3. Holy Days
   "The following Holy Days are regularly observed throughout the year. Unless
   otherwise ordered in the preceding rules concerning Sundays, they have
   precedence over all other days of commemoration or of special observance."
   [Lists: Other Feasts of our Lord (Holy Name, Presentation, Annunciation,
   Visitation, Transfiguration, Holy Cross Day), All feasts of Apostles, All
   feasts of Evangelists, Saint Stephen, The Holy Innocents, Saint Joseph,
   Saint Mary Magdalene, Saint Mary the Virgin, Saint Michael and All Angels,
   Saint James of Jerusalem, Independence Day, Thanksgiving Day,
   Saint John the Baptist; Fasts: Ash Wednesday, Good Friday.]
   "Feasts appointed on fixed days in the Calendar are not observed on the days
   of Holy Week or of Easter Week. Major Feasts falling in these weeks are
   transferred to the week following the Second Sunday of Easter, in the order
   of their occurrence."
   "Feasts appointed on fixed days in the Calendar do not take precedence over
   Ash Wednesday."

4. Days of Special Devotion (not a precedence level but relevant to transfer)
   "Ash Wednesday and the other weekdays of Lent and of Holy Week, except the
   feast of the Annunciation."

5. Days of Optional Observance (= Lesser Commemorations in the feast table)
   "Subject to the rules of precedence governing Principal Feasts, Sundays, and
   Holy Days, the following may be observed ... Commemorations listed in the
   Calendar ..."

Implementation notes (Phase 4.5)
---------------------------------
- ``FeastRow`` is an alias for ``dict`` (plain dict from sqlite3.Row conversion).
- ``SeasonInfo`` carries pre-computed boundary dates for a given year so
  ``precedence_rank`` and ``apply_precedence`` stay pure (no I/O).
- The three feasts that take precedence over ANY Sunday (Holy Name 01-01,
  Presentation 02-02, Transfiguration 08-06) are encoded as
  ``SUNDAY_OVERRIDE_FIXED_DATES``.
- Holy Week = Palm Sunday through Holy Saturday (inclusive).
- Easter Week = Easter Day through the following Saturday (inclusive).
- Feasts in Holy Week or Easter Week transfer to the week AFTER the 2nd Sunday
  of Easter, maintaining their calendar order.
- Lesser commemorations blocked by a higher-precedence feast (including Sundays
  and Lenten Sundays) transfer to the next open non-Sunday, non-blocked weekday.
- Annunciation (03-25) is explicitly carved out from Lenten suppression by the
  BCP rules: "except the feast of the Annunciation."
- Out of scope (document for 5.1): Octaves, Eves of feasts, Ember Days
  precedence interactions. These are noted in the rules as "optional" but the
  transfer logic here does not model them explicitly.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from dateutil.easter import EASTER_ORTHODOX, EASTER_WESTERN, easter

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

FeastRow = dict  # plain dict produced by sqlite3.Row conversion

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

# Fixed MM-DD dates that take precedence over ANY Sunday (BCP/LFF rule 2)
SUNDAY_OVERRIDE_FIXED_DATES: frozenset[str] = frozenset({"01-01", "02-02", "08-06"})

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
# Season info — pre-computed boundaries for a given year
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeasonInfo:
    """Pre-computed liturgical boundary dates for a calendar year.

    All attributes are dates for the *year* supplied to ``season_info_for_year``.
    The Advent / Christmas boundary is intentionally excluded — Advent starts in
    the prior calendar year's November/December and is not needed for the
    transfer rules implemented here.
    """

    year: int
    easter_date: date
    ash_wednesday: date
    palm_sunday: date          # first day of Holy Week
    holy_saturday: date        # last day of Holy Week
    easter_saturday: date      # last day of Easter Week
    second_sunday_of_easter: date
    # Start of the week AFTER the 2nd Sunday of Easter (transfer target for
    # feasts displaced from Holy Week / Easter Week)
    week_after_second_sunday: date


def season_info_for_year(
    year: int,
    tradition: Literal["anglican", "byzantine"] = "anglican",
) -> SeasonInfo:
    """Compute liturgical boundary dates for *year*."""
    method = EASTER_WESTERN if tradition == "anglican" else EASTER_ORTHODOX
    easter_date: date = easter(year, method=method)  # type: ignore[arg-type]

    ash_wednesday = easter_date - timedelta(days=46)
    palm_sunday = easter_date - timedelta(days=7)
    holy_saturday = easter_date - timedelta(days=1)
    easter_saturday = easter_date + timedelta(days=6)
    second_sunday_of_easter = easter_date + timedelta(days=7)
    week_after_second_sunday = second_sunday_of_easter + timedelta(days=1)

    return SeasonInfo(
        year=year,
        easter_date=easter_date,
        ash_wednesday=ash_wednesday,
        palm_sunday=palm_sunday,
        holy_saturday=holy_saturday,
        easter_saturday=easter_saturday,
        second_sunday_of_easter=second_sunday_of_easter,
        week_after_second_sunday=week_after_second_sunday,
    )


def is_in_holy_week(d: date, si: SeasonInfo) -> bool:
    """Return True if *d* falls within Holy Week (Palm Sunday through Holy Saturday)."""
    return si.palm_sunday <= d <= si.holy_saturday


def is_in_easter_week(d: date, si: SeasonInfo) -> bool:
    """Return True if *d* falls within Easter Week (Easter Day through Saturday)."""
    return si.easter_date <= d <= si.easter_saturday


def is_sunday_in_advent_lent_easter(d: date, year: int, si: SeasonInfo) -> bool:
    """Return True if *d* is a Sunday in Advent, Lent, or the Easter season.

    Lent Sundays: Ash Wednesday through Holy Saturday (inclusive).
    Easter season Sundays: Easter Day through Pentecost (inclusive).
    Advent Sundays: the four Sundays before Christmas.
    """
    if d.weekday() != 6:  # not a Sunday
        return False

    # Lent: Ash Wednesday to Holy Saturday
    if si.ash_wednesday <= d <= si.holy_saturday:
        return True

    # Easter season: Easter through Pentecost
    pentecost = si.easter_date + timedelta(days=49)
    if si.easter_date <= d <= pentecost:
        return True

    # Advent: the four Sundays before Christmas (Dec 25 of same calendar year)
    christmas = date(year, 12, 25)
    advent_1 = christmas - timedelta(days=22)  # may go back further if needed
    # Find actual first Sunday of Advent (Sunday on or after Nov 27)
    advent_start_search = date(year, 11, 27)
    d_iter = advent_start_search
    while d_iter.weekday() != 6:
        d_iter += timedelta(days=1)
    advent_1 = d_iter
    advent_end = christmas - timedelta(days=1)
    return advent_1 <= d <= advent_end


# ---------------------------------------------------------------------------
# Precedence ranking
# ---------------------------------------------------------------------------

# Rank values: lower = higher priority.
_RANK_PRINCIPAL_FEAST = 1
_RANK_SUNDAY_OVERRIDE = 2   # Holy Name, Presentation, Transfiguration on a Sunday
_RANK_SUNDAY = 3            # All other Sundays
_RANK_HOLY_DAY = 4
_RANK_LESSER_COMMEMORATION = 5
_RANK_FERIAL = 6


def precedence_rank(feast: FeastRow, resolved_date: date, si: SeasonInfo) -> int:
    """Return a sort rank (lower = higher priority) for *feast* on *resolved_date*.

    Parameters
    ----------
    feast:
        A feast dict (from the ``feast`` table; keys include ``precedence``,
        ``calendar_type``, ``date_rule``).
    resolved_date:
        The date on which *feast* is actually being evaluated (may differ from
        its canonical date for transferred feasts).
    si:
        Pre-computed season boundaries for the relevant year.
    """
    prec = feast.get("precedence", "ferial")

    if prec == "principal_feast":
        return _RANK_PRINCIPAL_FEAST

    if prec == "ferial":
        return _RANK_FERIAL

    if prec == "lesser_commemoration":
        return _RANK_LESSER_COMMEMORATION

    # holy_day — could be in the "Sunday override" set
    if prec == "holy_day":
        if (
            feast.get("calendar_type") == "fixed"
            and feast.get("date_rule") in SUNDAY_OVERRIDE_FIXED_DATES
            and resolved_date.weekday() == 6  # it IS the Sunday in question
        ):
            return _RANK_SUNDAY_OVERRIDE
        return _RANK_HOLY_DAY

    # Fallback
    return _RANK_FERIAL


def _sunday_rank(resolved_date: date) -> int:
    """Rank for a plain Sunday (feasts of our Lord Jesus Christ)."""
    if resolved_date.weekday() != 6:
        return _RANK_FERIAL
    return _RANK_SUNDAY


# ---------------------------------------------------------------------------
# Apply precedence — pick winner for a single date
# ---------------------------------------------------------------------------


def apply_precedence(
    feasts_on_date: list[FeastRow],
    resolved_date: date,
    si: SeasonInfo,
) -> FeastRow | None:
    """Given all feasts resolving to *resolved_date*, return the LFF 2024 winner.

    Returns ``None`` if the list is empty (ferial day).

    Tie-breaking for equal-rank holy days: the feast whose ``date_rule``
    sorts earlier (i.e., appeared earlier in the calendar year) wins; this
    reflects the LFF principle of ordering transfers by order of occurrence.
    """
    if not feasts_on_date:
        return None

    def sort_key(f: FeastRow) -> tuple[int, str]:
        rank = precedence_rank(f, resolved_date, si)
        # Secondary sort: feasts that appear earlier in the year first
        return (rank, f.get("date_rule", ""))

    sorted_feasts = sorted(feasts_on_date, key=sort_key)
    return sorted_feasts[0]


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
# Public resolvers (Phase 0.4)
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
    with no precedence ordering.  Precedence is implemented in task 4.5 via
    ``resolve_with_precedence``.
    """
    return resolve_fixed_date(conn, query_date, tradition) + resolve_movable_date(
        conn, query_date, tradition
    )


# ---------------------------------------------------------------------------
# Full-year resolver with precedence + transfers (Phase 4.5)
# ---------------------------------------------------------------------------


def _resolve_all_fixed_for_year(
    conn: sqlite3.Connection,
    year: int,
    tradition: str,
) -> list[tuple[date, FeastRow]]:
    """Return (canonical_date, feast_dict) pairs for all fixed and commemoration feasts.

    Both ``calendar_type = 'fixed'`` and ``calendar_type = 'commemoration'`` use
    MM-DD date rules and are treated identically for precedence purposes.
    """
    _ensure_row_factory(conn)
    if not _feast_table_exists(conn):
        return []

    rows = conn.execute(
        "SELECT * FROM feast"
        " WHERE calendar_type IN ('fixed', 'commemoration') AND tradition = ?",
        (tradition,),
    ).fetchall()

    result: list[tuple[date, FeastRow]] = []
    for row in rows:
        feast = dict(row)
        rule: str = feast["date_rule"]
        # Expect MM-DD format
        m = re.match(r"^(\d{2})-(\d{2})$", rule)
        if m is None:
            continue
        try:
            canonical = date(year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue  # e.g. Feb 29 in non-leap year
        result.append((canonical, feast))

    return result


def _first_weekday_on_or_after(d: date, weekday: int) -> date:
    """Return the first date on or after *d* with ``date.weekday() == weekday``."""
    days_ahead = (weekday - d.weekday()) % 7
    return d + timedelta(days=days_ahead)


def _first_weekday_after(d: date, weekday: int) -> date:
    """Return the first date strictly after *d* with ``date.weekday() == weekday``."""
    result = _first_weekday_on_or_after(d, weekday)
    if result == d:
        result += timedelta(days=7)
    return result


def _nth_weekday_of_month(year: int, month: int, n: int, weekday: int) -> date:
    """Return the *n*-th occurrence of *weekday* in *month* of *year* (1-indexed)."""
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + (n - 1) * 7)


# Weekday name → weekday integer (Monday=0, Sunday=6)
_WEEKDAY_NAME_MAP: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_MONTH_MAP: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
_ORDINAL_MAP: dict[str, int] = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
}


def _compute_movable_date(rule: str, year: int, si: SeasonInfo) -> date | None:
    """Compute the calendar date for a non-fixed feast given its *rule* string.

    Handles:
    - ``easter±N``
    - ``advent_1±N``  (First Sunday of Advent is Sunday on or after Nov 27)
    - ``sunday_on_or_after_MM-DD``
    - ``first_sunday_after_MM-DD``
    - ``{weekday}_on_or_after_MM-DD``  (e.g., ``wednesday_after_09-14``)
    - ``{weekday}_after_MM-DD``
    - ``fourth_thursday_of_november`` (one-off)
    - ``{ordinal}_{weekday}_of_{month}`` (generic)

    Returns ``None`` if the rule is unrecognised or yields an out-of-year date.
    """
    rule = rule.strip()

    # easter±N
    m = _MOVABLE_RULE_RE.match(rule)
    if m:
        offset = int(m.group(1))
        return si.easter_date + timedelta(days=offset)

    # advent_1±N  — first Sunday of Advent is Sunday on or after Nov 27
    m_adv = re.match(r"^advent_1([+-]\d+)$", rule)
    if m_adv:
        offset = int(m_adv.group(1))
        # First Sunday of Advent: Sunday on or after Nov 27 of the same year
        nov27 = date(year, 11, 27)
        advent_1 = _first_weekday_on_or_after(nov27, 6)  # 6 = Sunday
        return advent_1 + timedelta(days=offset)

    # sunday_on_or_after_MM-DD  (e.g., sunday_on_or_after_11-27)
    m_sun_oa = re.match(r"^sunday_on_or_after_(\d{2})-(\d{2})$", rule)
    if m_sun_oa:
        month, day = int(m_sun_oa.group(1)), int(m_sun_oa.group(2))
        try:
            anchor = date(year, month, day)
        except ValueError:
            return None
        return _first_weekday_on_or_after(anchor, 6)

    # first_sunday_after_MM-DD  (strict — skip the anchor date if it's Sunday)
    m_sun_a = re.match(r"^first_sunday_after_(\d{2})-(\d{2})$", rule)
    if m_sun_a:
        month, day = int(m_sun_a.group(1)), int(m_sun_a.group(2))
        try:
            anchor = date(year, month, day)
        except ValueError:
            return None
        return _first_weekday_after(anchor, 6)

    # {weekday}_on_or_after_MM-DD  (e.g., wednesday_on_or_after_09-14)
    m_wd_oa = re.match(r"^(\w+)_on_or_after_(\d{2})-(\d{2})$", rule)
    if m_wd_oa:
        wday_name = m_wd_oa.group(1).lower()
        month, day = int(m_wd_oa.group(2)), int(m_wd_oa.group(3))
        wday = _WEEKDAY_NAME_MAP.get(wday_name)
        if wday is None:
            return None
        try:
            anchor = date(year, month, day)
        except ValueError:
            return None
        return _first_weekday_on_or_after(anchor, wday)

    # {weekday}_after_MM-DD  (e.g., wednesday_after_09-14, friday_after_12-13)
    m_wd_a = re.match(r"^(\w+)_after_(\d{2})-(\d{2})$", rule)
    if m_wd_a:
        wday_name = m_wd_a.group(1).lower()
        month, day = int(m_wd_a.group(2)), int(m_wd_a.group(3))
        wday = _WEEKDAY_NAME_MAP.get(wday_name)
        if wday is None:
            return None
        try:
            anchor = date(year, month, day)
        except ValueError:
            return None
        return _first_weekday_after(anchor, wday)

    # {ordinal}_{weekday}_of_{month}  e.g., fourth_thursday_of_november
    m_ord = re.match(r"^(first|second|third|fourth|fifth)_(\w+)_of_(\w+)$", rule)
    if m_ord:
        ordinal_val = _ORDINAL_MAP.get(m_ord.group(1))
        wday_val = _WEEKDAY_NAME_MAP.get(m_ord.group(2).lower())
        month_val = _MONTH_MAP.get(m_ord.group(3).lower())
        if ordinal_val is None or wday_val is None or month_val is None:
            return None
        try:
            return _nth_weekday_of_month(year, month_val, ordinal_val, wday_val)
        except ValueError:
            return None

    return None


def _resolve_all_movable_for_year(
    conn: sqlite3.Connection,
    year: int,
    tradition: str,
    si: SeasonInfo,
) -> list[tuple[date, FeastRow]]:
    """Return (canonical_date, feast_dict) pairs for all movable feasts."""
    _ensure_row_factory(conn)
    if not _feast_table_exists(conn):
        return []

    rows = conn.execute(
        "SELECT * FROM feast WHERE calendar_type = 'movable' AND tradition = ?",
        (tradition,),
    ).fetchall()

    result: list[tuple[date, FeastRow]] = []
    for row in rows:
        feast = dict(row)
        rule: str = feast["date_rule"]
        canonical = _compute_movable_date(rule, year, si)
        if canonical is None:
            continue
        if canonical.year != year:
            continue  # computed date outside requested year (e.g., Advent 1 in a different year)
        result.append((canonical, feast))

    return result


def _find_next_open_day(
    start: date,
    occupied: set[date],
) -> date:
    """Return the first date >= *start* that is not a Sunday and not in *occupied*."""
    d = start
    while True:
        if d.weekday() != 6 and d not in occupied:
            return d
        d += timedelta(days=1)


def resolve_with_precedence(
    year: int,
    tradition: str = "anglican",
    conn: sqlite3.Connection | None = None,
) -> dict[date, FeastRow]:
    """Full year → date-to-winner map applying LFF 2024 precedence + transfers.

    Parameters
    ----------
    year:
        Calendar year to resolve.
    tradition:
        Liturgical tradition (only ``'anglican'`` is fully implemented; Phase 4.5).
    conn:
        Optional SQLite connection.  If ``None``, returns an empty dict (useful
        for testing the pure-precedence helpers without a DB).

    Returns
    -------
    dict[date, FeastRow]
        Maps each date that has an observance to the winning feast dict.
        Dates with no observance are absent from the map.
        Transferred feasts appear at their *final* date (not their canonical
        date_rule date).

    Transfer rules applied
    ----------------------
    1. Principal feasts: never transferred; always win on their canonical date.
    2. Holy Name (01-01), Presentation (02-02), Transfiguration (08-06): may
       fall on a Sunday and win — not transferred.
    3. Other Holy Days falling on a Sunday: transferred to first open weekday
       within the week (Monday through Saturday).
    4. Holy Days falling in Holy Week or Easter Week: transferred to the week
       after the Second Sunday of Easter, in calendar order.
    5. Holy Days may not take precedence over Ash Wednesday (a fixed holy_day
       that holds its date against other fixed feasts).
    6. Lesser commemorations blocked by ANY higher-precedence day (including
       Sundays, Holy Days, other principal feasts, Ash Wednesday, Holy Week
       weekdays, Easter Week weekdays): transferred to next open weekday.
    7. Lesser commemorations in Holy Week or Easter Week: suppressed (per LFF
       §3 which says feasts not observed in those weeks); they transfer to next
       open weekday after Easter Week.

    Out of scope for this phase
    ---------------------------
    - Octaves and Eves
    - Ember and Rogation Days precedence interactions
    - Byzantine / Roman traditions
    - Second Sunday of Easter "extra" All Saints observance
    """
    trad: Literal["anglican", "byzantine"] = (
        "anglican" if tradition == "anglican" else "byzantine"
    )
    si = season_info_for_year(year, trad)

    if conn is None:
        return {}

    # ------------------------------------------------------------------
    # Step 1: collect all canonical (date, feast) pairs for the year
    # ------------------------------------------------------------------
    fixed_pairs = _resolve_all_fixed_for_year(conn, year, tradition)
    movable_pairs = _resolve_all_movable_for_year(conn, year, tradition, si)
    all_pairs = fixed_pairs + movable_pairs

    # ------------------------------------------------------------------
    # Step 2: Group by canonical date
    # ------------------------------------------------------------------
    from collections import defaultdict

    by_canonical: dict[date, list[FeastRow]] = defaultdict(list)
    for canonical_date, feast in all_pairs:
        by_canonical[canonical_date].append(feast)

    # ------------------------------------------------------------------
    # Step 3: First pass — resolve principal feasts (they never move)
    # ------------------------------------------------------------------
    # These occupy their slots unconditionally.
    occupied: set[date] = set()          # dates already "owned" by a winner
    final_calendar: dict[date, FeastRow] = {}

    # Principal feasts go in first
    for canonical_date, feasts in sorted(by_canonical.items()):
        principal = [f for f in feasts if f.get("precedence") == "principal_feast"]
        if principal:
            winner = apply_precedence(principal, canonical_date, si)
            if winner is not None:
                final_calendar[canonical_date] = winner
                occupied.add(canonical_date)

    # ------------------------------------------------------------------
    # Step 4: Holy Days — with transfer rules
    # ------------------------------------------------------------------
    # Sort by canonical date so "order of their occurrence" is preserved
    # for Holy-Week/Easter-Week transfers.
    holy_day_queue: list[tuple[date, FeastRow]] = []
    for canonical_date, feasts in sorted(by_canonical.items()):
        holy_days = [
            f
            for f in feasts
            if f.get("precedence") == "holy_day"
            and canonical_date.year == year
        ]
        for hd in holy_days:
            holy_day_queue.append((canonical_date, hd))

    holy_day_queue.sort(key=lambda x: x[0])

    for canonical_date, feast in holy_day_queue:
        date_rule = feast.get("date_rule", "")
        is_fixed = feast.get("calendar_type") == "fixed"

        # --- Rule: Holy Days in Holy Week or Easter Week transfer to week
        #     after 2nd Sunday of Easter, in canonical order.
        if is_fixed and (
            is_in_holy_week(canonical_date, si) or is_in_easter_week(canonical_date, si)
        ):
            # Annunciation (03-25) in Holy Week: BCP explicitly allows it
            # (Days of Special Devotion: "except the feast of the Annunciation")
            # but the calendar rule says fixed feasts are NOT OBSERVED in HW/EW;
            # Annunciation is the one carve-out mentioned for Lenten discipline
            # (not for observance precedence). Per LFF §3, fixed feasts in HW/EW
            # still transfer.
            transfer_start = si.week_after_second_sunday
            target = _find_next_open_day(transfer_start, occupied)
            final_calendar[target] = feast
            occupied.add(target)
            continue

        # --- Rule: Fixed Holy Days falling on a Sunday (except the three
        #     that override Sundays) transfer to first open weekday in the week.
        if is_fixed and canonical_date.weekday() == 6:
            if date_rule in SUNDAY_OVERRIDE_FIXED_DATES:
                # These take precedence of the Sunday — place on canonical date
                target = canonical_date
            else:
                # Transfer to Monday (first open day within the week)
                target = _find_next_open_day(canonical_date + timedelta(days=1), occupied)
        else:
            target = canonical_date

        # --- Rule: Ash Wednesday holds — other fixed feasts do not take
        #     precedence over Ash Wednesday. If a fixed holy_day lands on
        #     Ash Wednesday AND is not the Ash Wednesday feast itself, skip
        #     (Ash Wednesday is movable and already placed as a holy_day).
        #     (The Annunciation on Ash Wednesday is an edge case; historically
        #     transferred to the nearest open day.)
        if target == si.ash_wednesday and date_rule != "easter-46":
            # Shift forward to the next open day
            target = _find_next_open_day(si.ash_wednesday + timedelta(days=1), occupied)

        if target in occupied:
            # Slot already taken — skip (a higher-precedence feast won it)
            # Lesser commemorations handle their own transfer logic below
            continue

        final_calendar[target] = feast
        occupied.add(target)

    # ------------------------------------------------------------------
    # Step 5: Lesser commemorations — transfer if blocked
    # ------------------------------------------------------------------
    # For each canonical date, pick ONE winner among the lesser comms on that
    # date (earlier date_rule wins ties).  Runners-up on the same canonical
    # date are dropped — they are not observed that year (LFF §5 says lesser
    # feasts are "optional"; the calendar has at most one slot per day).
    #
    # The winning feast for each canonical date is then transferred to the
    # next open weekday if its canonical slot is blocked by a higher-
    # precedence observance or by being a Sunday.
    lesser_by_canonical: dict[date, list[FeastRow]] = defaultdict(list)
    for canonical_date, feasts in by_canonical.items():
        for f in feasts:
            if (
                f.get("precedence") == "lesser_commemoration"
                and canonical_date.year == year
            ):
                lesser_by_canonical[canonical_date].append(f)

    for canonical_date in sorted(lesser_by_canonical):
        candidates = lesser_by_canonical[canonical_date]
        if not candidates:
            continue

        # Pick the ONE winner for this canonical date (best precedence rank;
        # tie-break by date_rule string then primary_name for stable ordering).
        winner = sorted(
            candidates,
            key=lambda f: (
                precedence_rank(f, canonical_date, si),
                f.get("date_rule", ""),
                f.get("primary_name", ""),
            ),
        )[0]

        # Determine target date
        if is_in_holy_week(canonical_date, si) or is_in_easter_week(canonical_date, si):
            # Transfer to week after 2nd Sunday of Easter
            transfer_start = si.week_after_second_sunday
            target = _find_next_open_day(transfer_start, occupied)
        elif canonical_date.weekday() == 6 or canonical_date in occupied:
            # Blocked by Sunday or higher-precedence feast — move to next open day
            search_start = canonical_date + timedelta(days=1)
            target = _find_next_open_day(search_start, occupied)
        else:
            target = canonical_date

        final_calendar[target] = winner
        occupied.add(target)

    return final_calendar
