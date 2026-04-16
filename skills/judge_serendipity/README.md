# judge_serendipity

Haiku-pinned skill that decides which candidate passages from vector search make a genuine connective claim on the current conversation topic. Runs on every chat turn that triggers ambient surfacing (high volume, low latency). Rejection is the default.

## When this skill is invoked

The `surface` MCP tool (task 4.5) calls this skill after a vector search over the user's corpus returns a small batch of candidates. The skill makes the second pass of the two-pass filter described in `commonplace-plan-v5.md` §"Serendipity":

1. Vector search returns top ~10 candidates (sqlite-vec, local).
2. Threshold gate: if none pass similarity floor, skip silently.
3. **This skill** judges which survivors actually connect to the seed.
4. Cap: at most 2 surfaces per chat.

## Input contract

JSON object on stdin:

| Field | Required | Notes |
|---|---|---|
| `seed` | yes | 1-3 sentences — the current conversation topic or excerpt |
| `mode` | yes | `ambient` (unsolicited) or `on_demand` (user asked) |
| `candidates` | yes | List of passages that already cleared vector-search threshold |
| `accumulated_directives` | no | Rules accumulated from past user feedback |

Each candidate:

| Field | Notes |
|---|---|
| `id` | Stable identifier (document_id + chunk offset) |
| `source_type` | `book`, `highlight`, `capture`, `bluesky`, or `journal` |
| `source_title` | Book title, article title, etc. |
| `text` | Passage text, <=500 words |
| `similarity_score` | 0.0-1.0 from vector search |
| `last_engaged_days_ago` | Integer or null |

## Output contract

Single JSON object on stdout, no preamble:

```json
{
  "accepted": [
    {"id": "...", "reason": "<=30 words — why this candidate has purchase on the seed"}
  ],
  "rejected": [
    {"id": "...", "reason": "<=15 words — category + optional specifics"}
  ],
  "triangulation_groups": [
    {"ids": ["id1","id2"], "reason": "<=30 words — what these passages triangulate on"}
  ]
}
```

Rules:

- **Cap:** `len(accepted) + len(triangulation_groups) <= 2`. A triangulation group counts as one surface.
- **Coverage:** every candidate id appears exactly once across the three buckets.
- **Ambient mode:** reject aggressively; empty accepted + empty triangulation_groups is often correct.
- **On-demand mode:** more permissive, but still reject the truly shallow.

## Parser

A pure-Python parser/validator lives at `skills/judge_serendipity/parser.py`. No third-party deps. Exposes:

- `parse(output: str, expected_ids: list[str] | None = None) -> Judgment` — parses the skill's stdout; raises `ParseError` on format violations, cap violation, duplicate ids, or coverage mismatch when `expected_ids` is supplied. Strict: first non-whitespace character must be `{`.
- `strip_code_fences(output: str) -> str` — tolerance helper for Haiku's frequent ``` ```json ... ``` ``` wrapping tic. Consumers call this before `parse`. See "Preamble guard" below.
- `validate_reject_reason_prefix(reason) -> bool` — advisory check that reject reasons start with an approved category (thematic-only / on-the-nose / shallow / off-topic / low-density / decontextualized).

Dataclasses: `Judgment`, `AcceptedEntry`, `RejectedEntry`, `TriangulationGroup`.

## Model pin

`haiku`. This runs on every substantive chat turn — latency and cost matter. See `AGENTS.md` model-pins table and `build/pins/claude-code.md` for the invocation shape.

## Invocation

```bash
cat skills/judge_serendipity/fixtures/clear_accept_weil_hiddenness.json \
  | claude -p --system-prompt-file skills/judge_serendipity/SKILL.md --model haiku
```

## Smoke test

```bash
bash scripts/smoke_judge_serendipity.sh
```

Runs every fixture through the skill, parses the output with `parser.py`, and confirms every candidate id is covered exactly once and the cap is respected. Does not grade *which* candidates the model accepted — prompt-quality iteration is Phase 4 task 4.7 territory, and subjective.

## Pytest coverage

```bash
.venv/bin/python -m pytest tests/test_judge_serendipity_skill.py -v
```

Offline tests: round-trip parse, cap enforcement, coverage checks, preamble-leak detection, word-cap enforcement, fixture integrity.

## Preamble guard

The output spec requires the very first character of the response to be `{`. `parser.py` enforces this strictly.

**Observed Haiku behavior (2026-04-15):** despite the SKILL.md guard, Haiku frequently wraps the JSON in ``` ```json ... ``` ``` markdown fences. The JSON *content* is correct — judgments are on-target, caps honored, ids covered — but the fence precedes the `{`. Rather than keep iterating the prompt to fight this, `parser.py` ships a `strip_code_fences(output)` helper. Consumers (smoke script, the `surface` MCP tool in task 4.5) call it first, then `parse`. Conversational preambles ("Here is my judgment:") are still hard fails — only the single fence-wrapper tic is tolerated. See `scripts/smoke_judge_serendipity.sh` for the canonical consumer pattern.

This is a known weakness worth revisiting in task 4.7 (prompt iteration). If a future Haiku release drops the fence habit, tighten the smoke script and delete the tolerance path.

## Directive-based learning

Per `commonplace-plan-v5.md` §"Directive-based learning": when the user reacts to a surface (*"good pull"* / *"shallow, skip that"*), Claude adds a directive to the skill's `accumulated_directives` input. These get folded into the skill file itself during periodic edits. No separate feedback subsystem.

## Known weakness (from v5, line 400)

*"Ambient serendipity requires real prompt iteration before it feels right."* At launch, the judge will misfire. Budget Phase 4 task 4.7 for tuning. If a fixture reveals a decision boundary the prompt can't handle reliably, surface it to the primary rather than rewriting the fixture.

## Related

- Plan v5 serendipity section: `commonplace-plan-v5.md` §"Serendipity" (~lines 218-246)
- `surface` MCP tool (consumer): task 4.5
- Smoke test: `scripts/smoke_judge_serendipity.sh`
- Pytest coverage: `tests/test_judge_serendipity_skill.py`
