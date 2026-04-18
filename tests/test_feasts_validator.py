"""Tests for commonplace_db.feast_schema — Pydantic validator for feasts.yaml.

Covers:
- Valid files round-trip without error.
- Each invalid fixture produces the expected error (match on substring).
- ``_other:<anything>`` is accepted even when not in the controlled set.
- Empty feasts.yaml validates as an empty list.
- Subjects file with duplicated subject entries is rejected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from commonplace_db.feast_schema import FeastEntry, FeastValidationError, validate_feasts

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "feasts"
VALID_FEASTS = FIXTURE_DIR / "valid_feasts.yaml"
VALID_SUBJECTS = FIXTURE_DIR / "valid_subjects.yaml"


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_valid_round_trip() -> None:
    """Valid feasts.yaml + valid subjects.yaml returns a non-empty list of FeastEntry."""
    entries = validate_feasts(VALID_FEASTS, VALID_SUBJECTS)
    assert isinstance(entries, list)
    assert len(entries) == 4
    assert all(isinstance(e, FeastEntry) for e in entries)


def test_valid_round_trip_names() -> None:
    """Spot-check that primary_name and alternate_names survive the round-trip."""
    entries = validate_feasts(VALID_FEASTS, VALID_SUBJECTS)
    names = [e.primary_name for e in entries]
    assert "Dormition of the Theotokos" in names
    assert "Easter Day" in names


def test_valid_entry_fields() -> None:
    """Check that individual field types are correctly parsed."""
    entries = validate_feasts(VALID_FEASTS, VALID_SUBJECTS)
    dormition = next(e for e in entries if e.primary_name == "Dormition of the Theotokos")
    assert dormition.tradition == "byzantine"
    assert dormition.calendar_type == "fixed"
    assert dormition.date_rule == "08-15"
    assert dormition.precedence == "principal_feast"
    assert "theotokos" in dormition.theological_subjects
    assert dormition.cross_tradition_equivalent == "saint_mary_the_virgin_anglican"


def test_alternate_names_default_empty(tmp_path: Path) -> None:
    """A feast with no alternate_names field gets an empty list."""
    feasts_yaml = tmp_path / "feasts.yaml"
    feasts_yaml.write_text(
        "- primary_name: Feast of the Nativity\n"
        "  tradition: roman\n"
        "  calendar_type: fixed\n"
        "  date_rule: '12-25'\n"
        "  precedence: principal_feast\n"
        "  source: local\n"
    )
    entries = validate_feasts(feasts_yaml, VALID_SUBJECTS)
    assert entries[0].alternate_names == []


def test_empty_feasts_yaml(tmp_path: Path) -> None:
    """An empty (null) feasts.yaml validates as an empty list."""
    feasts_yaml = tmp_path / "feasts.yaml"
    feasts_yaml.write_text("")  # empty file → yaml.safe_load returns None
    entries = validate_feasts(feasts_yaml, VALID_SUBJECTS)
    assert entries == []


def test_other_escape_hatch_accepted() -> None:
    """``_other:<anything>`` is accepted even when the freeform part is not in controlled set."""
    entries = validate_feasts(VALID_FEASTS, VALID_SUBJECTS)
    ash_wed = next(e for e in entries if e.primary_name == "Ash Wednesday")
    # _other:mortality_and_dust should be in the list without error
    assert "_other:mortality_and_dust" in ash_wed.theological_subjects


def test_other_escape_hatch_arbitrary(tmp_path: Path) -> None:
    """Any string starting with ``_other:`` passes even with a completely fresh vocabulary."""
    subjects_yaml = tmp_path / "subjects.yaml"
    subjects_yaml.write_text("- subject: resurrection\n")
    feasts_yaml = tmp_path / "feasts.yaml"
    feasts_yaml.write_text(
        "- primary_name: Test Feast\n"
        "  tradition: anglican\n"
        "  calendar_type: fixed\n"
        "  date_rule: '01-01'\n"
        "  precedence: lesser_commemoration\n"
        "  source: lff_2024\n"
        "  theological_subjects: [_other:completely_new_concept]\n"
    )
    entries = validate_feasts(feasts_yaml, subjects_yaml)
    assert entries[0].theological_subjects == ["_other:completely_new_concept"]


# ---------------------------------------------------------------------------
# Invalid feast fixtures
# ---------------------------------------------------------------------------


def test_invalid_bad_date_rule() -> None:
    """A date_rule that doesn't match MM-DD or easter[+-]N is rejected."""
    with pytest.raises(FeastValidationError) as exc_info:
        validate_feasts(FIXTURE_DIR / "invalid_bad_date_rule.yaml", VALID_SUBJECTS)
    joined = " ".join(exc_info.value.errors)
    assert "date_rule" in joined


def test_invalid_bad_tradition() -> None:
    """An unknown tradition literal is rejected."""
    with pytest.raises(FeastValidationError) as exc_info:
        validate_feasts(FIXTURE_DIR / "invalid_bad_tradition.yaml", VALID_SUBJECTS)
    # Pydantic's Literal error mentions 'tradition' or the invalid value
    joined = " ".join(exc_info.value.errors)
    assert "lutheran" in joined or "tradition" in joined


