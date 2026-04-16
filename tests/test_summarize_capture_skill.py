"""Offline tests for the summarize_capture skill.

Does NOT invoke claude -p — that is the smoke script's job.
Covers:
  - File structure + SKILL.md content (frontmatter, model pin, sections)
  - Fixture integrity (required keys, length threshold relevance)
  - Parser correctness: round-trip, structural rejects, too-short branch,
    fabricated-quote detection, bullet-count bounds.

At least 8 test methods as required by the task contract.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "summarize_capture"
SKILL_MD = SKILL_DIR / "SKILL.md"
README = SKILL_DIR / "README.md"
FIXTURE_DIR = SKILL_DIR / "fixtures"
PARSER_PATH = SKILL_DIR / "parser.py"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke_summarize_capture.sh"

# Make the skill's parser importable without adding to sys.path permanently.
sys.path.insert(0, str(SKILL_DIR))
from parser import (  # noqa: E402
    DEFAULT_WORD_THRESHOLD,
    CaptureSummary,
    ParseError,
    parse,
    should_summarize,
    verify_quotes,
    word_count,
)

VALID_SOURCE_KINDS = {"article", "podcast", "youtube", "other"}


GOOD_OUTPUT = """---
summary_version: 1
source_kind: article
title: The Quiet Revolution in City Buses
word_count: 2563
---
# Summary
The article argues that bus rapid transit has been quietly transforming American urban transit. It makes the case by contrasting rail romance with bus arithmetic. The argument focuses on frequency, operating funding, and patient institutional work.

## Key points
- BRT costs an order of magnitude less per mile than light rail while delivering comparable ridership in most corridors.
- Frequency, not new vehicles, is what moves ridership numbers on a bus corridor.
- Operating funding is scarcer than capital funding, which is why many BRT corridors underperform after launch.
- Electrification and BRT are often most successful when combined into a single legible project.
- The pandemic surfaced a midday and evening ridership base that peak-hour planning had undercounted for decades.
- Network redesigns in Houston, Columbus, and Dallas reorient service around all-day frequency rather than rush-hour relief.

## Quotes
> The quiet revolution has been, in effect, a slow surrender to that math.
> Frequency is boring. It does not lend itself to ribbon-cuttings.
> The revolution is already here. It just does not look like one.
"""

# A corresponding source text that contains the verbatim quotes above.
GOOD_SOURCE_TEXT = (
    "Filler prefix. "
    "The quiet revolution has been, in effect, a slow surrender to that math. "
    "More filler. "
    "Frequency is boring. It does not lend itself to ribbon-cuttings. "
    "Later. "
    "The revolution is already here. It just does not look like one."
)

TOO_SHORT_OUTPUT = """---
summary_version: 1
source_kind: article
title: Short Thing
word_count: 120
too_short: true
---
"""


class TestSkillMd:
    def test_skill_md_exists(self) -> None:
        assert SKILL_MD.exists(), f"SKILL.md not found at {SKILL_MD}"

    def test_skill_md_has_frontmatter_and_haiku_pin(self) -> None:
        content = SKILL_MD.read_text()
        assert content.startswith("---"), "SKILL.md must start with YAML frontmatter"
        assert "model: haiku" in content, "summarize_capture must pin model: haiku"

    def test_skill_md_names_required_sections(self) -> None:
        content = SKILL_MD.read_text()
        for token in ("# Summary", "## Key points", "## Quotes"):
            assert token in content, f"SKILL.md must reference section '{token}'"

    def test_skill_md_has_preamble_guard(self) -> None:
        content = SKILL_MD.read_text()
        # The "first character must be -" guard (equivalent to "first character must be #"
        # for the book note skills).
        assert "first character" in content.lower(), (
            "SKILL.md must include a preamble guard ('first character...')"
        )

    def test_skill_md_names_too_short_threshold(self) -> None:
        content = SKILL_MD.read_text()
        assert "2000" in content, "SKILL.md must document the ~2000-word threshold"
        assert "too_short" in content, "SKILL.md must document the too_short short-circuit"

    def test_readme_exists(self) -> None:
        assert README.exists()


class TestFixtures:
    def _fixtures(self) -> list[Path]:
        assert FIXTURE_DIR.exists(), f"Fixture directory not found: {FIXTURE_DIR}"
        fixtures = sorted(FIXTURE_DIR.glob("*.json"))
        assert len(fixtures) >= 3, f"Expected >=3 fixtures, found {len(fixtures)}"
        return fixtures

    def test_fixture_count(self) -> None:
        assert len(self._fixtures()) >= 3

    def test_fixtures_span_source_kinds(self) -> None:
        kinds = set()
        for fx in self._fixtures():
            with open(fx) as f:
                kinds.add(json.load(f)["source_kind"])
        # Task spec: article, podcast, youtube.
        assert {"article", "podcast", "youtube"}.issubset(kinds), (
            f"Fixtures must cover article + podcast + youtube, got {kinds}"
        )

    def test_fixtures_have_required_keys_and_length(self) -> None:
        for fx in self._fixtures():
            with open(fx) as f:
                data = json.load(f)
            for key in ("source_kind", "title", "text"):
                assert key in data and data[key], f"{fx.name}: missing/empty '{key}'"
            assert data["source_kind"] in VALID_SOURCE_KINDS, (
                f"{fx.name}: invalid source_kind {data['source_kind']!r}"
            )
            wc = word_count(data["text"])
            assert wc >= DEFAULT_WORD_THRESHOLD, (
                f"{fx.name}: only {wc} words; fixtures must exercise the long-capture path"
            )


class TestParserRoundTrip:
    def test_parse_known_good_output(self) -> None:
        summary = parse(GOOD_OUTPUT)
        assert isinstance(summary, CaptureSummary)
        assert summary.summary_version == 1
        assert summary.source_kind == "article"
        assert summary.title == "The Quiet Revolution in City Buses"
        assert summary.word_count == 2563
        assert summary.too_short is False
        assert len(summary.key_points) == 6
        assert len(summary.quotes) == 3
        assert summary.description.startswith("The article argues")

    def test_parse_too_short_output(self) -> None:
        summary = parse(TOO_SHORT_OUTPUT)
        assert summary.too_short is True
        assert summary.key_points == []
        assert summary.quotes == []
        assert summary.description == ""

    def test_verify_quotes_passes_on_verbatim_matches(self) -> None:
        summary = parse(GOOD_OUTPUT)
        missing = verify_quotes(summary, GOOD_SOURCE_TEXT)
        assert missing == []

    def test_verify_quotes_flags_fabrication(self) -> None:
        summary = parse(GOOD_OUTPUT)
        # Source text that does not contain any of the quoted phrases.
        benign_text = "A completely unrelated source text that contains none of the quoted phrases."
        missing = verify_quotes(summary, benign_text)
        assert len(missing) == 3


class TestParserRejections:
    def test_rejects_missing_frontmatter(self) -> None:
        bad = "# Summary\nhello\n## Key points\n- a\n- b\n- c\n- d\n- e\n## Quotes\n> q1\n> q2"
        try:
            parse(bad)
        except ParseError as e:
            assert "frontmatter" in str(e).lower()
        else:
            raise AssertionError("ParseError expected for missing frontmatter")

    def test_rejects_preamble_leak(self) -> None:
        # Haiku leak: conversational prefix before the '---'.
        leaked = "Here is your summary:\n" + GOOD_OUTPUT
        try:
            parse(leaked)
        except ParseError:
            return
        raise AssertionError("ParseError expected for preamble leak")

    def test_rejects_wrong_header_name(self) -> None:
        bad = GOOD_OUTPUT.replace("# Summary", "# Overview")
        try:
            parse(bad)
        except ParseError as e:
            assert "# Summary" in str(e)
        else:
            raise AssertionError("ParseError expected for wrong header")

    def test_rejects_missing_section(self) -> None:
        # Drop the Quotes section entirely.
        bad = GOOD_OUTPUT.split("## Quotes")[0].rstrip() + "\n"
        try:
            parse(bad)
        except ParseError as e:
            assert "Quotes" in str(e)
        else:
            raise AssertionError("ParseError expected for missing Quotes section")

    def test_rejects_too_few_bullets(self) -> None:
        # Build an output with only 3 bullets.
        bad_body = """---
