# regenerate_profile

Opus-pinned skill that regenerates the tier-3 operational profile (`~/commonplace/profile/current.md`) from the current profile plus profile-inbox additions plus a sample of recent corpus signal. Monthly cron job; low-volume; high-judgment.

## When this skill is invoked

A monthly cron job (handler built in task 4.2, not this task) reads `profile/current.md`, the `profile/inbox/` additions, and a sample of recent corpus signal (highlights, captures, Bluesky posts, books engaged). It shapes them into the JSON input contract below, invokes this skill, snapshots the existing `current.md` to `profile/history/`, and writes the skill's output as the new `current.md`.

On-demand invocation (when the reader asks for a regen in chat) follows the same shape.

## Input contract

JSON object on stdin:

| Field | Required | Notes |
|---|---|---|
| `current_profile` | yes | Full markdown text of current.md, or `""` on first run |
| `perennials` | yes | Full markdown text of perennials.md (context only; never restated in output) |
| `inbox_additions` | yes | List of `{"timestamp": ISO8601, "content": string}`; may be empty |
| `corpus_sample` | yes | Object with `recent_highlights`, `recent_captures`, `recent_bluesky`, `books_engaged` (all lists of strings; any may be empty) |

See `SKILL.md` for the full spec.

## Output contract

Raw Markdown — the full replacement contents of `current.md`. Shape:

```
# Profile — updated YYYY-MM-DD

## How to talk to me

- <item> [directive, YYYY-MM-DD]
- <item> [inferred]

## What I'm sensitive about

- <item> [directive, YYYY-MM-DD]

## How I think

- <item> [inferred]
```

≤500 tokens total. Every `[directive, YYYY-MM-DD]` line from input is preserved byte-for-byte verbatim. Only `[inferred]` items are regenerated.

Preamble guard: the very first character of the response must be `#`. The parser enforces this strictly.

## Parser

A pure-Python parser/validator lives at `skills/regenerate_profile/parser.py`. It has no third-party deps and exposes:

- `parse(output: str) -> Profile` — parses the skill's stdout and raises `ParseError` on any format violation (missing H1, missing/reordered sections, bullets without tags, ≥500 token budget, leading preamble, etc.).
- `extract_directives(profile_markdown: str) -> list[Directive]` — pulls every `[directive, YYYY-MM-DD]` line from a profile string. Used by the smoke script to verify directive preservation: every directive in the input must appear in the output byte-for-byte.
- `verify_directives_preserved(input_profile: str, output_profile: str) -> list[str]` — returns a list of directive lines present in input but missing from output. Empty list == all preserved.
- `approximate_token_count(text: str) -> int` — rough token count used to enforce the ≤500 budget.

## Model pin

`opus`. See `AGENTS.md` model-pins table (regenerate_profile is called out explicitly: "monthly, low-volume, heavy reasoning") and `build/pins/claude-code.md` for the invocation shape.

Opus is appropriate here because:

- Monthly (not per-chat) — token cost is negligible.
- High judgment — reading the corpus sample and the prior profile to write a voice-matched, non-generic update requires real reasoning.
- Voice-matching is hard — Haiku flattens register; Sonnet is usable but Opus is markedly better at preserving the reader's idiom across "How to talk to me" and "How I think."

## Invocation

```bash
cat skills/regenerate_profile/fixtures/update_with_inbox_and_corpus.json \
  | claude -p --system-prompt-file skills/regenerate_profile/SKILL.md --model opus
```

## Smoke test

```bash
bash scripts/smoke_regenerate_profile.sh
```

Runs every fixture in `skills/regenerate_profile/fixtures/` through the skill and verifies:

1. Output begins with `#` (no preamble leak).
2. Output parses via `parser.py` (all required sections present, correct order, token budget respected).
3. Every `[directive, YYYY-MM-DD]` line from the fixture's `current_profile` appears byte-for-byte in the output.

Passes only if all fixtures pass.

## Pytest coverage

```bash
.venv/bin/python -m pytest tests/test_regenerate_profile_skill.py -v
```

Offline tests: round-trip parse, structural rejections (missing H1, wrong section titles, missing tags, bullets without markers, token budget), directive extraction, directive preservation verification, fixture integrity.

## Fixtures

Three fixtures exercise the main cases the handler will hit:

- `cold_start.json` — empty `current_profile`, a handful of inbox additions, a small corpus sample. Tests that the skill produces a coherent first profile without inventing directives.
- `update_with_inbox_and_corpus.json` — populated `current_profile` with a mix of directives and inferred items, fresh inbox additions, and a rich corpus sample. Tests the main monthly-regen path.
- `directive_preservation.json` — populated `current_profile` heavy on directives with unusual punctuation, contradictory inbox additions, and a corpus sample that tempts rephrasing. Tests that directives pass through verbatim and contradicting inbox entries don't override them.

## Preamble guard

Opus is very reliable but not immune to a "Here is the regenerated profile:" leak on an off day. The SKILL.md prompt ends with an explicit first-byte guard and the parser rejects anything that doesn't start with `#`.

## Related

- Plan v5 profile section: `commonplace-plan-v5.md` §"The cockpit (three tiers)" and §"Synthesis → Profile"
- Perennials (tier-2 context): `~/commonplace/profile/perennials.md`
- Profile handler + cron (task 4.2, not yet built)
- `correct(target='profile', ...)` tool (task 4.3, not yet built)
- Smoke test: `scripts/smoke_regenerate_profile.sh`
- Pytest coverage: `tests/test_regenerate_profile_skill.py`
