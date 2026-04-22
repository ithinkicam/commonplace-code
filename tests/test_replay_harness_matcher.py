"""Offline test for the replay-harness expected-pair matcher.

The replay harness at ``scripts/replay_4_7_review.py`` credits a positive
liturgical case when at least one accepted hit matches an expected_surface
entry on ``kind`` + a loose name check. Phase 4 Wave 4.14 rewrites that
matcher to use token overlap + dash/underscore + bio/prose normalization.

This test isolates the matcher from the DB / retrieval loop by feeding
hand-authored candidate dicts shaped like ``run_surface``'s accepted output
and asserting each of the 10 lit_pos fixture seeds credits its expectation.
Running the real harness costs ~33 min of wall clock; this runs in <0.1s.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).parent.parent
HARNESS_PATH = REPO_ROOT / "scripts" / "replay_4_7_review.py"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "liturgical_surfacing.json"


def _load_harness() -> ModuleType:
    """Load the harness by path so ``scripts/`` doesn't need to be a package."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        "replay_4_7_review_under_test", HARNESS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness() -> ModuleType:
    return _load_harness()


@pytest.fixture(scope="module")
def fixture() -> dict:
    with open(FIXTURE_PATH) as f:
        data: dict = json.load(f)
    return data


# --- Unit tests on helpers ----------------------------------------------------


class TestKindMatches:
    """``_kind_matches`` normalizes fixture/DB impedance mismatches."""

    def test_collect_liturgical_unit_matches(self, harness: ModuleType) -> None:
        hit = {"source_type": "liturgical_unit", "genre": "collect"}
        assert harness._kind_matches(hit, "collect")

    def test_dashed_fixture_kind_matches_underscored_db_genre(
        self, harness: ModuleType
    ) -> None:
        # Fixture: "prayer-body"; DB: "prayer_body" (liturgy_bcp.py:798).
        hit = {"source_type": "liturgical_unit", "genre": "prayer_body"}
        assert harness._kind_matches(hit, "prayer-body")

    def test_bio_matches_prose_source_type(self, harness: ModuleType) -> None:
        # LFF bios are ingested as content_type='prose' with no genre.
        hit = {"source_type": "prose", "genre": None}
        assert harness._kind_matches(hit, "bio")

    def test_bio_does_not_match_liturgical_unit(self, harness: ModuleType) -> None:
        hit = {"source_type": "liturgical_unit", "genre": "collect"}
        assert not harness._kind_matches(hit, "bio")

    def test_non_bio_rejects_prose(self, harness: ModuleType) -> None:
        hit = {"source_type": "prose", "genre": None}
        assert not harness._kind_matches(hit, "collect")

    def test_wrong_genre_rejected(self, harness: ModuleType) -> None:
        hit = {"source_type": "liturgical_unit", "genre": "collect"}
        assert not harness._kind_matches(hit, "prayer")


class TestExpectedSlugTokens:
    def test_strips_tradition_suffix(self, harness: ModuleType) -> None:
        tokens = harness._expected_slug_tokens(
            {"source_id": "julian_of_norwich_anglican"}
        )
        assert tokens == {"julian", "norwich"}

    def test_strips_rite_suffix(self, harness: ModuleType) -> None:
        tokens = harness._expected_slug_tokens(
            {"source_id": "julian_of_norwich_rite-ii"}
        )
        assert tokens == {"julian", "norwich"}

    def test_numeric_leading_zero_collapses(self, harness: ModuleType) -> None:
        tokens = harness._expected_slug_tokens({"source_id": "psalm_023_anglican"})
        assert tokens == {"psalm", "23"}


class TestTitleTokenOverlapMatch:
    def test_short_slug_requires_full_overlap(self, harness: ModuleType) -> None:
        # 'proper_21' vs 'Collect for Proper 15' — should NOT match.
        assert not harness._title_token_overlap_match(
            "Collect for Proper 15", {"proper", "21"}
        )
        # 'proper_21' vs 'Collect for Proper 21' — matches.
        assert harness._title_token_overlap_match(
            "Collect for Proper 21", {"proper", "21"}
        )

    def test_long_slug_allows_partial_overlap(self, harness: ModuleType) -> None:
        # Slug 'ash_wednesday_the_imposition_of_ashes' vs actual display title
        # 'Optional Block (Ash Wednesday)' — the ≥2-overlap threshold credits this.
        slug_tokens = {"ash", "wednesday", "imposition", "ashes"}
        assert harness._title_token_overlap_match(
            "Optional Block (Ash Wednesday)", slug_tokens
        )

    def test_single_shared_token_below_threshold(self, harness: ModuleType) -> None:
        # Overlap of 1 content word is below min(2, 4) threshold.
        assert not harness._title_token_overlap_match(
            "Collect for Ignatius", {"benedict", "nursia", "monastic", "rule"}
        )

    def test_empty_slug_never_matches(self, harness: ModuleType) -> None:
        assert not harness._title_token_overlap_match("Anything", set())


