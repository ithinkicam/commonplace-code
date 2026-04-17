"""Pydantic v2 schema and validator for feasts.yaml and theological_subjects.yaml.

Usage::

    from commonplace_db.feast_schema import validate_feasts
    entries = validate_feasts(
        feasts_path=Path("commonplace_db/seed/feasts.yaml"),
        subjects_path=Path("commonplace_db/seed/theological_subjects.yaml"),
    )

Raises ``FeastValidationError`` (subclass of ``ValueError``) with all collected
errors if validation fails.  Returns a list of ``FeastEntry`` on success.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

_DATE_RULE_RE = re.compile(r"^(\d{2}-\d{2}|easter[+-]\d+)$")
_SUBJECT_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class FeastValidationError(ValueError):
    """Raised when one or more feast/subject entries fail validation.

    Attributes
    ----------
    errors:
        List of human-readable error strings, one per failure.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        bullet_list = "\n  - ".join(errors)
        super().__init__(f"Feast validation failed with {len(errors)} error(s):\n  - {bullet_list}")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SubjectDef(BaseModel):
    """One entry in theological_subjects.yaml."""

    subject: Annotated[
        str,
        Field(description="Lowercase snake_case identifier for the theological subject."),
    ]
    definition: str | None = None

    @field_validator("subject")
    @classmethod
    def _subject_slug(cls, v: str) -> str:
        if not _SUBJECT_SLUG_RE.match(v):
            raise ValueError(
                f"subject {v!r} must be lowercase_snake_case (a-z, 0-9, underscore, "
                "must start with a letter)"
            )
        return v


class TheologicalSubjectsFile(BaseModel):
    """Top-level model for theological_subjects.yaml.

    The YAML file is a list of SubjectDef mappings::

        - subject: theotokos
          definition: "The God-bearer; title of the Virgin Mary..."
        - subject: kenosis
    """

    subjects: list[SubjectDef]


class FeastEntry(BaseModel):
    """One entry in feasts.yaml."""

    primary_name: str
    alternate_names: list[str] = Field(default_factory=list)
    tradition: Literal["anglican", "byzantine", "roman", "shared"]
    calendar_type: Literal["fixed", "movable", "commemoration"]
    date_rule: str
    precedence: Literal["principal_feast", "holy_day", "lesser_commemoration", "ferial"]
    theological_subjects: list[str] = Field(default_factory=list)
    cross_tradition_equivalent: str | None = None

    @field_validator("date_rule")
    @classmethod
    def _validate_date_rule(cls, v: str) -> str:
        if not _DATE_RULE_RE.match(v):
            raise ValueError(
                f"date_rule {v!r} must be 'MM-DD' (e.g. '08-15') "
                "or 'easter[+-]<n>' (e.g. 'easter+49', 'easter-46')"
            )
        return v


# ---------------------------------------------------------------------------
# Loader helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> object:
    """Load a YAML file, wrapping parse errors into FeastValidationError."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise FeastValidationError([f"YAML parse error in {path}: {exc}"]) from exc


def _parse_subjects_file(raw: object, path: Path) -> tuple[TheologicalSubjectsFile, set[str]]:
    """Parse the raw YAML object into a TheologicalSubjectsFile.

    Returns the parsed model and the controlled-vocabulary set.
    Raises FeastValidationError for structural problems or duplicate subjects.
    """
    errors: list[str] = []

    if raw is None:
        raw = []

    if not isinstance(raw, list):
        raise FeastValidationError(
            [f"{path}: expected a YAML list at top level, got {type(raw).__name__}"]
        )

    subject_defs: list[SubjectDef] = []
    seen: dict[str, int] = {}  # subject -> first index

    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"{path} entry[{i}]: expected a mapping, got {type(item).__name__}")
            continue
        try:
            sd = SubjectDef.model_validate(item)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path} entry[{i}]: {exc}")
            continue

        if sd.subject in seen:
            errors.append(
                f"{path}: duplicate subject {sd.subject!r} "
                f"(first at index {seen[sd.subject]}, again at index {i})"
            )
        else:
            seen[sd.subject] = i
            subject_defs.append(sd)

    if errors:
        raise FeastValidationError(errors)

    model = TheologicalSubjectsFile(subjects=subject_defs)
    controlled: set[str] = {sd.subject for sd in subject_defs}
    return model, controlled


def _parse_feasts_file(
    raw: object,
    path: Path,
    controlled: set[str],
) -> list[FeastEntry]:
    """Parse the raw YAML object into a list of FeastEntry.

    Validates each entry against FeastEntry and checks theological_subjects
    against the controlled-vocabulary set (or ``_other:`` escape hatch).

    Collects ALL errors before raising.
    """
    errors: list[str] = []

    if raw is None:
        return []

    if not isinstance(raw, list):
        raise FeastValidationError(
            [f"{path}: expected a YAML list at top level, got {type(raw).__name__}"]
        )

    entries: list[FeastEntry] = []

    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"{path} entry[{i}]: expected a mapping, got {type(item).__name__}")
            continue

        # Use primary_name for error messages if available
        display_name = item.get("primary_name", f"entry[{i}]")

        try:
            entry = FeastEntry.model_validate(item)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path} feast {display_name!r}: {exc}")
            continue

        # Validate theological_subjects against controlled vocab + _other: escape hatch
        for subj in entry.theological_subjects:
            if subj in controlled:
                continue  # in controlled set — OK
            if subj.startswith("_other:"):
                continue  # escape hatch — OK
            errors.append(
                f"{path} feast {entry.primary_name!r}: "
                f"theological subject {subj!r} is not in the controlled vocabulary "
                "and does not use the '_other:<freeform>' escape hatch"
            )

        entries.append(entry)

    if errors:
        raise FeastValidationError(errors)

    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_feasts(
    feasts_path: Path,
    subjects_path: Path,
) -> list[FeastEntry]:
    """Load, parse, and validate feasts.yaml against theological_subjects.yaml.

    Parameters
    ----------
    feasts_path:
        Path to the feasts YAML seed file.
    subjects_path:
        Path to the theological_subjects YAML controlled-vocabulary file.

    Returns
    -------
    list[FeastEntry]
        Validated feast entries.  Empty list if feasts_path is an empty file.

    Raises
    ------
    FeastValidationError
        If any entry fails validation.  The exception's ``.errors`` attribute
        lists all failures so callers can report them in bulk.
    """
    subjects_raw = _load_yaml(subjects_path)
    _subjects_model, controlled = _parse_subjects_file(subjects_raw, subjects_path)

    feasts_raw = _load_yaml(feasts_path)
    return _parse_feasts_file(feasts_raw, feasts_path, controlled)
