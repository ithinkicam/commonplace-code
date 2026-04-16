"""Offline tests for the regenerate_profile skill.

Does NOT invoke claude -p — that is the smoke script's job.
Covers:
  - File structure + SKILL.md content (frontmatter, model pin, sections)
  - Fixture integrity (required top-level keys, inbox_additions shape,
    corpus_sample shape, cold-start + directive-heavy coverage)
  - Parser correctness: round-trip, structural rejects, preamble leak,
    tag enforcement, directive extraction, directive preservation,
    section ordering, token budget.

At least 8 test methods as required by the task contract.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "regenerate_profile"
SKILL_MD = SKILL_DIR / "SKILL.md"
README = SKILL_DIR / "README.md"
FIXTURE_DIR = SKILL_DIR / "fixtures"
PARSER_PATH = SKILL_DIR / "parser.py"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke_regenerate_profile.sh"


def _load_parser_module():
    """Load the regenerate_profile parser under a unique module name.

    Using ``importlib.util.spec_from_file_location`` rather than
    ``sys.path.insert`` + ``from parser import ...`` avoids a name clash with
    other skills' ``parser.py`` modules when the full test suite runs.
    """
    spec = importlib.util.spec_from_file_location(
        "regenerate_profile_parser", PARSER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["regenerate_profile_parser"] = module
    spec.loader.exec_module(module)
    return module


_parser = _load_parser_module()
MAX_TOKENS = _parser.MAX_TOKENS
SECTION_TITLES = _parser.SECTION_TITLES
ParseError = _parser.ParseError
Profile = _parser.Profile
approximate_token_count = _parser.approximate_token_count
extract_directives = _parser.extract_directives
parse = _parser.parse
verify_directives_preserved = _parser.verify_directives_preserved

GOOD_OUTPUT = """# Profile — updated 2026-04-15

## How to talk to me

- Skip the disclaimers and openings; get to the argument. [directive, 2026-01-10]
- Push back when you disagree; don't sand the edges off. [directive, 2026-02-04]
- Match her bawdy-pious register; moralizing lands flat. [inferred]

## What I'm sensitive about

- Orthodoxy and transness are not a paradox; don't frame them as one. [directive, 2026-01-10]
- 'Lived experience' as a debate-ender reads as a dodge. [inferred]

## How I think

- Reads fiction as doing theological work the systematic theologians miss. [inferred]
- Drafts the take, then hunts the citation — honest about the order. [inferred]
"""

GOOD_INPUT_PROFILE = """# Profile — updated 2026-03-15

## How to talk to me

- Skip the disclaimers and openings; get to the argument. [directive, 2026-01-10]
- Push back when you disagree; don't sand the edges off. [directive, 2026-02-04]
- She's bawdy-pious by default. [inferred]

## What I'm sensitive about

- Orthodoxy and transness are not a paradox; don't frame them as one. [directive, 2026-01-10]

## How I think