summary_version: 1
source_kind: podcast
title: T
word_count: 3000
---
# Summary
Short desc here.

## Key points
- one
- two
- three

## Quotes
> q1
> q2
"""
        try:
            parse(bad_body)
        except ParseError as e:
            assert "bullets" in str(e).lower() or "key points" in str(e).lower()
        else:
            raise AssertionError("ParseError expected for too few bullets")

    def test_rejects_too_few_quotes(self) -> None:
        bad_body = """---
summary_version: 1
source_kind: podcast
title: T
word_count: 3000
---
# Summary
Short desc here.

## Key points
- one
- two
- three
- four
- five

## Quotes
> only one quote
"""
        try:
            parse(bad_body)
        except ParseError as e:
            assert "quotes" in str(e).lower()
        else:
            raise AssertionError("ParseError expected for too few quotes")

    def test_rejects_invalid_source_kind(self) -> None:
        bad = GOOD_OUTPUT.replace("source_kind: article", "source_kind: bogus")
        try:
            parse(bad)
        except ParseError as e:
            assert "source_kind" in str(e)
        else:
            raise AssertionError("ParseError expected for invalid source_kind")

    def test_rejects_non_integer_word_count(self) -> None:
        bad = GOOD_OUTPUT.replace("word_count: 2563", "word_count: lots")
        try:
            parse(bad)
        except ParseError as e:
            assert "word_count" in str(e)
        else:
            raise AssertionError("ParseError expected for non-integer word_count")


class TestLengthGate:
    def test_should_summarize_true_above_threshold(self) -> None:
        long_text = "word " * (DEFAULT_WORD_THRESHOLD + 10)
        assert should_summarize(long_text) is True

    def test_should_summarize_false_below_threshold(self) -> None:
        short_text = "word " * 500
        assert should_summarize(short_text) is False

    def test_should_summarize_respects_custom_threshold(self) -> None:
        text = "word " * 100
        assert should_summarize(text, threshold=50) is True
        assert should_summarize(text, threshold=500) is False

    def test_word_count_whitespace_tokenization(self) -> None:
        assert word_count("one two three") == 3
        assert word_count("   one  two\nthree\tfour ") == 4
        assert word_count("") == 0


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
        assert "summarize_capture" in content
        assert "haiku" in content
        assert "system-prompt-file" in content


class TestParserModule:
    def test_parser_file_exists(self) -> None:
        assert PARSER_PATH.exists(), f"parser.py not found at {PARSER_PATH}"

    def test_parser_has_no_third_party_imports(self) -> None:
        content = PARSER_PATH.read_text()
        # Quick sanity: parser must not require yaml/pydantic/etc.
        forbidden = ("import yaml", "from yaml", "import pydantic", "from pydantic")
        for token in forbidden:
            assert token not in content, f"parser.py must not depend on {token!r}"