# --- Fixture-level matcher tests ---------------------------------------------


def _hit(
    *,
    cid: int,
    title: str,
    source_type: str,
    genre: str | None = None,
) -> dict:
    """Build a hit dict matching the shape produced inside replay_liturgical_case."""
    return {
        "candidate_id": cid,
        "source_type": source_type,
        "source_title": title,
        "verdict_type": "accepted",
        "reason": "",
        "frame": None,
        "category": None,
        "genre": genre,
        "feast_name": None,
        "tradition": "anglican",
    }


# Hand-authored plausible hits for each lit_pos seed. Titles reflect what
# liturgy_bcp.py / liturgy_lff.py actually emit (verified against handlers).
CANDIDATE_HITS_BY_SEED: dict[str, list[dict]] = {
    "lit_pos_01": [
        _hit(
            cid=101,
            title="Collect for Saint Mary the Virgin",
            source_type="liturgical_unit",
            genre="collect",
        ),
    ],
    "lit_pos_02": [
        _hit(
            cid=201,
            title="The Collect for Proper 21",
            source_type="liturgical_unit",
            genre="collect",
        ),
    ],
    "lit_pos_03": [
        _hit(
            cid=301,
            title="A Prayer of Self-Dedication",
            source_type="liturgical_unit",
            genre="prayer",
        ),
    ],
    "lit_pos_04": [
        # LFF produces a prose bio doc + two rite-specific collect docs.
        _hit(cid=401, title="Julian of Norwich", source_type="prose"),
        _hit(
            cid=402,
            title="Collect for Julian of Norwich",
            source_type="liturgical_unit",
            genre="collect",
        ),
    ],
    "lit_pos_05": [
        _hit(cid=501, title="Martin of Tours", source_type="prose"),
        _hit(
            cid=502,
            title="Collect for Martin of Tours",
            source_type="liturgical_unit",
            genre="collect",
        ),
    ],
    "lit_pos_06": [
        _hit(cid=601, title="Benedict of Nursia", source_type="prose"),
        _hit(
            cid=602,
            title="Collect for Benedict of Nursia",
            source_type="liturgical_unit",
            genre="collect",
        ),
    ],
    "lit_pos_07": [
        # Verbatim from build/4_7_replay_results.json run 3 accepted list.
        # Title is singular ("Sentence") while the expected slug is plural
        # ("opening_sentences"). Genre is "prayer" (not "collect") because the
        # bcp parser emits "prayer" as the generic kind for many "A Collect
        # for X" entries; see liturgy_bcp.py:795–798.
        _hit(
            cid=701,
            title="Opening Sentence (Easter Season)",
            source_type="liturgical_unit",
            genre="seasonal_sentence",
        ),
        _hit(
            cid=702,
            title="A Collect for the Renewal of Life",
            source_type="liturgical_unit",
            genre="prayer",
        ),
    ],
    "lit_pos_08": [
        _hit(
            cid=801,
            title="An Order for Compline",
            source_type="liturgical_unit",
            genre="prayer",
        ),
    ],
    "lit_pos_09": [
        # Ash Wednesday proper liturgy surfaces as an 'Optional Block' display
        # title even though the canonical slug references 'imposition of ashes'.
        _hit(
            cid=901,
            title="Optional Block (Ash Wednesday)",
            source_type="liturgical_unit",
            genre="prayer_body",
        ),
        _hit(
            cid=902,
            title="Collect for Ash Wednesday",
            source_type="liturgical_unit",
            genre="collect",
        ),
    ],
    "lit_pos_10": [
        _hit(
            cid=1001,
            title="Psalm 23 Dominus regit me",
            source_type="liturgical_unit",
            genre="psalm",
        ),
    ],
}


