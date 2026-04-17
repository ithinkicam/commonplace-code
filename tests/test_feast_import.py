"""Tests for scripts/feast_import.py — idempotent feast importer.

Covers:
- Fresh DB + valid YAML → main() returns 0; correct row count; cross-ref resolved;
  theological_subjects is valid JSON.
- Re-run on same YAML → idempotent (row count unchanged, updated_at bumps, id/created_at stable).
- Edit a field then re-run → that row's field updated; others unchanged.
- --dry-run → no DB rows touched but accurate counts reported.
- Missing cross-tradition reference → non-zero exit unless --ignore-missing-cross-refs.
- Validation error in YAML → non-zero exit; errors passed through.
- Empty feasts.yaml → succeeds with "0 feasts" message.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from io import StringIO
from pathlib import Path

from commonplace_db import connect, migrate

# ---------------------------------------------------------------------------
# Make scripts/ importable
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from feast_import import _make_slug, main  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "feasts"
VALID_FEASTS = FIXTURE_DIR / "valid_feasts.yaml"
VALID_SUBJECTS = FIXTURE_DIR / "valid_subjects.yaml"
CROSS_REF_FEASTS = FIXTURE_DIR / "cross_ref_feasts.yaml"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _feast_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM feast").fetchone()[0]


def _get_feast(conn: sqlite3.Connection, primary_name: str, tradition: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM feast WHERE primary_name = ? AND tradition = ?",
        (primary_name, tradition),
    ).fetchone()


def _run_main(tmp_path: Path, *extra_args: str, feasts: Path = VALID_FEASTS) -> tuple[int, str]:
    """Run main() with a temp DB and capture stdout. Returns (exit_code, stdout).

    When using valid_feasts.yaml (default), ``--ignore-missing-cross-refs`` is
    automatically appended because that fixture references a feast
    (saint_mary_the_virgin_anglican) that is intentionally absent — it exists
    only to test the cross_tradition_equivalent slug field on FeastEntry.
    Cross-ref *resolution* is tested via cross_ref_feasts.yaml instead.
    """
    db_path = str(tmp_path / "test.db")
    effective_args = list(extra_args)
    if feasts == VALID_FEASTS and "--ignore-missing-cross-refs" not in effective_args:
        effective_args.append("--ignore-missing-cross-refs")
    captured = StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured
    try:
        code = main(
            [
                "--feasts", str(feasts),
                "--subjects", str(VALID_SUBJECTS),
                "--db", db_path,
                *effective_args,
            ]
        )
    finally:
        sys.stdout = old_stdout
    return code, captured.getvalue()


# ---------------------------------------------------------------------------
# Slug helper unit tests
# ---------------------------------------------------------------------------


class TestMakeSlug:
    def test_simple(self) -> None:
        assert _make_slug("Easter Day", "shared") == "easter_day_shared"

    def test_apostrophe_and_spaces(self) -> None:
        slug = _make_slug("Saint Mary the Virgin", "anglican")
        assert slug == "saint_mary_the_virgin_anglican"

    def test_tradition_appended(self) -> None:
        assert _make_slug("Ash Wednesday", "anglican").endswith("_anglican")

    def test_cross_ref_fixture_slug(self) -> None:
        """The slug for the roman feast in cross_ref_feasts.yaml must match the ref string."""
        slug = _make_slug("Assumption of the Blessed Virgin Mary", "roman")
        assert slug == "assumption_of_the_blessed_virgin_mary_roman"


# ---------------------------------------------------------------------------
# Happy-path: fresh DB
# ---------------------------------------------------------------------------


class TestFreshImport:
    def test_returns_zero(self, tmp_path: Path) -> None:
        code, _ = _run_main(tmp_path)
        assert code == 0

    def test_correct_row_count(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _run_main(tmp_path)
        conn = connect(str(db_path))
        assert _feast_count(conn) == 4  # valid_feasts.yaml has 4 entries

    def test_theological_subjects_is_valid_json(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _run_main(tmp_path)
        conn = connect(str(db_path))
        rows = conn.execute("SELECT theological_subjects FROM feast").fetchall()
        for row in rows:
            val = row[0]
            if val is not None:
                parsed = json.loads(val)
                assert isinstance(parsed, list)

    def test_summary_output_contains_new_count(self, tmp_path: Path) -> None:
        _, out = _run_main(tmp_path)
        assert "4 new" in out

    def test_all_new_on_fresh_db(self, tmp_path: Path) -> None:
        _, out = _run_main(tmp_path)
        assert "0 updated" in out
        assert "0 unchanged" in out


# ---------------------------------------------------------------------------
# Cross-tradition equivalent resolution
# ---------------------------------------------------------------------------


class TestCrossRefResolution:
    def test_cross_ref_resolved(self, tmp_path: Path) -> None:
        """Dormition (byzantine) cross_tradition_equivalent_id → Assumption (roman)."""
        db_path = tmp_path / "test.db"
        code, _ = _run_main(tmp_path, feasts=CROSS_REF_FEASTS)
        assert code == 0

        conn = connect(str(db_path))
        dormition = _get_feast(conn, "Dormition of the Theotokos", "byzantine")
        assumption = _get_feast(conn, "Assumption of the Blessed Virgin Mary", "roman")

        assert dormition is not None
        assert assumption is not None
        assert dormition["cross_tradition_equivalent_id"] == assumption["id"]

    def test_no_self_ref(self, tmp_path: Path) -> None:
        """The referenced feast should not have cross_tradition_equivalent_id set (no ref in fixture)."""
        db_path = tmp_path / "test.db"
        _run_main(tmp_path, feasts=CROSS_REF_FEASTS)
        conn = connect(str(db_path))
        assumption = _get_feast(conn, "Assumption of the Blessed Virgin Mary", "roman")
        assert assumption is not None
        assert assumption["cross_tradition_equivalent_id"] is None


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_row_count_unchanged_on_rerun(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _run_main(tmp_path)
        count_after_first = _feast_count(connect(str(db_path)))
        _run_main(tmp_path)
        count_after_second = _feast_count(connect(str(db_path)))
        assert count_after_first == count_after_second

    def test_id_stable_on_rerun(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _run_main(tmp_path)
        conn = connect(str(db_path))
        id_first = _get_feast(conn, "Easter Day", "shared")["id"]

        _run_main(tmp_path)
        conn = connect(str(db_path))
        id_second = _get_feast(conn, "Easter Day", "shared")["id"]

        assert id_first == id_second

    def test_created_at_stable_on_rerun(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _run_main(tmp_path)
        conn = connect(str(db_path))
        created_first = _get_feast(conn, "Easter Day", "shared")["created_at"]

        _run_main(tmp_path)
        conn = connect(str(db_path))
        created_second = _get_feast(conn, "Easter Day", "shared")["created_at"]

        assert created_first == created_second

    def test_second_run_all_unchanged(self, tmp_path: Path) -> None:
        _run_main(tmp_path)
        _, out = _run_main(tmp_path)
        assert "0 new" in out
        assert "4 unchanged" in out

    def test_updated_at_bumps_on_update(self, tmp_path: Path) -> None:
        """Editing a field and re-running bumps updated_at."""
        db_path = tmp_path / "test.db"
        _run_main(tmp_path)
        conn = connect(str(db_path))
        updated_at_first = _get_feast(conn, "Easter Day", "shared")["updated_at"]

        # Wait a second so the timestamp can differ
        time.sleep(1.1)

        # Write a modified feasts.yaml that changes Easter Day's precedence
        modified = tmp_path / "modified_feasts.yaml"
        modified.write_text(
            "- primary_name: Easter Day\n"
            "  alternate_names: [Pascha]\n"
            "  tradition: shared\n"
            "  calendar_type: movable\n"
            "  date_rule: 'easter+0'\n"
            "  precedence: holy_day\n"  # changed from principal_feast
            "  theological_subjects: [resurrection]\n"
        )

        code, out = _run_main(tmp_path, feasts=modified)
        assert code == 0

        conn = connect(str(db_path))
        row = _get_feast(conn, "Easter Day", "shared")
        assert row["updated_at"] != updated_at_first
        assert row["precedence"] == "holy_day"


# ---------------------------------------------------------------------------
# Field-level update
# ---------------------------------------------------------------------------


class TestFieldUpdate:
    def test_edited_field_updated_others_unchanged(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _run_main(tmp_path)

        # Modify only Easter Day's date_rule (normally easter+0 → easter+1 for test)
        # Build a feasts file with ALL four entries, but easter changed
        modified = tmp_path / "modified_feasts.yaml"
        modified.write_text(
            "- primary_name: Dormition of the Theotokos\n"
            "  alternate_names: [Falling Asleep of the Virgin Mary, Koimesis]\n"
            "  tradition: byzantine\n"
            "  calendar_type: fixed\n"
            "  date_rule: '08-15'\n"
            "  precedence: principal_feast\n"
            "  theological_subjects: [theotokos, kenosis]\n"
            "  cross_tradition_equivalent: saint_mary_the_virgin_anglican\n"
            "\n"
            "- primary_name: Easter Day\n"
            "  alternate_names: [Pascha, The Resurrection of Our Lord]\n"
            "  tradition: shared\n"
            "  calendar_type: movable\n"
            "  date_rule: 'easter+0'\n"
            "  precedence: holy_day\n"  # changed
            "  theological_subjects: [resurrection]\n"
            "\n"
            "- primary_name: Ash Wednesday\n"
            "  alternate_names: []\n"
            "  tradition: anglican\n"
            "  calendar_type: movable\n"
            "  date_rule: 'easter-46'\n"
            "  precedence: holy_day\n"
            "  theological_subjects: [penitence, _other:mortality_and_dust]\n"
            "\n"
            "- primary_name: The Nativity of Our Lord\n"
            "  tradition: roman\n"
            "  calendar_type: fixed\n"
            "  date_rule: '12-25'\n"
            "  precedence: principal_feast\n"
            "  theological_subjects: [incarnation]\n"
        )

        # modified file still references saint_mary_the_virgin_anglican (absent) —
        # use --ignore-missing-cross-refs so the test focuses on field-update logic
        code, out = _run_main(tmp_path, "--ignore-missing-cross-refs", feasts=modified)
        assert code == 0

        conn = connect(str(db_path))
        easter = _get_feast(conn, "Easter Day", "shared")
        assert easter["precedence"] == "holy_day"

        ash = _get_feast(conn, "Ash Wednesday", "anglican")
        assert ash["precedence"] == "holy_day"  # unchanged

        # Summary: 1 updated, rest unchanged
        assert "1 updated" in out
        assert "3 unchanged" in out


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_no_db_writes(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        code, out = _run_main(tmp_path, "--dry-run")
        assert code == 0
        # DB should not have been written to (table doesn't exist / is empty)
        conn = connect(str(db_path))
        migrate(conn)
        assert _feast_count(conn) == 0

    def test_dry_run_reports_new_count(self, tmp_path: Path) -> None:
        _, out = _run_main(tmp_path, "--dry-run")
        assert "4 new" in out

    def test_dry_run_after_import_reports_unchanged(self, tmp_path: Path) -> None:
        _run_main(tmp_path)  # real run first
        _, out = _run_main(tmp_path, "--dry-run")  # dry-run second
        assert "4 unchanged" in out
        assert "0 new" in out


# ---------------------------------------------------------------------------
# Missing cross-tradition reference
# ---------------------------------------------------------------------------


class TestMissingCrossRef:
    def test_missing_cross_ref_fails(self, tmp_path: Path) -> None:
        """A cross_tradition_equivalent that resolves to no feast → non-zero exit."""
        feasts = tmp_path / "feasts.yaml"
        feasts.write_text(
            "- primary_name: Easter Day\n"
            "  tradition: anglican\n"
            "  calendar_type: movable\n"
            "  date_rule: 'easter+0'\n"
            "  precedence: principal_feast\n"
            "  theological_subjects: [resurrection]\n"
            "  cross_tradition_equivalent: nonexistent_feast_slug_byzantine\n"
        )
        db_path = str(tmp_path / "test.db")
        code = main(
            ["--feasts", str(feasts), "--subjects", str(VALID_SUBJECTS), "--db", db_path]
        )
        assert code != 0

    def test_missing_cross_ref_ignore_flag(self, tmp_path: Path) -> None:
        """With --ignore-missing-cross-refs the import succeeds despite unresolvable ref."""
        feasts = tmp_path / "feasts.yaml"
        feasts.write_text(
            "- primary_name: Easter Day\n"
            "  tradition: anglican\n"
            "  calendar_type: movable\n"
            "  date_rule: 'easter+0'\n"
            "  precedence: principal_feast\n"
            "  theological_subjects: [resurrection]\n"
            "  cross_tradition_equivalent: nonexistent_feast_slug_byzantine\n"
        )
        db_path = str(tmp_path / "test.db")
        code = main(
            [
                "--feasts", str(feasts),
                "--subjects", str(VALID_SUBJECTS),
                "--db", db_path,
                "--ignore-missing-cross-refs",
            ]
        )
        assert code == 0


# ---------------------------------------------------------------------------
# Validation error passthrough
# ---------------------------------------------------------------------------


class TestValidationErrors:
    def test_bad_date_rule_yields_nonzero(self, tmp_path: Path) -> None:
        code, _ = _run_main(tmp_path, feasts=FIXTURE_DIR / "invalid_bad_date_rule.yaml")
        assert code != 0

    def test_bad_tradition_yields_nonzero(self, tmp_path: Path) -> None:
        code, _ = _run_main(tmp_path, feasts=FIXTURE_DIR / "invalid_bad_tradition.yaml")
        assert code != 0

    def test_uncontrolled_subject_yields_nonzero(self, tmp_path: Path) -> None:
        code, _ = _run_main(
            tmp_path, feasts=FIXTURE_DIR / "invalid_uncontrolled_subject.yaml"
        )
        assert code != 0


# ---------------------------------------------------------------------------
# Empty feasts file
# ---------------------------------------------------------------------------


class TestEmptyFeasts:
    def test_empty_feasts_returns_zero(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty_feasts.yaml"
        empty.write_text("")
        db_path = str(tmp_path / "test.db")
        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            code = main(
                ["--feasts", str(empty), "--subjects", str(VALID_SUBJECTS), "--db", db_path]
            )
        finally:
            sys.stdout = old_stdout
        assert code == 0
        assert "0 feasts" in captured.getvalue()

    def test_empty_feasts_no_db_writes(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty_feasts.yaml"
        empty.write_text("")
        db_path = str(tmp_path / "test.db")
        main(["--feasts", str(empty), "--subjects", str(VALID_SUBJECTS), "--db", db_path])
        # connect without migrate — feast table shouldn't even be needed
        conn = connect(db_path)
        migrate(conn)
        assert _feast_count(conn) == 0
