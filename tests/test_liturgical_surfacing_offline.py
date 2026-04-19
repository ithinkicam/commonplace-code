"""Offline structural integrity test for the liturgical surfacing fixture.

Runs in normal `pytest` (no live marker required). Guards against schema
changes to tests/fixtures/liturgical_surfacing.json that would silently break
the live replay in task 4.7.

These tests do NOT call the pipeline. They assert that the fixture is
structurally sound and contains the expected number of cases per category.

Companion to ``test_prose_regression_offline.py`` — mirrors its shape
per task 4.2 forward flag (a).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "liturgical_surfacing.json"

EXPECTED_TOTAL_CASES = 20
EXPECTED_POSITIVE_CASES = 10
EXPECTED_NEGATIVE_TRUE_CASES = 5
EXPECTED_NEGATIVE_SPILLOVER_CASES = 5

REQUIRED_TOP_KEYS = {
    "authored_at",
    "purpose",
    "design_notes",
    "plan_ref",
    "pipeline_version_note",
    "categories",
    "stats",
    "cases",
}

REQUIRED_CASE_KEYS = {
    "id",
    "category",
    "theme",
    "seed",
    "expected_surface",
    "should_surface_prose",
    "notes",
}

REQUIRED_EXPECTED_SURFACE_KEYS = {
    "source_id",
    "kind",
    "tradition",
    "source",
    "reason",
}

VALID_CATEGORIES = {"positive", "negative_true", "negative_spillover"}


@pytest.fixture(scope="module")
def fixture() -> dict:
    assert FIXTURE_PATH.exists(), f"Fixture not found at {FIXTURE_PATH}."
    with open(FIXTURE_PATH) as f:
        data: dict = json.load(f)
    return data


class TestFixtureTopLevel:
    def test_fixture_exists(self) -> None:
        assert FIXTURE_PATH.exists(), f"Fixture not found: {FIXTURE_PATH}"

    def test_fixture_is_valid_json(self) -> None:
        with open(FIXTURE_PATH) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_required_top_level_keys(self, fixture: dict) -> None:
        missing = REQUIRED_TOP_KEYS - set(fixture.keys())
        assert not missing, f"Fixture missing top-level keys: {missing}"

    def test_plan_ref_points_at_q4(self, fixture: dict) -> None:
        ref = fixture["plan_ref"]
        assert "Q4" in ref or "q4" in ref.lower() or "4.2" in ref, (
            f"plan_ref must reference §6 Q4 or task 4.2; got {ref!r}"
        )

    def test_pipeline_version_note_pins_baseline(self, fixture: dict) -> None:
        note = fixture["pipeline_version_note"]
        assert "prose_regression" in note.lower() or "f420d8e" in note, (
            "pipeline_version_note must pin the prose_regression baseline"
        )

    def test_stats_match_actual(self, fixture: dict) -> None:
        stats = fixture["stats"]
        cases = fixture["cases"]
        assert stats["total_cases"] == len(cases) == EXPECTED_TOTAL_CASES
        pos = [c for c in cases if c["category"] == "positive"]
        neg_true = [c for c in cases if c["category"] == "negative_true"]
        neg_spill = [c for c in cases if c["category"] == "negative_spillover"]
        assert len(pos) == stats["positive_cases"] == EXPECTED_POSITIVE_CASES
        assert (
            len(neg_true) == stats["negative_true_cases"] == EXPECTED_NEGATIVE_TRUE_CASES
        )
        assert (
            len(neg_spill) == stats["negative_spillover_cases"]
            == EXPECTED_NEGATIVE_SPILLOVER_CASES
        )

    def test_categories_legend_covers_all_categories(self, fixture: dict) -> None:
        legend = fixture["categories"]
        assert set(legend.keys()) >= VALID_CATEGORIES, (
            f"categories legend missing entries: "
            f"{VALID_CATEGORIES - set(legend.keys())}"
        )


class TestFixtureCases:
    def test_case_ids_unique(self, fixture: dict) -> None:
        ids = [c["id"] for c in fixture["cases"]]
        assert len(ids) == len(set(ids)), f"Duplicate case ids: {ids}"

    def test_case_ids_follow_naming(self, fixture: dict) -> None:
        for case in fixture["cases"]:
            cid = case["id"]
            assert cid.startswith(("lit_pos_", "lit_neg_")), (
                f"Case id {cid!r} doesn't follow lit_pos_/lit_neg_ naming"
            )

    def test_all_cases_have_required_keys(self, fixture: dict) -> None:
        for case in fixture["cases"]:
            missing = REQUIRED_CASE_KEYS - set(case.keys())
            assert not missing, (
                f"Case {case.get('id', '?')} missing keys: {missing}"
            )

    def test_cases_have_valid_category(self, fixture: dict) -> None:
        for case in fixture["cases"]:
            assert case["category"] in VALID_CATEGORIES, (
                f"Case {case['id']}: invalid category {case['category']!r}"
            )

    def test_seeds_have_non_trivial_content(self, fixture: dict) -> None:
        for case in fixture["cases"]:
            assert len(case["seed"].strip()) >= 50, (
                f"Case {case['id']}: seed too short ({len(case['seed'])} chars)"
            )

    def test_should_surface_prose_is_bool(self, fixture: dict) -> None:
        for case in fixture["cases"]:
            assert isinstance(case["should_surface_prose"], bool), (
                f"Case {case['id']}: should_surface_prose must be bool"
            )

    def test_positive_cases_have_expected_surface(self, fixture: dict) -> None:
        for case in fixture["cases"]:
            if case["category"] == "positive":
                assert len(case["expected_surface"]) >= 1, (
                    f"Case {case['id']}: positive case must name >=1 expected unit"
                )

    def test_negative_cases_have_empty_expected(self, fixture: dict) -> None:
        for case in fixture["cases"]:
            if case["category"] in {"negative_true", "negative_spillover"}:
                assert case["expected_surface"] == [], (
                    f"Case {case['id']}: negative cases must have empty "
                    f"expected_surface; got {case['expected_surface']!r}"
                )

    def test_expected_surface_entries_well_formed(self, fixture: dict) -> None:
        for case in fixture["cases"]:
            for i, exp in enumerate(case["expected_surface"]):
                missing = REQUIRED_EXPECTED_SURFACE_KEYS - set(exp.keys())
                assert not missing, (
                    f"Case {case['id']} expected_surface[{i}] missing keys: "
                    f"{missing}"
                )
                assert exp["source"] in {"bcp_1979", "lff_2024"}, (
                    f"Case {case['id']} expected_surface[{i}]: unknown source "
                    f"{exp['source']!r}"
                )
                assert exp["tradition"] in {"anglican", "orthodox", "catholic"}, (
                    f"Case {case['id']} expected_surface[{i}]: invalid tradition "
                    f"{exp['tradition']!r}"
                )


class TestSpilloverTraps:
    """Guard the spillover pairings — these are the heart of §6 Q4."""

    def test_spillover_cases_mark_prose_accept(self, fixture: dict) -> None:
        """Spillover traps must assert prose candidates are still fine.

        The whole point of a spillover trap is that prose candidates should
        surface (e.g., Butler on grief) while liturgical candidates must NOT.
        So should_surface_prose must be True for all negative_spillover cases.
        """
        for case in fixture["cases"]:
            if case["category"] == "negative_spillover":
                assert case["should_surface_prose"] is True, (
                    f"Case {case['id']}: spillover trap must allow prose; "
                    f"should_surface_prose should be True"
                )

    def test_negative_true_cases_also_mark_prose_reject(
        self, fixture: dict
    ) -> None:
        """Pure-technical negatives: prose shouldn't surface either.

        These are paired with seed_18, seed_19, seed_20 from prose_regression,
        which were all reject-path seeds in the baseline.
        """
        for case in fixture["cases"]:
            if case["category"] == "negative_true":
                assert case["should_surface_prose"] is False, (
                    f"Case {case['id']}: negative_true case should not "
                    f"surface prose either; should_surface_prose should be False"
                )
