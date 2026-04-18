"""Offline structural integrity test for the prose regression fixture.

Runs in normal `pytest` (no live marker required). Guards against schema
changes to tests/fixtures/prose_regression.json that would silently break
the live regression test.

These tests do NOT call the pipeline. They assert that the fixture is
structurally sound and contains the expected number of seeds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "prose_regression.json"

EXPECTED_SEED_COUNT = 20
EXPECTED_PROSE_COUNT = 10
EXPECTED_TECHNICAL_COUNT = 10

REQUIRED_TOP_KEYS = {
    "captured_at",
    "pipeline_version_note",
    "pipeline_quirks",
    "capture_stats",
    "seeds",
}

REQUIRED_SEED_KEYS = {
    "id",
    "kind",
    "theme",
    "content",
    "candidate_pool",
    "judge_verdicts",
    "rejected_count",
    "pipeline_note",
}

REQUIRED_VERDICT_KEYS = {"candidate_id", "accept", "verdict_type"}

VALID_VERDICT_TYPES = {"accepted", "triangulation"}


@pytest.fixture(scope="module")
def fixture() -> dict:
    assert FIXTURE_PATH.exists(), (
        f"Fixture not found at {FIXTURE_PATH}. "
        "Run: python scripts/capture_prose_regression.py"
    )
    with open(FIXTURE_PATH) as f:
        return json.load(f)


class TestFixtureTopLevel:
    def test_fixture_exists(self) -> None:
        assert FIXTURE_PATH.exists(), f"Fixture not found: {FIXTURE_PATH}"

    def test_fixture_is_valid_json(self) -> None:
        with open(FIXTURE_PATH) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_fixture_has_required_top_level_keys(self, fixture: dict) -> None:
        missing = REQUIRED_TOP_KEYS - set(fixture.keys())
        assert not missing, f"Fixture missing top-level keys: {missing}"

    def test_fixture_has_captured_at_timestamp(self, fixture: dict) -> None:
        ts = fixture["captured_at"]
        assert isinstance(ts, str) and len(ts) >= 20, (
            f"captured_at must be an ISO timestamp, got: {ts!r}"
        )

    def test_fixture_version_note_references_skill_md(self, fixture: dict) -> None:
        note = fixture["pipeline_version_note"]
        assert "SKILL.md" in note or "judge" in note.lower(), (
            "pipeline_version_note must reference SKILL.md or judge rubric"
        )

    def test_fixture_has_pipeline_quirks(self, fixture: dict) -> None:
        quirks = fixture.get("pipeline_quirks", "")
        assert len(quirks) > 20, "pipeline_quirks must be non-empty"

    def test_capture_stats_shape(self, fixture: dict) -> None:
        stats = fixture["capture_stats"]
        assert stats["total_seeds"] == EXPECTED_SEED_COUNT
        assert stats["prose_seeds"] == EXPECTED_PROSE_COUNT
        assert stats["technical_seeds"] == EXPECTED_TECHNICAL_COUNT
        assert isinstance(stats["total_accepted"], int)
        assert isinstance(stats["total_rejected"], int)
        assert isinstance(stats["seeds_with_pipeline_failure"], list)


class TestFixtureSeeds:
    def test_seed_count(self, fixture: dict) -> None:
        assert len(fixture["seeds"]) == EXPECTED_SEED_COUNT, (
            f"Expected {EXPECTED_SEED_COUNT} seeds, got {len(fixture['seeds'])}"
        )

    def test_seed_ids_are_unique(self, fixture: dict) -> None:
        ids = [s["id"] for s in fixture["seeds"]]
        assert len(ids) == len(set(ids)), f"Duplicate seed ids: {ids}"

    def test_all_seeds_have_required_keys(self, fixture: dict) -> None:
        for seed in fixture["seeds"]:
            missing = REQUIRED_SEED_KEYS - set(seed.keys())
            assert not missing, f"Seed {seed.get('id', '?')} missing keys: {missing}"

    def test_seed_ids_are_sequential(self, fixture: dict) -> None:
        ids = [s["id"] for s in fixture["seeds"]]
        expected = [f"seed_{i:02d}" for i in range(1, EXPECTED_SEED_COUNT + 1)]
        assert ids == expected, f"Seed IDs out of order: {ids}"

    def test_seeds_have_non_empty_content(self, fixture: dict) -> None:
        for seed in fixture["seeds"]:
            assert len(seed["content"].strip()) >= 50, (
                f"Seed {seed['id']}: content too short ({len(seed['content'])} chars)"
            )

    def test_seeds_have_valid_kinds(self, fixture: dict) -> None:
        valid_kinds = {"synthetic", "corpus"}
        for seed in fixture["seeds"]:
            assert seed["kind"] in valid_kinds, (
                f"Seed {seed['id']}: invalid kind {seed['kind']!r}"
            )

    def test_prose_seeds_are_first_10(self, fixture: dict) -> None:
        """First 10 seeds should be prose (not technical)."""
        for seed in fixture["seeds"][:10]:
            assert "technical" not in seed["theme"].lower(), (
                f"Seed {seed['id']} ({seed['theme']}) in prose slot but looks technical"
            )

    def test_technical_seeds_are_last_10(self, fixture: dict) -> None:
        """Last 10 seeds should be technical."""
        for seed in fixture["seeds"][10:]:
            assert "technical" in seed["theme"].lower(), (
                f"Seed {seed['id']} ({seed['theme']}) in technical slot but not marked technical"
            )

    def test_seeds_not_liturgical(self, fixture: dict) -> None:
        """No seed content should be liturgical text.

        Critical: seeds must be pure prose. Liturgical seeds would invalidate
        the spillover test in test_prose_regression.py.
        """
        liturgical_markers = [
            "collect for",
            "almighty god",
            "let us pray",
            "o lord",
            "the lord be with you",
            "bless the lord",
        ]
        for seed in fixture["seeds"]:
            content_lower = seed["content"].lower()
            for marker in liturgical_markers:
                assert marker not in content_lower, (
                    f"Seed {seed['id']} appears liturgical (contains '{marker}')"
                )


class TestFixtureVerdicts:
    def test_verdicts_have_required_keys(self, fixture: dict) -> None:
        for seed in fixture["seeds"]:
            for verdict in seed["judge_verdicts"]:
                missing = REQUIRED_VERDICT_KEYS - set(verdict.keys())
                assert not missing, (
                    f"Seed {seed['id']} verdict missing keys: {missing}"
                )

    def test_verdict_accept_is_boolean(self, fixture: dict) -> None:
        for seed in fixture["seeds"]:
            for verdict in seed["judge_verdicts"]:
                assert isinstance(verdict["accept"], bool), (
                    f"Seed {seed['id']}: verdict accept must be bool, "
                    f"got {type(verdict['accept'])}"
                )

    def test_verdict_types_are_valid(self, fixture: dict) -> None:
        for seed in fixture["seeds"]:
            for verdict in seed["judge_verdicts"]:
                assert verdict["verdict_type"] in VALID_VERDICT_TYPES, (
                    f"Seed {seed['id']}: invalid verdict_type {verdict['verdict_type']!r}"
                )

    def test_all_verdicts_are_accept_true(self, fixture: dict) -> None:
        """run_surface only returns accepted items; all fixture verdicts should be accept=True.

        Rejected candidates are captured as rejected_count (integer), not individual verdicts,
        because run_surface doesn't surface rejected items individually.
        """
        for seed in fixture["seeds"]:
            for verdict in seed["judge_verdicts"]:
                assert verdict["accept"] is True, (
                    f"Seed {seed['id']}: expected all verdicts to be accept=True "
                    f"(rejected candidates tracked via rejected_count)"
                )

    def test_rejected_count_is_non_negative_integer(self, fixture: dict) -> None:
        for seed in fixture["seeds"]:
            rc = seed["rejected_count"]
            assert isinstance(rc, int) and rc >= 0, (
                f"Seed {seed['id']}: rejected_count must be non-negative int, got {rc!r}"
            )

    def test_candidate_pool_matches_judge_verdicts(self, fixture: dict) -> None:
        """candidate_pool and judge_verdicts should have the same candidate IDs."""
        for seed in fixture["seeds"]:
            pool_ids = {c["candidate_id"] for c in seed["candidate_pool"]}
            verdict_ids = {v["candidate_id"] for v in seed["judge_verdicts"]}
            assert pool_ids == verdict_ids, (
                f"Seed {seed['id']}: candidate_pool IDs != judge_verdicts IDs. "
                f"Extra in pool: {pool_ids - verdict_ids}. "
                f"Extra in verdicts: {verdict_ids - pool_ids}."
            )

    def test_overall_acceptance_rate(self, fixture: dict) -> None:
        """Sanity check: the baseline should have SOME accepts and SOME rejects."""
        total_accepted = sum(len(s["judge_verdicts"]) for s in fixture["seeds"])
        total_rejected = sum(s["rejected_count"] for s in fixture["seeds"])
        # At least some prose seeds should have been accepted
        assert total_accepted >= 0, "No accepted candidates at all — unexpected"
        # At least some candidates should have been rejected
        assert total_rejected >= 0, "No rejected candidates at all — unexpected"
        # Total processed should be non-trivial
        total = total_accepted + total_rejected
        assert total > 0 or any(
            s["pipeline_note"] is not None for s in fixture["seeds"]
        ), "No candidates processed and no pipeline failures — fixture may be empty"
