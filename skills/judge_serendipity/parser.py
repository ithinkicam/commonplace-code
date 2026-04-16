"""Pure-Python parser/validator for judge_serendipity output.

No third-party deps. Used by the offline tests and by the surface MCP tool
(task 4.5) when consuming the skill's output.

Output format:

    {
      "accepted": [{"id": "...", "reason": "<=30 words"}],
      "rejected": [{"id": "...", "reason": "<=15 words"}],
      "triangulation_groups": [{"ids": ["id1","id2"], "reason": "<=30 words"}]
    }

Constraints enforced:
  - Output is valid JSON, first character must be '{' (preamble guard).
  - All three keys present; values are lists (possibly empty).
  - Each accepted/rejected entry has `id` (non-empty str) and `reason` (str).
  - Each triangulation_groups entry has `ids` (list of 2-4 non-empty strs) and `reason`.
  - Word caps on reasons (30 for accepted/triangulation, 15 for rejected).
  - Cap: len(accepted) + len(triangulation_groups) <= 2.
  - Every candidate id appears exactly once across all three buckets (when caller
    supplies the expected id set).
  - No id appears in more than one bucket.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

MAX_TOTAL_SURFACED = 2
MAX_ACCEPTED_REASON_WORDS = 30
MAX_REJECTED_REASON_WORDS = 15
MAX_TRIANGULATION_REASON_WORDS = 30
MIN_TRIANGULATION_GROUP_SIZE = 2
MAX_TRIANGULATION_GROUP_SIZE = 4

VALID_REJECT_PREFIXES = (
    "thematic-only",
    "on-the-nose",
    "shallow",
    "off-topic",
    "low-density",
    "decontextualized",
)


class ParseError(ValueError):
    """Raised when output does not conform to the spec."""


@dataclass
class AcceptedEntry:
    id: str
    reason: str


@dataclass
class RejectedEntry:
    id: str
    reason: str


@dataclass
class TriangulationGroup:
    ids: list[str]
    reason: str


@dataclass
class Judgment:
    accepted: list[AcceptedEntry] = field(default_factory=list)
    rejected: list[RejectedEntry] = field(default_factory=list)
    triangulation_groups: list[TriangulationGroup] = field(default_factory=list)

    def all_ids(self) -> list[str]:
        """Every id the judgment references, in order (accepted, triangulation, rejected)."""
        ids: list[str] = [e.id for e in self.accepted]
        for g in self.triangulation_groups:
            ids.extend(g.ids)
        ids.extend(e.id for e in self.rejected)
        return ids

    def surfaced_count(self) -> int:
        """Items that would actually be surfaced (caps counted per-group, not per-id)."""
        return len(self.accepted) + len(self.triangulation_groups)


def _word_count(s: str) -> int:
    return len(s.split())


def _require_str(val: object, where: str) -> str:
    if not isinstance(val, str):
        raise ParseError(f"{where}: must be a string, got {type(val).__name__}")
    if not val.strip():
        raise ParseError(f"{where}: must be non-empty")
    return val


def strip_code_fences(output: str) -> str:
    """Strip a leading/trailing markdown code fence pair if present.

    Haiku often wraps JSON responses in ```json ... ``` fences despite prompt
    guidance against it. Consumers (surface MCP tool, smoke tests) call this
    first, then hand the result to `parse`. The strict `parse` function itself
    still requires a clean '{'-first response when fences are absent.

    Only strips ONE level. Returns the input unchanged if no fences found.
    """
    text = output.strip()
    if text.startswith("```"):
        # Split into lines, drop the first fence line and the last fence line.
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def parse(output: str, expected_ids: list[str] | None = None) -> Judgment:
    """Parse a judge_serendipity output string into a Judgment.

    Raises ParseError on any format violation.

    If `expected_ids` is provided, verifies that every expected id appears
    exactly once across the three buckets, and no extra ids are present.

    This is strict: the first non-whitespace character must be '{'. Callers
    who want to tolerate Haiku's markdown fence tic should pass the output
    through `strip_code_fences` first.
    """
    if not output or not output.strip():
        raise ParseError("output is empty")

    # Preamble guard: first non-whitespace character must be '{'.
    stripped = output.lstrip()
    if not stripped.startswith("{"):
        raise ParseError(
            f"output must start with '{{' (preamble leak?): head={output[:80]!r}"
        )

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise ParseError(f"output is not valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise ParseError(f"output must be a JSON object, got {type(data).__name__}")

    for key in ("accepted", "rejected", "triangulation_groups"):
        if key not in data:
            raise ParseError(f"output missing required key: {key!r}")
        if not isinstance(data[key], list):
            raise ParseError(f"{key!r} must be a list, got {type(data[key]).__name__}")

    accepted: list[AcceptedEntry] = []
    for i, entry in enumerate(data["accepted"]):
        if not isinstance(entry, dict):
            raise ParseError(f"accepted[{i}]: must be an object")
        id_ = _require_str(entry.get("id"), f"accepted[{i}].id")
        reason = _require_str(entry.get("reason"), f"accepted[{i}].reason")
        wc = _word_count(reason)
        if wc > MAX_ACCEPTED_REASON_WORDS:
            raise ParseError(
                f"accepted[{i}].reason exceeds {MAX_ACCEPTED_REASON_WORDS} words (got {wc})"
            )
        accepted.append(AcceptedEntry(id=id_, reason=reason))

    rejected: list[RejectedEntry] = []
    for i, entry in enumerate(data["rejected"]):
        if not isinstance(entry, dict):
            raise ParseError(f"rejected[{i}]: must be an object")
        id_ = _require_str(entry.get("id"), f"rejected[{i}].id")
        reason = _require_str(entry.get("reason"), f"rejected[{i}].reason")
        wc = _word_count(reason)
        if wc > MAX_REJECTED_REASON_WORDS:
            raise ParseError(
                f"rejected[{i}].reason exceeds {MAX_REJECTED_REASON_WORDS} words (got {wc})"
            )
        rejected.append(RejectedEntry(id=id_, reason=reason))

    triangulation_groups: list[TriangulationGroup] = []
    for i, entry in enumerate(data["triangulation_groups"]):
        if not isinstance(entry, dict):
            raise ParseError(f"triangulation_groups[{i}]: must be an object")
        ids_raw = entry.get("ids")
        if not isinstance(ids_raw, list):
            raise ParseError(f"triangulation_groups[{i}].ids: must be a list")
        if not (MIN_TRIANGULATION_GROUP_SIZE <= len(ids_raw) <= MAX_TRIANGULATION_GROUP_SIZE):
            raise ParseError(
                f"triangulation_groups[{i}].ids: must have "
                f"{MIN_TRIANGULATION_GROUP_SIZE}-{MAX_TRIANGULATION_GROUP_SIZE} ids, "
                f"got {len(ids_raw)}"
            )
        ids: list[str] = []
        for j, id_val in enumerate(ids_raw):
            ids.append(_require_str(id_val, f"triangulation_groups[{i}].ids[{j}]"))
        if len(set(ids)) != len(ids):
            raise ParseError(
                f"triangulation_groups[{i}].ids: duplicate id within group: {ids}"
            )
        reason = _require_str(entry.get("reason"), f"triangulation_groups[{i}].reason")
        wc = _word_count(reason)
        if wc > MAX_TRIANGULATION_REASON_WORDS:
            raise ParseError(
                f"triangulation_groups[{i}].reason exceeds "
                f"{MAX_TRIANGULATION_REASON_WORDS} words (got {wc})"
            )
        triangulation_groups.append(TriangulationGroup(ids=ids, reason=reason))

    judgment = Judgment(
        accepted=accepted,
        rejected=rejected,
        triangulation_groups=triangulation_groups,
    )

    # Cap enforcement.
    if judgment.surfaced_count() > MAX_TOTAL_SURFACED:
        raise ParseError(
            f"cap exceeded: len(accepted)+len(triangulation_groups)="
            f"{judgment.surfaced_count()} > {MAX_TOTAL_SURFACED}"
        )

    # Duplicate-id check across buckets.
    seen: dict[str, str] = {}
    for entry in judgment.accepted:
        if entry.id in seen:
            raise ParseError(
                f"id {entry.id!r} appears twice: first in {seen[entry.id]}, again in accepted"
            )
        seen[entry.id] = "accepted"
    for gi, group in enumerate(judgment.triangulation_groups):
        for id_ in group.ids:
            if id_ in seen:
                raise ParseError(
                    f"id {id_!r} appears twice: first in {seen[id_]}, "
                    f"again in triangulation_groups[{gi}]"
                )
            seen[id_] = f"triangulation_groups[{gi}]"
    for entry in judgment.rejected:
        if entry.id in seen:
            raise ParseError(
                f"id {entry.id!r} appears twice: first in {seen[entry.id]}, again in rejected"
            )
        seen[entry.id] = "rejected"

    # Coverage check.
    if expected_ids is not None:
        expected_set = set(expected_ids)
        got_set = set(seen.keys())
        missing = expected_set - got_set
        extra = got_set - expected_set
        if missing:
            raise ParseError(f"expected ids missing from judgment: {sorted(missing)}")
        if extra:
            raise ParseError(f"unexpected ids in judgment (not in input): {sorted(extra)}")

    return judgment


def validate_reject_reason_prefix(reason: str) -> bool:
    """Return True iff the reject reason starts with one of the approved category prefixes.

    Advisory — the parser does not fail on this, but smoke tests and iterators
    may warn when the model freelance-phrases reject reasons.
    """
    lowered = reason.strip().lower()
    return any(lowered.startswith(prefix) for prefix in VALID_REJECT_PREFIXES)