class TestMatchExpectedPairsOnFixture:
    """Every lit_pos seed must credit ≥1 expected pair given plausible hits."""

    def test_all_lit_pos_seeds_credit_at_least_one(
        self, harness: ModuleType, fixture: dict
    ) -> None:
        failures: list[str] = []
        for case in fixture["cases"]:
            if case["category"] != "positive":
                continue
            hits = CANDIDATE_HITS_BY_SEED.get(case["id"])
            assert hits is not None, f"missing hand-authored hits for {case['id']}"
            matched = harness._match_expected_pairs(case["expected_surface"], hits)
            if not matched:
                failures.append(
                    f"{case['id']}: expected={[e['source_id'] for e in case['expected_surface']]}, "
                    f"hits={[(h['source_title'], h.get('genre'), h['source_type']) for h in hits]}"
                )
        assert not failures, "seeds credited 0 pairs:\n" + "\n".join(failures)

    def test_lit_pos_09_credits_optional_block_title(
        self, harness: ModuleType, fixture: dict
    ) -> None:
        """Regression-pin: 'Optional Block (Ash Wednesday)' vs long slug
        'ash_wednesday_the_imposition_of_ashes' failed under substring-match."""
        case = next(c for c in fixture["cases"] if c["id"] == "lit_pos_09")
        matched = harness._match_expected_pairs(
            case["expected_surface"], CANDIDATE_HITS_BY_SEED["lit_pos_09"]
        )
        matched_kinds = {m["expected_kind"] for m in matched}
        assert "prayer-body" in matched_kinds
        assert "collect" in matched_kinds

    def test_lit_pos_04_credits_bio_via_prose_hit(
        self, harness: ModuleType, fixture: dict
    ) -> None:
        """Regression-pin: 'bio' expectations must match prose hits (bios
        bypass liturgical_unit_meta; see liturgy_lff.py:217)."""
        case = next(c for c in fixture["cases"] if c["id"] == "lit_pos_04")
        matched = harness._match_expected_pairs(
            case["expected_surface"], CANDIDATE_HITS_BY_SEED["lit_pos_04"]
        )
        matched_kinds = {m["expected_kind"] for m in matched}
        assert "bio" in matched_kinds
        assert "collect" in matched_kinds

    def test_lit_pos_07_credits_season_suffix_plural_and_prayer_genre(
        self, harness: ModuleType, fixture: dict
    ) -> None:
        """Regression-pin for Wave 4.15:

        Replay run 3 surfaced exactly these two titles with these genres:
          - "Opening Sentence (Easter Season)" / genre=seasonal_sentence
          - "A Collect for the Renewal of Life" / genre=prayer

        Both must credit the lit_pos_07 expectations
        (morning_prayer_rite_ii_opening_sentences / kind=seasonal_sentence and
        a_collect_for_the_renewal_of_life / kind=collect). Failure modes
        fixed here: (a) singular/plural token mismatch ("sentence" vs
        "sentences"); (b) bcp parser emits genre="prayer" for "A Collect for
        X" liturgical units, so matcher must tolerate collect↔prayer when
        the title self-identifies as a collect."""
        case = next(c for c in fixture["cases"] if c["id"] == "lit_pos_07")
        matched = harness._match_expected_pairs(
            case["expected_surface"], CANDIDATE_HITS_BY_SEED["lit_pos_07"]
        )
        matched_kinds = {m["expected_kind"] for m in matched}
        assert "seasonal_sentence" in matched_kinds, (
            "Opening Sentence (Easter Season) must credit the plural-slug "
            "seasonal_sentence expectation"
        )
        assert "collect" in matched_kinds, (
            "A Collect for the Renewal of Life (genre=prayer) must credit "
            "the collect expectation"
        )


class TestMatcherRejectsFalsePositives:
    """Sanity: the matcher should not credit arbitrarily-titled hits."""

    def test_unrelated_collect_does_not_match(self, harness: ModuleType) -> None:
        expected = [{"source_id": "julian_of_norwich_anglican", "kind": "collect"}]
        hits = [
            _hit(
                cid=1,
                title="Collect for Ignatius of Loyola",
                source_type="liturgical_unit",
                genre="collect",
            ),
        ]
        assert harness._match_expected_pairs(expected, hits) == []

    def test_right_title_wrong_kind_does_not_match(self, harness: ModuleType) -> None:
        # 'prayer' expected, but hit has genre='collect'.
        expected = [
            {"source_id": "a_prayer_of_self_dedication_prayer_61_anglican", "kind": "prayer"}
        ]
        hits = [
            _hit(
                cid=1,
                title="A Prayer of Self-Dedication",
                source_type="liturgical_unit",
                genre="collect",
            ),
        ]
        assert harness._match_expected_pairs(expected, hits) == []
