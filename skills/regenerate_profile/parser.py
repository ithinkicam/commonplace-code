"""Pure-Python parser/validator for regenerate_profile output.

No third-party deps. Used by the offline tests and by the profile-regen handler
(task 4.2) when consuming the skill's output.

Output format (v1):

    # Profile — updated YYYY-MM-DD

    ## How to talk to me

    - <item> [directive, YYYY-MM-DD]
    - <item> [inferred]
    ...

    ## What I'm sensitive about

    - <item> [directive, YYYY-MM-DD]
    - <item> [inferred]
    ...

    ## How I think

    - <item> [inferred]
    - <item> [directive, YYYY-MM-DD]
    ...

- The document starts with the H1 line.
- Sections appear in the canonical order. Any section may be omitted entirely
  (not emitted as an empty heading) when there is nothing to say — cold start.
- Every bullet line ends with exactly one tag: `[directive, YYYY-MM-DD]` or
  `[inferred]`.
- Total length is ≤500 approximate tokens.

The parser intentionally avoids PyYAML and any third-party deps: this output is
plain markdown with a tiny fixed grammar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SECTION_TITLES = (
    "How to talk to me",
    "What I'm sensitive about",
    "How I think",
)
H1_PREFIX = "# Profile"
H1_PATTERN = re.compile(r"^# Profile\s+—\s+updated\s+(\d{4}-\d{2}-\d{2})\s*$")
H2_PATTERN = re.compile(r"^## (.+?)\s*$")
DIRECTIVE_TAG_PATTERN = re.compile(r"\[directive,\s*(\d{4}-\d{2}-\d{2})\]\s*$")
INFERRED_TAG_PATTERN = re.compile(r"\[inferred\]\s*$")
DIRECTIVE_LINE_PATTERN = re.compile(r"^- .+\[directive,\s*\d{4}-\d{2}-\d{2}\]\s*$")
MAX_TOKENS = 500


class ParseError(ValueError):
    """Raised when output does not conform to the spec."""


@dataclass
class ProfileItem:
    text: str  # the line content minus the leading "- " and trailing tag
    tag: str  # "directive" or "inferred"
    directive_date: str | None  # YYYY-MM-DD for directives, else None
    raw_line: str  # full line as it appeared in the source, for byte-for-byte checks


@dataclass
class ProfileSection:
    title: str
    items: list[ProfileItem] = field(default_factory=list)


@dataclass
class Profile:
    updated_date: str  # YYYY-MM-DD from the H1
    sections: list[ProfileSection] = field(default_factory=list)
    token_count_estimate: int = 0

    def section(self, title: str) -> ProfileSection | None:
        for s in self.sections:
            if s.title == title:
                return s
        return None

    def all_items(self) -> list[ProfileItem]:
        out: list[ProfileItem] = []
        for s in self.sections:
            out.extend(s.items)
        return out

    def directives(self) -> list[ProfileItem]:
        return [i for i in self.all_items() if i.tag == "directive"]

    def inferred(self) -> list[ProfileItem]:
        return [i for i in self.all_items() if i.tag == "inferred"]


def approximate_token_count(text: str) -> int:
    """Rough token count.

    We don't ship tiktoken here. Use the conservative heuristic
    ``max(word_count * 1.3, char_count / 4)`` rounded up. This tends to
    over-count slightly — which is what we want for a budget-enforcement
    check: err toward rejecting borderline-too-long output.
    """
    if not text:
        return 0
    words = len(text.split())
    chars = len(text)
    return int(max(words * 1.3, chars / 4) + 0.5)


def _parse_bullet(line: str) -> ProfileItem:
    if not line.startswith("- "):
        raise ParseError(f"bullet must start with '- ': {line!r}")
    body = line[2:].rstrip()
    if not body:
        raise ParseError(f"empty bullet: {line!r}")

    directive_match = DIRECTIVE_TAG_PATTERN.search(body)
    inferred_match = INFERRED_TAG_PATTERN.search(body)

    # Detect accidental double-tagging regardless of which tag is trailing.
    # DIRECTIVE_TAG_PATTERN and INFERRED_TAG_PATTERN are end-anchored, so they
    # only fire on the trailing tag. Look elsewhere in the body for a second
    # tag marker.
    has_any_directive = bool(re.search(r"\[directive,\s*\d{4}-\d{2}-\d{2}\]", body))
    has_any_inferred = bool(re.search(r"\[inferred\]", body))
    if has_any_directive and has_any_inferred:
        raise ParseError(f"bullet has both directive and inferred tags: {line!r}")

    if directive_match and inferred_match:
        raise ParseError(f"bullet has both directive and inferred tags: {line!r}")
    if not directive_match and not inferred_match:
        raise ParseError(
            f"bullet missing required tag (expected '[directive, YYYY-MM-DD]' "
            f"or '[inferred]'): {line!r}"
        )

    if directive_match:
        tag = "directive"
        directive_date = directive_match.group(1)
        text = body[: directive_match.start()].rstrip()
    else:
        tag = "inferred"
        directive_date = None
        assert inferred_match is not None
        text = body[: inferred_match.start()].rstrip()

    if not text:
        raise ParseError(f"bullet has only a tag, no content: {line!r}")

    return ProfileItem(text=text, tag=tag, directive_date=directive_date, raw_line=line)


def parse(output: str) -> Profile:
    """Parse a regenerate_profile output string into a Profile.

    Raises ParseError on any format violation.
    """
    if not output or not output.strip():
        raise ParseError("output is empty")

    # Preamble guard: the very first character must be '#'.
    if output[0] != "#":
        raise ParseError(
            f"output must begin with '#' (H1), got {output[0]!r} "
            f"(possible preamble leak: {output[:80]!r})"
        )

    lines = output.splitlines()

    # H1.
    if not lines:
        raise ParseError("output has no lines")
    h1 = lines[0]
    h1_match = H1_PATTERN.match(h1)
    if not h1_match:
        raise ParseError(
            f"H1 must match '# Profile — updated YYYY-MM-DD', got {h1!r}"
        )
    updated_date = h1_match.group(1)

    profile = Profile(updated_date=updated_date)
    profile.token_count_estimate = approximate_token_count(output)

    if profile.token_count_estimate > MAX_TOKENS:
        raise ParseError(
            f"profile exceeds {MAX_TOKENS} token budget: "
            f"~{profile.token_count_estimate} tokens"
        )

    # Walk remaining lines, splitting into sections at H2 headers.
    i = 1
    seen_titles: list[str] = []
    current_section: ProfileSection | None = None

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Forbid stray H1s or deeper headers.
        if line.startswith("# ") and i != 0:
            raise ParseError(f"unexpected second H1 at line {i + 1}: {line!r}")
        if line.startswith("### ") or line.startswith("#### "):
            raise ParseError(f"unexpected sub-header (only H1 + H2 allowed): {line!r}")

        h2_match = H2_PATTERN.match(line)
        if h2_match and line.startswith("## "):
            title = h2_match.group(1).strip()
            if title not in SECTION_TITLES:
                raise ParseError(
                    f"unexpected section title {title!r}; allowed: "
                    f"{list(SECTION_TITLES)}"
                )
            if title in seen_titles:
                raise ParseError(f"duplicate section {title!r}")
            # Canonical order: each seen title must appear at or after its index
            # in SECTION_TITLES, and in order relative to prior seen titles.
            prior_indices = [SECTION_TITLES.index(t) for t in seen_titles]
            this_index = SECTION_TITLES.index(title)
            if prior_indices and this_index <= max(prior_indices):
                raise ParseError(
                    f"section {title!r} appeared out of canonical order; "
                    f"expected order: {list(SECTION_TITLES)}"
                )
            seen_titles.append(title)
            current_section = ProfileSection(title=title)
            profile.sections.append(current_section)
            i += 1
            continue

        # Must be inside a section.
        if current_section is None:
            raise ParseError(
                f"content before first section header at line {i + 1}: {line!r}"
            )

        if line.startswith("- "):
            current_section.items.append(_parse_bullet(line))
            i += 1
            continue

        # Reject anything else: sub-bullets, numbered lists, quote blocks,
        # bold/italic paragraphs.
        if line.startswith("  -") or line.startswith("\t-"):
            raise ParseError(f"sub-bullets not permitted: {line!r}")
        if re.match(r"^\d+\.\s", line):
            raise ParseError(f"numbered lists not permitted: {line!r}")
        if line.startswith(">"):
            raise ParseError(f"quote blocks not permitted: {line!r}")
        raise ParseError(
            f"unexpected content in section {current_section.title!r}: {line!r}"
        )

    # At least one section required.
    if not profile.sections:
        raise ParseError("profile must contain at least one section")

    # Each section must have at least one bullet.
    for s in profile.sections:
        if not s.items:
            raise ParseError(
                f"section {s.title!r} is empty; omit the section entirely rather "
                f"than emit a heading with no bullets"
            )

    return profile


def extract_directives(profile_markdown: str) -> list[str]:
    """Return every ``[directive, YYYY-MM-DD]`` line from a profile markdown blob.

    Works on both input profiles (may contain a frontmatter-less body in any
    shape) and output profiles. Lines are returned with their trailing
    whitespace stripped but otherwise byte-for-byte as they appeared. Used for
    the "every input directive must appear in output" check.
    """
    if not profile_markdown:
        return []
    out: list[str] = []
    for raw in profile_markdown.splitlines():
        line = raw.rstrip()
        if DIRECTIVE_LINE_PATTERN.match(line):
            out.append(line)
    return out


def verify_directives_preserved(input_profile: str, output_profile: str) -> list[str]:
    """Return a list of directive lines that are in the input but missing from output.

    Empty list == all directives preserved verbatim. Non-empty == the caller
    should reject the regen: the skill dropped or mutated a directive.
    """
    input_directives = extract_directives(input_profile)
    output_directives = set(extract_directives(output_profile))
    missing: list[str] = []
    for d in input_directives:
        if d not in output_directives:
            missing.append(d)
    return missing
