"""Pure-Python parser/validator for summarize_capture output.

No third-party deps. Used by the offline tests and by the worker (later tasks
3.4-3.7) when consuming the skill's output.

Output format (v1):

    ---
    summary_version: 1
    source_kind: article | podcast | youtube | other
    title: <single line>
    word_count: <integer>
    [too_short: true]         # only when input was under threshold
    ---
    # Summary
    <one paragraph, 2-4 sentences>

    ## Key points
    - bullet 1
    ...
    - bullet N        # 5 <= N <= 8

    ## Quotes
    > verbatim quote 1
    > verbatim quote 2
    ...              # 2 <= count <= 4

If `too_short: true` is present in the frontmatter, the body MUST be absent.

This module intentionally avoids PyYAML: the frontmatter uses a tiny fixed
subset of YAML (scalars only, no nesting, no lists) so a key:value line parser
is sufficient and avoids a dep.
"""

from __future__ import annotations

from dataclasses import dataclass, field

VALID_SOURCE_KINDS = {"article", "podcast", "youtube", "other"}
MIN_BULLETS = 5
MAX_BULLETS = 8
MIN_QUOTES = 2
MAX_QUOTES = 4
DEFAULT_WORD_THRESHOLD = 2000


class ParseError(ValueError):
    """Raised when output does not conform to the spec."""


@dataclass
class CaptureSummary:
    summary_version: int
    source_kind: str
    title: str
    word_count: int
    too_short: bool = False
    description: str = ""
    key_points: list[str] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)


def word_count(text: str) -> int:
    """Whitespace-tokenized word count. Matches the SKILL.md spec."""
    return len(text.split())


def _split_frontmatter(output: str) -> tuple[str, str]:
    """Return (frontmatter_body, rest). Raises ParseError if malformed."""
    lines = output.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ParseError("output must start with '---' frontmatter delimiter")
    # Find the closing '---' on its own line, starting after line 0.
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_idx = i
            break
    if close_idx is None:
        raise ParseError("frontmatter has no closing '---' delimiter")
    fm = "\n".join(lines[1:close_idx])
    rest = "\n".join(lines[close_idx + 1 :])
    return fm, rest


def _parse_frontmatter(fm: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw in fm.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if ":" not in line:
            raise ParseError(f"frontmatter line missing ':': {line!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            raise ParseError(f"frontmatter line with empty key: {line!r}")
        if key in data:
            raise ParseError(f"duplicate frontmatter key: {key}")
        data[key] = value
    return data


def _require_int(data: dict[str, str], key: str) -> int:
    if key not in data:
        raise ParseError(f"frontmatter missing required key: {key}")
    try:
        return int(data[key])
    except ValueError as e:
        raise ParseError(f"frontmatter key {key!r} must be an integer, got {data[key]!r}") from e


def _require_str(data: dict[str, str], key: str) -> str:
    if key not in data:
        raise ParseError(f"frontmatter missing required key: {key}")
    value = data[key]
    if not value:
        raise ParseError(f"frontmatter key {key!r} must be non-empty")
    return value


def _parse_body(rest: str) -> tuple[str, list[str], list[str]]:
    """Parse the body into (description, key_points, quotes).

    Headers must appear exactly as: "# Summary", "## Key points", "## Quotes"
    in that order.
    """
    lines = rest.splitlines()
    # Strip leading blank lines.
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1

    if i >= len(lines) or lines[i].strip() != "# Summary":
        raise ParseError("body must start with '# Summary' header")
    i += 1

    # Collect description until next header.
    desc_lines: list[str] = []
    while i < len(lines) and not lines[i].startswith("#"):
        desc_lines.append(lines[i])
        i += 1
    description = "\n".join(desc_lines).strip()
    if not description:
        raise ParseError("'# Summary' section is empty")

    if i >= len(lines) or lines[i].strip() != "## Key points":
        raise ParseError("body must contain '## Key points' header after Summary")
    i += 1

    key_points: list[str] = []
    while i < len(lines) and not lines[i].startswith("#"):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("- "):
            key_points.append(stripped[2:].strip())
        elif stripped.startswith("-"):
            # Tolerate "-foo" no-space variant? No — spec says "- ". Fail loudly.
            raise ParseError(f"Key points bullet malformed (expected '- '): {line!r}")
        elif stripped:
            # Non-bullet non-empty content between bullets is not allowed.
            raise ParseError(f"unexpected content in Key points section: {line!r}")
        i += 1

    if not (MIN_BULLETS <= len(key_points) <= MAX_BULLETS):
        raise ParseError(
            f"Key points must have {MIN_BULLETS}-{MAX_BULLETS} bullets, got {len(key_points)}"
        )

    if i >= len(lines) or lines[i].strip() != "## Quotes":
        raise ParseError("body must contain '## Quotes' header after Key points")
    i += 1

    quotes: list[str] = []
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("> "):
            quotes.append(stripped[2:])
        elif stripped.startswith(">"):
            raise ParseError(f"Quote malformed (expected '> '): {line!r}")
        elif stripped.startswith("#"):
            raise ParseError(f"unexpected trailing header after Quotes: {line!r}")
        elif stripped:
            raise ParseError(f"unexpected content in Quotes section: {line!r}")
        i += 1

    if not (MIN_QUOTES <= len(quotes) <= MAX_QUOTES):
        raise ParseError(
            f"Quotes must have {MIN_QUOTES}-{MAX_QUOTES} entries, got {len(quotes)}"
        )

    return description, key_points, quotes


def parse(output: str) -> CaptureSummary:
    """Parse a summarize_capture output string into a CaptureSummary.

    Raises ParseError on any format violation. Does NOT verify quotes against
    an input text; use verify_quotes() for that.
    """
    if not output or not output.strip():
        raise ParseError("output is empty")

    fm_raw, rest = _split_frontmatter(output)
    fm = _parse_frontmatter(fm_raw)

    version = _require_int(fm, "summary_version")
    if version != 1:
        raise ParseError(f"unsupported summary_version: {version}")

    source_kind = _require_str(fm, "source_kind")
    if source_kind not in VALID_SOURCE_KINDS:
        raise ParseError(
            f"source_kind must be one of {sorted(VALID_SOURCE_KINDS)}, got {source_kind!r}"
        )

    title = _require_str(fm, "title")
    wc = _require_int(fm, "word_count")

    too_short = False
    if "too_short" in fm:
        if fm["too_short"].lower() != "true":
            raise ParseError(f"too_short, if present, must be 'true', got {fm['too_short']!r}")
        too_short = True

    if too_short:
        # Body must be absent (only whitespace allowed).
        if rest.strip():
            raise ParseError("too_short=true but body is non-empty")
        return CaptureSummary(
            summary_version=version,
            source_kind=source_kind,
            title=title,
            word_count=wc,
            too_short=True,
        )

    description, key_points, quotes = _parse_body(rest)
    return CaptureSummary(
        summary_version=version,
        source_kind=source_kind,
        title=title,
        word_count=wc,
        too_short=False,
        description=description,
        key_points=key_points,
        quotes=quotes,
    )


def verify_quotes(summary: CaptureSummary, source_text: str) -> list[str]:
    """Return a list of quotes that are NOT verbatim substrings of source_text.

    Empty list == all quotes check out. Non-empty == potential fabrication.
    """
    missing: list[str] = []
    for q in summary.quotes:
        if q and q not in source_text:
            missing.append(q)
    return missing


def should_summarize(text: str, threshold: int = DEFAULT_WORD_THRESHOLD) -> bool:
    """Gate: return True iff text is long enough to warrant summarization."""
    return word_count(text) >= threshold
