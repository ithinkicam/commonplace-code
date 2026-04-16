"""Offline tests for the judge_serendipity skill.

Does NOT invoke claude -p — that is the smoke script's job.
Covers:
  - File structure + SKILL.md content (frontmatter, haiku pin, sections)
  - Fixture integrity (required keys, mode values, candidate shapes)
  - Parser correctness: round-trip, cap enforcement, coverage, preamble guard,
    word caps, triangulation group sizing, duplicate-id detection.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "judge_serendipity"
SKILL_MD = SKILL_DIR / "SKILL.md"
README = SKILL_DIR / "README.md"
FIXTURE_DIR = SKILL_DIR / "fixtures"
PARSER_PATH = SKILL_DIR / "parser.py"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke_judge_serendipity.sh"

# Load the skill's parser under a unique module name to avoid clashing with
# other skills' parser.py files (summarize_capture, regenerate_profile) that
# also live on sys.path during pytest collection.
_spec = importlib.util.spec_from_file_location(
    "judge_serendipity_parser", PARSER_PATH
)
assert _spec is not None and _spec.loader is not None
_parser_mod = importlib.util.module_from_spec(_spec)
sys.modules["judge_serendipity_parser"] = _parser_mod
_spec.loader.exec_module(_parser_mod)

MAX_TOTAL_SURFACED = _parser_mod.MAX_TOTAL_SURFACED
Judgment = _parser_mod.Judgment
ParseError = _parser_mod.ParseError
parse = _parser_mod.parse
strip_code_fences = _parser_mod.strip_code_fences
validate_reject_reason_prefix = _parser_mod.validate_reject_reason_prefix

VALID_MODES = {"ambient", "on_demand"}
VALID_SOURCE_TYPES = {"book", "highlight", "capture", "bluesky", "journal"}
FIXTURE_REQUIRED_KEYS = {"seed", "mode", "candidates", "accumulated_directives"}
CANDIDATE_REQUIRED_KEYS = {
    "id",
    "source_type",
    "source_title",
    "text",
    "similarity_score",
}


# A known-good judgment for a fixture with three candidate ids.
GOOD_OUTPUT_ACCEPT_PLUS_REJECTS = """{
  "accepted": [
    {"id": "weil_gravity_and_grace_ch3_p4", "reason": "Weil reframes hiddenness as posture — attention as waiting, the emptied self as address. Puts purchase on the question of what the seeker must become."}
  ],
  "rejected": [
    {"id": "devotional_app_daily_reading_2025_02_14", "reason": "low-density: devotional throat-clearing, no claim."},
    {"id": "bluesky_post_2024_09_03_hiddenness", "reason": "on-the-nose: paraphrases the seed."}
  ],
  "triangulation_groups": []
}"""

# A known-good triangulation output for the attention fixture.
GOOD_OUTPUT_TRIANGULATION = """{
  "accepted": [],
  "rejected": [
    {"id": "bluesky_post_2024_11_22_attention", "reason": "on-the-nose: restates the seed."},
    {"id": "article_newport_deep_work_hour_1", "reason": "thematic-only: productivity register, different concept."}
  ],
  "triangulation_groups": [
    {"ids": ["weil_waiting_for_god_ch6_p23", "nazianzen_oration_28_theologica_p4", "hadot_philosophy_as_way_of_life_attention"], "reason": "Three traditions on attention as posture: Weil (waiting), Nazianzen (purification), Hadot (prosochē). Each frames attention as disciplined openness rather than focus."}
  ]
}"""

# An empty-accepted, all-rejected output (ambient null case).
GOOD_OUTPUT_ALL_REJECTED = """{
  "accepted": [],
  "rejected": [
    {"id": "highlight_nyssa_life_of_moses_ch2_p31", "reason": "off-topic: technical seed, theological candidate."},
    {"id": "bluesky_post_2024_08_15_code_rot", "reason": "thematic-only: same topic, no new purchase."},
    {"id": "highlight_weil_notebooks_vol2_p91", "reason": "off-topic: different register entirely."}
  ],
  "triangulation_groups": []
}"""


class TestSkillMd:
    def test_skill_md_exists(self) -> None:
        assert SKILL_MD.exists(), f"SKILL.md not found at {SKILL_MD}"

    def test_skill_md_non_empty(self) -> None:
        assert len(SKILL_MD.read_text().strip()) > 500, "SKILL.md appears too short"

    def test_skill_md_has_frontmatter_and_haiku_pin(self) -> None:
        content = SKILL_MD.read_text()
        assert content.startswith("---"), "SKILL.md must start with YAML frontmatter"
        assert "model: haiku" in content, "judge_serendipity must pin model: haiku"

    def test_skill_md_names_required_sections(self) -> None:
        content = SKILL_MD.read_text()
        for token in ("ACCEPT", "REJECT", "TRIANGULATION"):
            assert token in content, f"SKILL.md must reference '{token}' decision category"

    def test_skill_md_documents_both_modes(self) -> None:
        content = SKILL_MD.read_text()
        assert "ambient" in content and "on_demand" in content, (
            "SKILL.md must document both ambient and on_demand modes"
        )

    def test_skill_md_has_preamble_guard(self) -> None:
        content = SKILL_MD.read_text()
        assert "first character" in content.lower(), (
            "SKILL.md must include a preamble guard ('first character...')"
        )
        assert "{" in content, "preamble guard must reference '{' opening character"

    def test_skill_md_documents_cap(self) -> None:
        content = SKILL_MD.read_text()
        # The cap of 2 must be explicit.
        assert "at most 2" in content.lower() or "<= 2" in content, (
            "SKILL.md must document the 2-item cap"
        )

    def test_skill_md_names_output_keys(self) -> None:
        content = SKILL_MD.read_text()
        for key in ("accepted", "rejected", "triangulation_groups"):
            assert key in content, f"SKILL.md must reference output key '{key}'"

    def test_readme_exists_and_references_parser(self) -> None:
        assert README.exists()
        content = README.read_text()
        assert "parser.py" in content
        assert "haiku" in content.lower()


class TestFixtures:
    def _fixtures(self) -> list[Path]:
        assert FIXTURE_DIR.exists(), f"Fixture directory not found: {FIXTURE_DIR}"
        fixtures = sorted(FIXTURE_DIR.glob("*.json"))
        assert len(fixtures) >= 5, f"Expected >=5 fixtures, found {len(fixtures)}"
        return fixtures

    def test_fixture_count(self) -> None:
        assert len(self._fixtures()) >= 5

    def test_fixtures_are_valid_json(self) -> None:
        for fx in self._fixtures():
            with open(fx) as f:
                data = json.load(f)
            assert isinstance(data, dict), f"{fx.name}: must be a JSON object"

    def test_fixtures_have_required_keys(self) -> None:
        for fx in self._fixtures():
            with open(fx) as f:
                data = json.load(f)
            for key in FIXTURE_REQUIRED_KEYS:
                assert key in data, f"{fx.name}: missing required key '{key}'"

    def test_fixtures_have_valid_modes(self) -> None:
        for fx in self._fixtures():
            with open(fx) as f:
                data = json.load(f)
            assert data["mode"] in VALID_MODES, (
                f"{fx.name}: invalid mode {data['mode']!r}"
            )

    def test_fixtures_candidates_well_formed(self) -> None:
        for fx in self._fixtures():
            with open(fx) as f:
                data = json.load(f)
            candidates = data["candidates"]
            assert isinstance(candidates, list), f"{fx.name}: candidates must be list"
            assert len(candidates) >= 2, f"{fx.name}: expected >=2 candidates"
            ids_seen: set[str] = set()
            for i, c in enumerate(candidates):
                for key in CANDIDATE_REQUIRED_KEYS:
                    assert key in c, f"{fx.name} candidate[{i}]: missing {key}"
                assert c["source_type"] in VALID_SOURCE_TYPES, (
                    f"{fx.name} candidate[{i}]: invalid source_type "
                    f"{c['source_type']!r}"
                )
                assert 0.0 <= c["similarity_score"] <= 1.0, (
                    f"{fx.name} candidate[{i}]: similarity_score out of range"
                )
                assert c["id"] not in ids_seen, (
                    f"{fx.name}: duplicate candidate id {c['id']!r}"
                )
                ids_seen.add(c["id"])

    def test_fixtures_cover_both_modes(self) -> None:
        modes: set[str] = set()
        for fx in self._fixtures():
            with open(fx) as f:
                modes.add(json.load(f)["mode"])
        assert modes == VALID_MODES, (
            f"Fixtures must cover both ambient and on_demand, got {modes}"
        )

    def test_fixtures_cover_triangulation_case(self) -> None:
        """At least one fixture must be sized to exercise triangulation (>=3 candidates)."""
        has_triangulation_candidate = False
        for fx in self._fixtures():
            with open(fx) as f:
                data = json.load(f)
            if len(data["candidates"]) >= 3:
                has_triangulation_candidate = True
                break
        assert has_triangulation_candidate, (
            "No fixture with >=3 candidates — cannot exercise triangulation judgments"
        )

    def test_fixtures_exercise_directives(self) -> None:
        """At least one fixture must carry accumulated_directives (non-empty)."""
        has_directives = False
        for fx in self._fixtures():
            with open(fx) as f:
                data = json.load(f)
            if data["accumulated_directives"]:
                has_directives = True
                break
        assert has_directives, (
            "No fixture with non-empty accumulated_directives — must exercise directive path"
        )


class TestParserRoundTrip:
    def test_parse_simple_accept_output(self) -> None:
        j = parse(GOOD_OUTPUT_ACCEPT_PLUS_REJECTS)
        assert isinstance(j, Judgment)
        assert len(j.accepted) == 1
        assert len(j.rejected) == 2
        assert len(j.triangulation_groups) == 0
        assert j.surfaced_count() == 1
        assert j.accepted[0].id == "weil_gravity_and_grace_ch3_p4"

    def test_parse_triangulation_output(self) -> None:
        j = parse(GOOD_OUTPUT_TRIANGULATION)
        assert len(j.accepted) == 0
        assert len(j.triangulation_groups) == 1
        assert len(j.triangulation_groups[0].ids) == 3
        assert j.surfaced_count() == 1

    def test_parse_all_rejected_output(self) -> None:
        j = parse(GOOD_OUTPUT_ALL_REJECTED)
        assert j.surfaced_count() == 0
        assert len(j.rejected) == 3

    def test_parse_with_expected_ids_passes(self) -> None:
        expected = [
            "weil_gravity_and_grace_ch3_p4",
            "devotional_app_daily_reading_2025_02_14",
            "bluesky_post_2024_09_03_hiddenness",
        ]
        j = parse(GOOD_OUTPUT_ACCEPT_PLUS_REJECTS, expected_ids=expected)
        assert set(j.all_ids()) == set(expected)

    def test_parse_with_expected_ids_missing_raises(self) -> None:
        expected = ["weil_gravity_and_grace_ch3_p4", "not_in_output_id"]
        try:
            parse(GOOD_OUTPUT_ACCEPT_PLUS_REJECTS, expected_ids=expected)
        except ParseError as e:
            assert "missing" in str(e).lower()
            return
        raise AssertionError("ParseError expected for missing expected_ids")

    def test_parse_with_expected_ids_extra_raises(self) -> None:
        expected = ["weil_gravity_and_grace_ch3_p4"]
        try:
            parse(GOOD_OUTPUT_ACCEPT_PLUS_REJECTS, expected_ids=expected)
        except ParseError as e:
            assert "unexpected" in str(e).lower() or "not in input" in str(e).lower()
            return
        raise AssertionError("ParseError expected for extra ids not in expected_ids")


class TestParserRejections:
    def test_rejects_preamble_leak(self) -> None:
        leaked = "Here is my judgment:\n" + GOOD_OUTPUT_ACCEPT_PLUS_REJECTS
        try:
            parse(leaked)
        except ParseError as e:
            assert "{" in str(e) or "preamble" in str(e).lower()
            return
        raise AssertionError("ParseError expected for preamble leak")

    def test_rejects_empty_output(self) -> None:
        try:
            parse("")
        except ParseError as e:
            assert "empty" in str(e).lower()
            return
        raise AssertionError("ParseError expected for empty output")

    def test_rejects_invalid_json(self) -> None:
        try:
            parse("{not valid json")
        except ParseError as e:
            assert "json" in str(e).lower()
            return
        raise AssertionError("ParseError expected for invalid JSON")

    def test_rejects_missing_top_level_key(self) -> None:
        bad = '{"accepted": [], "rejected": []}'  # missing triangulation_groups
        try:
            parse(bad)
        except ParseError as e:
            assert "triangulation_groups" in str(e)
            return
        raise AssertionError("ParseError expected for missing top-level key")

    def test_rejects_cap_violation_three_accepted(self) -> None:
        bad = """{
          "accepted": [
            {"id": "a", "reason": "one"},
            {"id": "b", "reason": "two"},
            {"id": "c", "reason": "three"}
          ],
          "rejected": [],
          "triangulation_groups": []
        }"""
        try:
            parse(bad)
        except ParseError as e:
            assert "cap" in str(e).lower()
            return
        raise AssertionError("ParseError expected for cap violation")

    def test_rejects_cap_violation_accepted_plus_triangulation(self) -> None:
        bad = """{
          "accepted": [
            {"id": "a", "reason": "one"},
            {"id": "b", "reason": "two"}
          ],
          "rejected": [],
          "triangulation_groups": [
            {"ids": ["c", "d"], "reason": "triangulate"}
          ]
        }"""
        try:
            parse(bad)
        except ParseError as e:
            assert "cap" in str(e).lower()
            return
        raise AssertionError("ParseError expected when accepted + triangulation > cap")

    def test_rejects_duplicate_id_across_buckets(self) -> None:
        bad = """{
          "accepted": [{"id": "dup", "reason": "yes"}],
          "rejected": [{"id": "dup", "reason": "shallow"}],
          "triangulation_groups": []
        }"""
        try:
            parse(bad)
        except ParseError as e:
            assert "dup" in str(e).lower() or "twice" in str(e).lower()
            return
        raise AssertionError("ParseError expected for duplicate id across buckets")

    def test_rejects_duplicate_id_within_triangulation_group(self) -> None:
        bad = """{
          "accepted": [],
          "rejected": [],
          "triangulation_groups": [
            {"ids": ["x", "x", "y"], "reason": "triangulate"}
          ]
        }"""
        try:
            parse(bad)
        except ParseError as e:
            assert "duplicate" in str(e).lower()
            return
        raise AssertionError("ParseError expected for duplicate id within group")

    def test_rejects_triangulation_group_too_small(self) -> None:
        bad = """{
          "accepted": [],
          "rejected": [],
          "triangulation_groups": [
            {"ids": ["only_one"], "reason": "solo"}
          ]
        }"""
        try:
            parse(bad)
        except ParseError as e:
            assert "2-4" in str(e) or "ids" in str(e)
            return
        raise AssertionError("ParseError expected for undersized triangulation group")

    def test_rejects_triangulation_group_too_large(self) -> None:
        bad = """{
          "accepted": [],
          "rejected": [],
          "triangulation_groups": [
            {"ids": ["a", "b", "c", "d", "e"], "reason": "too many"}
          ]
        }"""
        try:
            parse(bad)
        except ParseError as e:
            assert "2-4" in str(e) or "ids" in str(e)
            return
        raise AssertionError("ParseError expected for oversized triangulation group")

    def test_rejects_accepted_reason_over_30_words(self) -> None:
        long_reason = " ".join(["word"] * 31)
        bad = (
            '{"accepted": [{"id": "a", "reason": "'
            + long_reason
            + '"}], "rejected": [], "triangulation_groups": []}'
        )
        try:
            parse(bad)
        except ParseError as e:
            assert "30" in str(e) or "words" in str(e).lower()
            return
        raise AssertionError("ParseError expected for accepted reason > 30 words")

    def test_rejects_rejected_reason_over_15_words(self) -> None:
        long_reason = " ".join(["word"] * 16)
        bad = (
            '{"accepted": [], "rejected": [{"id": "a", "reason": "'
            + long_reason
            + '"}], "triangulation_groups": []}'
        )
        try:
            parse(bad)
        except ParseError as e:
            assert "15" in str(e) or "words" in str(e).lower()
            return
        raise AssertionError("ParseError expected for rejected reason > 15 words")

    def test_rejects_triangulation_reason_over_30_words(self) -> None:
        long_reason = " ".join(["word"] * 31)
        bad = (
            '{"accepted": [], "rejected": [], "triangulation_groups": ['
            '{"ids": ["a","b"], "reason": "' + long_reason + '"}]}'
        )
        try:
            parse(bad)
        except ParseError as e:
            assert "30" in str(e) or "words" in str(e).lower()
            return
        raise AssertionError("ParseError expected for triangulation reason > 30 words")

    def test_rejects_non_object_output(self) -> None:
        try:
            parse("[]")
        except ParseError as e:
            assert "object" in str(e).lower() or "start" in str(e).lower()
            return
        raise AssertionError("ParseError expected for non-object JSON output")

    def test_rejects_non_string_id(self) -> None:
        bad = '{"accepted": [{"id": 123, "reason": "hi"}], "rejected": [], "triangulation_groups": []}'
        try:
            parse(bad)
        except ParseError as e:
            assert "id" in str(e).lower()
            return
        raise AssertionError("ParseError expected for non-string id")


class TestEmptyAndEdgeCases:
    def test_all_empty_buckets_parses(self) -> None:
        # Degenerate but valid: no candidates at all.
        out = '{"accepted": [], "rejected": [], "triangulation_groups": []}'
        j = parse(out)
        assert j.surfaced_count() == 0
        assert j.all_ids() == []

    def test_cap_boundary_two_accepted(self) -> None:
        out = """{
          "accepted": [
            {"id": "a", "reason": "first"},
            {"id": "b", "reason": "second"}
          ],
          "rejected": [],
          "triangulation_groups": []
        }"""
        j = parse(out)
        assert j.surfaced_count() == MAX_TOTAL_SURFACED

    def test_cap_boundary_one_accepted_one_triangulation(self) -> None:
        out = """{
          "accepted": [{"id": "a", "reason": "one"}],
          "rejected": [],
          "triangulation_groups": [{"ids": ["b","c"], "reason": "triangulate"}]
        }"""
        j = parse(out)
        assert j.surfaced_count() == MAX_TOTAL_SURFACED
        assert len(j.triangulation_groups[0].ids) == 2


class TestStripCodeFences:
    def test_strips_json_fence(self) -> None:
        wrapped = '```json\n{"accepted": [], "rejected": [], "triangulation_groups": []}\n```'
        result = strip_code_fences(wrapped)
        assert result.startswith("{")
        assert result.endswith("}")

    def test_strips_plain_fence(self) -> None:
        wrapped = '```\n{"accepted": [], "rejected": [], "triangulation_groups": []}\n```'
        result = strip_code_fences(wrapped)
        assert result.startswith("{")
        assert result.endswith("}")

    def test_unchanged_when_no_fence(self) -> None:
        clean = '{"accepted": [], "rejected": [], "triangulation_groups": []}'
        assert strip_code_fences(clean) == clean

    def test_round_trip_parse_after_strip(self) -> None:
        wrapped = "```json\n" + GOOD_OUTPUT_ACCEPT_PLUS_REJECTS + "\n```"
        stripped = strip_code_fences(wrapped)
        j = parse(stripped)
        assert j.surfaced_count() == 1


class TestRejectReasonPrefixAdvisory:
    def test_approved_prefix_detected(self) -> None:
        assert validate_reject_reason_prefix("thematic-only: keyword overlap") is True
        assert validate_reject_reason_prefix("on-the-nose paraphrase of seed") is True
        assert validate_reject_reason_prefix("shallow — no claim") is True
        assert validate_reject_reason_prefix("off-topic") is True

    def test_unapproved_prefix_returns_false(self) -> None:
        assert validate_reject_reason_prefix("nah, didn't like it") is False
        assert validate_reject_reason_prefix("meh") is False


class TestSmokeScript:
    def test_smoke_script_exists(self) -> None:
        assert SMOKE_SCRIPT.exists(), f"Smoke script not found at {SMOKE_SCRIPT}"

    def test_smoke_script_is_executable(self) -> None:
        mode = SMOKE_SCRIPT.stat().st_mode
        assert mode & stat.S_IXUSR, (
            f"{SMOKE_SCRIPT} is not executable (run: chmod +x {SMOKE_SCRIPT})"
        )

    def test_smoke_script_references_skill_and_haiku(self) -> None:
        content = SMOKE_SCRIPT.read_text()
        assert "judge_serendipity" in content
        assert "haiku" in content
        assert "system-prompt-file" in content


class TestParserModule:
    def test_parser_file_exists(self) -> None:
        assert PARSER_PATH.exists(), f"parser.py not found at {PARSER_PATH}"

    def test_parser_has_no_third_party_imports(self) -> None:
        content = PARSER_PATH.read_text()
        forbidden = ("import yaml", "from yaml", "import pydantic", "from pydantic")
        for token in forbidden:
            assert token not in content, f"parser.py must not depend on {token!r}"