- Reads fiction theologically. [inferred]
"""

PREAMBLE_LEAK = "Here is the regenerated profile:\n\n" + GOOD_OUTPUT


class TestSkillMd:
    def test_skill_md_exists(self) -> None:
        assert SKILL_MD.exists(), f"SKILL.md not found at {SKILL_MD}"

    def test_skill_md_non_empty(self) -> None:
        content = SKILL_MD.read_text()
        assert len(content.strip()) > 500, "SKILL.md appears too short"

    def test_skill_md_has_frontmatter_and_opus_pin(self) -> None:
        content = SKILL_MD.read_text()
        assert content.startswith("---"), "SKILL.md must start with YAML frontmatter"
        assert "name: regenerate_profile" in content
        assert "model: opus" in content, "regenerate_profile must pin model: opus"
        assert "description:" in content

    def test_skill_md_names_required_sections(self) -> None:
        content = SKILL_MD.read_text()
        for token in SECTION_TITLES:
            assert token in content, f"SKILL.md must reference section '{token}'"

    def test_skill_md_has_input_contract(self) -> None:
        content = SKILL_MD.read_text()
        for token in (
            "current_profile",
            "perennials",
            "inbox_additions",
            "corpus_sample",
            "recent_highlights",
            "recent_captures",
            "recent_bluesky",
            "books_engaged",
        ):
            assert token in content, f"SKILL.md missing input-contract key '{token}'"

    def test_skill_md_has_output_contract(self) -> None:
        content = SKILL_MD.read_text()
        assert "[directive, YYYY-MM-DD]" in content
        assert "[inferred]" in content
        assert "500" in content, "SKILL.md must name the ~500-token budget"

    def test_skill_md_has_preamble_guard(self) -> None:
        content = SKILL_MD.read_text()
        assert "first character" in content.lower(), (
            "SKILL.md must include a preamble guard ('first character...')"
        )
        assert "`#`" in content or "'#'" in content, (
            "SKILL.md preamble guard must name the '#' character"
        )

    def test_skill_md_says_directives_are_sacred(self) -> None:
        content = SKILL_MD.read_text().lower()
        # We don't require the exact phrase, but the skill must clearly tell the
        # model never to edit directives.
        assert "verbatim" in content
        assert "directive" in content

    def test_readme_exists_and_names_opus(self) -> None:
        assert README.exists()
        readme = README.read_text()
        assert "opus" in readme.lower()
        assert "regenerate_profile" in readme


class TestFixtures:
    def _fixtures(self) -> list[Path]:
        assert FIXTURE_DIR.exists(), f"Fixture directory not found: {FIXTURE_DIR}"
        fixtures = sorted(FIXTURE_DIR.glob("*.json"))
        assert len(fixtures) >= 3, f"Expected >=3 fixtures, found {len(fixtures)}"
        return fixtures

    def test_fixture_count(self) -> None:
        assert len(self._fixtures()) >= 3

    def test_fixtures_are_valid_json_with_required_keys(self) -> None:
        for fx in self._fixtures():
            with open(fx) as f:
                data = json.load(f)
            for key in ("current_profile", "perennials", "inbox_additions", "corpus_sample"):
                assert key in data, f"{fx.name}: missing required key '{key}'"
            assert isinstance(data["current_profile"], str)
            assert isinstance(data["perennials"], str) and data["perennials"]
            assert isinstance(data["inbox_additions"], list)
            assert isinstance(data["corpus_sample"], dict)
            for sub in (
                "recent_highlights",
                "recent_captures",
                "recent_bluesky",
                "books_engaged",
            ):
                assert sub in data["corpus_sample"], (
                    f"{fx.name}: corpus_sample missing '{sub}'"
                )
                assert isinstance(data["corpus_sample"][sub], list)

    def test_inbox_additions_have_timestamp_and_content(self) -> None:
        for fx in self._fixtures():
            with open(fx) as f:
                data = json.load(f)
            for i, entry in enumerate(data["inbox_additions"]):
                assert isinstance(entry, dict), f"{fx.name}[{i}]: entry must be dict"
                assert "timestamp" in entry and entry["timestamp"], (
                    f"{fx.name}[{i}]: missing timestamp"
                )
                assert "content" in entry and entry["content"], (
                    f"{fx.name}[{i}]: missing content"
                )

    def test_at_least_one_cold_start_fixture(self) -> None:
        """At least one fixture must exercise the empty-current_profile path."""
        has_cold = False
        for fx in self._fixtures():
            with open(fx) as f:
                data = json.load(f)
            if not data["current_profile"].strip():
                has_cold = True
                break
        assert has_cold, (
            "No cold-start fixture found. At least one fixture must have an "
            "empty current_profile to exercise the cold-start branch."
        )

    def test_at_least_one_directive_heavy_fixture(self) -> None:
        """At least one fixture must have >=3 directives in current_profile."""
        has_heavy = False
        for fx in self._fixtures():
            with open(fx) as f:
                data = json.load(f)
            directives = extract_directives(data["current_profile"])
            if len(directives) >= 3:
                has_heavy = True
                break
        assert has_heavy, (
            "No directive-heavy fixture found. At least one fixture must have "
            ">=3 directives in current_profile to exercise the preservation path."
        )

    def test_fixtures_current_profile_parses_when_nonempty(self) -> None:
        """Whenever a fixture supplies a current_profile, that input must itself
        be a well-formed profile (otherwise the fixture is broken). Cold-start
        fixtures skip this check."""
        for fx in self._fixtures():
            with open(fx) as f:
                data = json.load(f)
            cp = data["current_profile"]
            if cp.strip():
                # Should parse cleanly.
                parse(cp)


class TestParserRoundTrip:
    def test_parse_known_good_output(self) -> None:
        profile = parse(GOOD_OUTPUT)
        assert isinstance(profile, Profile)
        assert profile.updated_date == "2026-04-15"
        assert len(profile.sections) == 3
        titles = [s.title for s in profile.sections]
        assert titles == list(SECTION_TITLES)
        # 3 + 2 + 2 items
        assert [len(s.items) for s in profile.sections] == [3, 2, 2]
        # Tag counts
        assert len(profile.directives()) == 3
        assert len(profile.inferred()) == 4

    def test_parse_cold_start_minimal(self) -> None:
        """One section, one inferred item, no directives."""
        minimal = (
            "# Profile — updated 2026-04-15\n\n"
            "## How I think\n\n"
            "- Weil's attention is the operative concept. [inferred]\n"
        )
        p = parse(minimal)
        assert len(p.sections) == 1
        assert p.sections[0].title == "How I think"
        assert p.directives() == []
        assert len(p.inferred()) == 1

    def test_parse_directive_item_keeps_date(self) -> None:
        p = parse(GOOD_OUTPUT)
        for item in p.directives():
            assert item.directive_date is not None
            assert len(item.directive_date) == 10  # YYYY-MM-DD

    def test_extract_directives_from_output(self) -> None:
        ds = extract_directives(GOOD_OUTPUT)
        assert len(ds) == 3
        for line in ds:
            assert line.startswith("- ")
            assert "[directive," in line

    def test_verify_directives_preserved_ok(self) -> None:
        missing = verify_directives_preserved(GOOD_INPUT_PROFILE, GOOD_OUTPUT)
        assert missing == []

    def test_verify_directives_preserved_flags_drop(self) -> None:
        # Remove one directive from the output.
        mangled = GOOD_OUTPUT.replace(
            "- Push back when you disagree; don't sand the edges off. [directive, 2026-02-04]\n",
            "",
        )
        missing = verify_directives_preserved(GOOD_INPUT_PROFILE, mangled)
        assert len(missing) == 1
        assert "Push back" in missing[0]

    def test_verify_directives_preserved_flags_mutation(self) -> None:
        # Change wording of a directive — treated as a drop (original missing).
        mangled = GOOD_OUTPUT.replace(
            "Skip the disclaimers and openings",
            "Skip the disclaimers and the openings",
        )
        missing = verify_directives_preserved(GOOD_INPUT_PROFILE, mangled)
        assert len(missing) == 1

    def test_verify_directives_preserved_flags_date_change(self) -> None:
        mangled = GOOD_OUTPUT.replace("[directive, 2026-01-10]", "[directive, 2026-04-15]")
        missing = verify_directives_preserved(GOOD_INPUT_PROFILE, mangled)
        # Two directives dated 2026-01-10 in the input; both no longer match.
        assert len(missing) == 2


class TestParserRejections:
    def test_rejects_empty(self) -> None:
        try:
            parse("")
        except ParseError:
            return
        raise AssertionError("ParseError expected for empty input")

    def test_rejects_preamble_leak(self) -> None:
        try:
            parse(PREAMBLE_LEAK)
        except ParseError as e:
            assert "#" in str(e) or "preamble" in str(e).lower()
            return
        raise AssertionError("ParseError expected for preamble leak")

    def test_rejects_missing_h1(self) -> None:
        bad = "## How to talk to me\n\n- A thing. [inferred]\n"
        try:
            parse(bad)
        except ParseError as e:
            assert "H1" in str(e) or "#" in str(e)
            return
        raise AssertionError("ParseError expected for missing H1")

    def test_rejects_malformed_h1(self) -> None:
        bad = "# Profile\n\n## How to talk to me\n\n- A thing. [inferred]\n"
        try:
            parse(bad)
        except ParseError as e:
            assert "H1" in str(e)
            return
        raise AssertionError("ParseError expected for malformed H1")

    def test_rejects_unknown_section(self) -> None:
        bad = (
            "# Profile — updated 2026-04-15\n\n"
            "## Random thoughts\n\n"
            "- Something. [inferred]\n"
        )
        try:
            parse(bad)
        except ParseError as e:
            assert "section" in str(e).lower()
            return
        raise AssertionError("ParseError expected for unknown section title")

    def test_rejects_sections_out_of_order(self) -> None:
        bad = (
            "# Profile — updated 2026-04-15\n\n"
            "## How I think\n\n"
            "- Thing one. [inferred]\n\n"
            "## How to talk to me\n\n"
            "- Thing two. [inferred]\n"
        )
        try:
            parse(bad)
        except ParseError as e:
            assert "order" in str(e).lower()
            return
        raise AssertionError("ParseError expected for out-of-order sections")

    def test_rejects_duplicate_section(self) -> None:
        bad = (
            "# Profile — updated 2026-04-15\n\n"
            "## How to talk to me\n\n"
            "- A thing. [inferred]\n\n"
            "## How to talk to me\n\n"
            "- Another. [inferred]\n"
        )
        try:
            parse(bad)
        except ParseError as e:
            assert "duplicate" in str(e).lower()
            return
        raise AssertionError("ParseError expected for duplicate section")

    def test_rejects_bullet_without_tag(self) -> None:
        bad = (
            "# Profile — updated 2026-04-15\n\n"
            "## How I think\n\n"
            "- A bullet with no tag.\n"
        )
        try:
            parse(bad)
        except ParseError as e:
            assert "tag" in str(e).lower()
            return
        raise AssertionError("ParseError expected for missing tag")

    def test_rejects_bullet_with_both_tags(self) -> None:
        bad = (
            "# Profile — updated 2026-04-15\n\n"
            "## How I think\n\n"
            "- A bullet. [directive, 2026-01-01] [inferred]\n"
        )
        try:
            parse(bad)
        except ParseError as e:
            assert "tag" in str(e).lower() or "both" in str(e).lower()
            return
        raise AssertionError("ParseError expected for double-tagged bullet")

    def test_rejects_sub_bullet(self) -> None:
        bad = (
            "# Profile — updated 2026-04-15\n\n"
            "## How I think\n\n"
            "- Parent. [inferred]\n"
            "  - Sub-bullet that should not exist. [inferred]\n"
        )
        try:
            parse(bad)
        except ParseError as e:
            assert "sub" in str(e).lower() or "bullet" in str(e).lower()
            return
        raise AssertionError("ParseError expected for sub-bullet")

    def test_rejects_numbered_list(self) -> None:
        bad = (
            "# Profile — updated 2026-04-15\n\n"
            "## How I think\n\n"
            "1. Numbered item. [inferred]\n"
        )
        try:
            parse(bad)
        except ParseError as e:
            assert "numbered" in str(e).lower() or "unexpected" in str(e).lower()
            return
        raise AssertionError("ParseError expected for numbered list")

    def test_rejects_empty_section_heading(self) -> None:
        bad = (
            "# Profile — updated 2026-04-15\n\n"
            "## How to talk to me\n\n"
            "## How I think\n\n"
            "- A thing. [inferred]\n"
        )
        try:
            parse(bad)
        except ParseError as e:
            assert "empty" in str(e).lower()
            return
        raise AssertionError("ParseError expected for empty section")

    def test_rejects_oversized_profile(self) -> None:
        # Build a profile that blows through the token budget.
        filler = "x " * 3000  # way over 500 tokens
        bad = (
            "# Profile — updated 2026-04-15\n\n"
            "## How I think\n\n"
            f"- {filler} [inferred]\n"
        )
        try:
            parse(bad)
        except ParseError as e:
            assert "500" in str(e) or "budget" in str(e).lower() or "token" in str(e).lower()
            return
        raise AssertionError("ParseError expected for oversized profile")


class TestTokenCount:
    def test_token_count_empty(self) -> None:
        assert approximate_token_count("") == 0

    def test_token_count_monotonic(self) -> None:
        short = approximate_token_count("hello world")
        longer = approximate_token_count("hello world " * 100)
        assert longer > short

    def test_token_count_bounded_for_good_fixture(self) -> None:
        assert approximate_token_count(GOOD_OUTPUT) <= MAX_TOKENS


class TestExtractDirectives:
    def test_extract_from_empty(self) -> None:
        assert extract_directives("") == []

    def test_extract_only_directives(self) -> None:
        text = (
            "# Profile — updated 2026-04-15\n\n"
            "## How to talk to me\n\n"
            "- A. [directive, 2026-01-10]\n"
            "- B. [inferred]\n"
            "- C. [directive, 2026-02-04]\n"
        )
        ds = extract_directives(text)
        assert len(ds) == 2
        assert all("[directive," in d for d in ds)

    def test_extract_preserves_text_verbatim(self) -> None:
        line = "- Exact text with punctuation! — and an em-dash. [directive, 2026-01-01]"
        text = "# Profile — updated 2026-04-15\n\n## How I think\n\n" + line + "\n"
        ds = extract_directives(text)
        assert ds == [line]


class TestSmokeScript:
    def test_smoke_script_exists(self) -> None:
        assert SMOKE_SCRIPT.exists(), f"Smoke script not found at {SMOKE_SCRIPT}"

    def test_smoke_script_is_executable(self) -> None:
        mode = SMOKE_SCRIPT.stat().st_mode
        assert mode & stat.S_IXUSR, (
            f"{SMOKE_SCRIPT} is not executable (run: chmod +x {SMOKE_SCRIPT})"
        )

    def test_smoke_script_references_skill_and_opus(self) -> None:
        content = SMOKE_SCRIPT.read_text()
        assert "regenerate_profile" in content
        assert "opus" in content
        assert "system-prompt-file" in content


class TestParserModule:
    def test_parser_file_exists(self) -> None:
        assert PARSER_PATH.exists(), f"parser.py not found at {PARSER_PATH}"

    def test_parser_has_no_third_party_imports(self) -> None:
        content = PARSER_PATH.read_text()
        forbidden = (
            "import yaml",
            "from yaml",
            "import pydantic",
            "from pydantic",
            "import frontmatter",
        )
        for token in forbidden:
            assert token not in content, f"parser.py must not depend on {token!r}"

    def test_parser_exposes_public_api(self) -> None:
        # Smoke-check: all the names the README promises exist on the module.
        parser_mod = _parser

        for name in (
            "parse",
            "extract_directives",
            "verify_directives_preserved",
            "approximate_token_count",
            "ParseError",
            "Profile",
            "ProfileItem",
            "SECTION_TITLES",
            "MAX_TOKENS",
        ):
            assert hasattr(parser_mod, name), f"parser.py missing public name '{name}'"

        # And they're the right kinds of objects.
        assert callable(parse)
        assert callable(extract_directives)
        assert callable(verify_directives_preserved)
        assert callable(approximate_token_count)
        assert issubclass(ParseError, ValueError)