def test_invalid_uncontrolled_subject() -> None:
    """A theological_subject that's neither controlled nor ``_other:`` is rejected."""
    with pytest.raises(FeastValidationError) as exc_info:
        validate_feasts(FIXTURE_DIR / "invalid_uncontrolled_subject.yaml", VALID_SUBJECTS)
    joined = " ".join(exc_info.value.errors)
    assert "totally_unknown_subject" in joined


def test_invalid_uncontrolled_subject_names_feast() -> None:
    """The error message names the offending feast."""
    with pytest.raises(FeastValidationError) as exc_info:
        validate_feasts(FIXTURE_DIR / "invalid_uncontrolled_subject.yaml", VALID_SUBJECTS)
    joined = " ".join(exc_info.value.errors)
    assert "Feast With Unknown Subject" in joined


def test_invalid_bad_precedence() -> None:
    """An unknown precedence literal is rejected."""
    with pytest.raises(FeastValidationError) as exc_info:
        validate_feasts(FIXTURE_DIR / "invalid_bad_precedence.yaml", VALID_SUBJECTS)
    joined = " ".join(exc_info.value.errors)
    assert "precedence" in joined or "super_important" in joined


def test_invalid_malformed_yaml() -> None:
    """A YAML parse error raises FeastValidationError with a parse-error message."""
    with pytest.raises(FeastValidationError) as exc_info:
        validate_feasts(FIXTURE_DIR / "invalid_malformed_yaml.yaml", VALID_SUBJECTS)
    joined = " ".join(exc_info.value.errors)
    assert "YAML" in joined or "parse" in joined.lower()


# ---------------------------------------------------------------------------
# Invalid subjects fixture
# ---------------------------------------------------------------------------


def test_invalid_duplicate_subjects() -> None:
    """A subjects file with a duplicated subject entry is rejected."""
    with pytest.raises(FeastValidationError) as exc_info:
        validate_feasts(VALID_FEASTS, FIXTURE_DIR / "invalid_duplicate_subjects.yaml")
    joined = " ".join(exc_info.value.errors)
    assert "duplicate" in joined.lower()
    assert "theotokos" in joined


# ---------------------------------------------------------------------------
# Error collection: all errors reported, not just the first
# ---------------------------------------------------------------------------


def test_multiple_errors_collected(tmp_path: Path) -> None:
    """validate_feasts collects all errors before raising, not just the first."""
    feasts_yaml = tmp_path / "feasts.yaml"
    feasts_yaml.write_text(
        "- primary_name: Feast A\n"
        "  tradition: made_up\n"
        "  calendar_type: fixed\n"
        "  date_rule: 'not-a-date'\n"
        "  precedence: principal_feast\n"
        "  source: bcp_1979\n"
        "\n"
        "- primary_name: Feast B\n"
        "  tradition: anglican\n"
        "  calendar_type: fixed\n"
        "  date_rule: '01-06'\n"
        "  precedence: also_made_up\n"
        "  source: bcp_1979\n"
    )
    with pytest.raises(FeastValidationError) as exc_info:
        validate_feasts(feasts_yaml, VALID_SUBJECTS)
    # Should have at least 2 errors (one per bad feast)
    assert len(exc_info.value.errors) >= 2


# ---------------------------------------------------------------------------
# Date-rule edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "date_rule",
    [
        "01-01",
        "12-31",
        "08-15",
        "easter+0",
        "easter+49",
        "easter-46",
        "easter+1",
    ],
)
def test_valid_date_rules(tmp_path: Path, date_rule: str) -> None:
    """Known-good date_rule values pass validation."""
    feasts_yaml = tmp_path / "feasts.yaml"
    feasts_yaml.write_text(
        f"- primary_name: Test\n"
        f"  tradition: anglican\n"
        f"  calendar_type: fixed\n"
        f"  date_rule: '{date_rule}'\n"
        f"  precedence: lesser_commemoration\n"
        f"  source: lff_2024\n"
    )
    entries = validate_feasts(feasts_yaml, VALID_SUBJECTS)
    assert entries[0].date_rule == date_rule


@pytest.mark.parametrize(
    "date_rule",
    [
        "January 6",
        "6-1",
        "01-1",
        "easter",
        "easter0",
        "Easter+0",
        "easter +-1",
    ],
)
def test_invalid_date_rules(tmp_path: Path, date_rule: str) -> None:
    """Known-bad date_rule values are rejected."""
    feasts_yaml = tmp_path / "feasts.yaml"
    feasts_yaml.write_text(
        f"- primary_name: Test\n"
        f"  tradition: anglican\n"
        f"  calendar_type: fixed\n"
        f"  date_rule: '{date_rule}'\n"
        f"  precedence: lesser_commemoration\n"
        f"  source: lff_2024\n"
    )
    with pytest.raises(FeastValidationError):
        validate_feasts(feasts_yaml, VALID_SUBJECTS)
