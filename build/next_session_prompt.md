# Next-session prompt — product discussions on observability + cross-tradition

Commonplace — facilitate two product discussions. **Do not implement until
alignment is reached on each.** Your role is to pull context, frame tradeoffs
crisply, ask the right questions, and — once the user chooses a direction —
scope a concrete plan they can approve or redirect.

## Catch up first

Working directory `~/code/commonplace-code`. HEAD should be `9116e8f`
("embedding: circuit breaker on Ollama HTTP failures"). Confirm with
`git log -5 --oneline` and `git status`.

The prior session closed all "clear-path autonomous" items and identified
two gaps that need product input before any more engineering:

1. **Real-query observability** on the serendipity judge.
2. **Cross-tradition liturgical matching** scope.

Read in this order before starting:
- `build/STATE.md` — status line near line 37 plus the narrative paragraph.
  The pipeline is mature; no lit_4_* waves pending.
- `build/state.json` — `active_phases`, and any `partial_*` / `shipped_partial`
  task entries. The `tasks.lit_4_14` and `tasks.lit_4_15` notes are the most
  recent substantive work; scan for gate numbers and residuals.
- `AGENTS.md` — primary/subagent operating model. Still applies.
- `commonplace_server/surface.py` — `run_surface()`. This is the function
  whose output we want to observe.
- `skills/judge_serendipity/SKILL.md` — the judge's current rubric. Two
  waves of calibration (4.11 register gate + 4.14 canonical-grounding) are
  in place; check the "worked accept" and "worked reject" sections.
- `tests/fixtures/prose_regression.json` and
  `tests/fixtures/liturgical_surfacing.json` — the synthetic seed sets the
  judge is currently evaluated against. **All 20 prose + 10 liturgical
  seeds are synthetic; nothing in the evaluation loop uses real user
  queries.** This is the heart of conversation #1.
- `docs/liturgical-ingest-plan.md` — sections and status. All Anglican;
  cross-tradition is mentioned but not scoped.

## Conversation #1 — real-query observability

### The gap

Nothing tracks `run_surface()` calls on real user queries. The 4.7/4.14/4.15
replay harness (`scripts/replay_4_7_review.py`) exercises 20 prose + 10
liturgical synthetic seeds — enough to catch regressions in the direction
the calibration has been going, but not enough to answer "is this actually
useful?". Judge tuning has been fixture-driven; if the user is getting bad
suggestions on real queries, there's no feedback loop.

### What's been ruled out already (don't re-propose)

- "Just log everything" — too broad, and the user cares about the right
  signal, not more data.
- Automated quality metrics — we don't have ground truth on real queries
  (that's what the user's feedback WOULD BE).

### Open questions for the user

Ask in this order, briefly:
1. **What's the outcome you want?** "Find bad suggestions" vs "find good
   suggestions I missed" vs "see what gets surfaced at all." These have
   different logging shapes.
2. **Per-call or per-session logging?** A surface call is one seed +
   accept/reject decisions on candidates. Do you want every call retained,
   or a rolling window, or only flagged ones?
3. **How do you want to flag a bad suggestion in the moment?** Options:
   - MCP tool: "mark last surface bad" invoked from Claude
   - A slash-skill like `/correct surface` with target-id
   - Nothing — you'll review a digest later and mark from there
4. **How long do these logs live?** Forever (grows unbounded), 30 days,
   or "until I finish a tuning pass then wipe"?

### Candidate shapes the agent should price out after alignment

- **Minimal log-only**: add structured logging on every `run_surface()`
  call + `embed_document()` — seed, candidates, accepted ids, reasons,
  timestamp. Store in a new `surface_invocations` table. ~1-2 files of
  changes. Ships in a session.
- **Log + digest tool**: add the logging, plus a new MCP tool
  `recent_surfacings(limit=N)` so Claude can pull "what have I been
  surfaced in the last week" for the user to review. ~2-3 files. Ships
  in a session.
- **Log + digest + feedback**: the above plus a `correct_surface`
  extension to the existing correct-tool (or new `flag_surface_judgement`).
  The feedback loop writes to a table that future judge tuning can draw
  from. ~3-4 files. Ships in a session.

Pair conversation #1 with `skills/correct` — there's prior-art for a
correction pattern (`target_type='judge_serendipity'` was added in 4.6).

### Observability infrastructure question

The user separately flagged "no metrics / dashboards" as a conspicuous
absence. If they go for option 2 or 3 above, this naturally connects —
the `surface_invocations` table becomes the data source a later metrics
pass would query. Don't scope the metrics layer itself in this session;
keep the conversation focused on the logging/feedback interface.

## Conversation #2 — cross-tradition liturgical matching

### The gap

The retrieval plumbing is tradition-agnostic — `search()` can filter by
tradition, the judge can reason about tradition in its rubric (the
canonical-grounding rule accepts across tradition when theologically
warranted). But **every seed in `tests/fixtures/liturgical_surfacing.json`
is Anglican** (BCP 1979 + LFF 2022). The 10 lit_pos + 10 lit_neg seeds
test within-tradition accuracy only.

The underlying DB has liturgical data for Anglican seeded and parsed.
Orthodox (Jordanville) and Catholic are mentioned in
`docs/liturgical-ingest-plan.md` as future phases but have no parser,
no seed, no ingest.

### What's been ruled out already

- "Just run the judge on cross-tradition queries" — the user isn't asking
  for a behavior change, they're asking whether to scope the work.

### Open questions for the user

1. **What's the cross-tradition experience you want?** Examples to disambiguate:
   - Query "rest and recovery" → surface both the Anglican Collect for
     the Renewal of Life AND the Orthodox Akathist of Thanksgiving? (Parallel surfacing)
   - Query "Mary, Mother of God" → surface the canonical collect from the
     feast in whichever tradition has it? (Best-match across traditions)
   - Query that mentions a specific tradition → limit to that tradition? (Explicit gating)
2. **Which traditions, in what order?** BCP 1979 is done. Next: Orthodox
   (Jordanville Prayer Book is the usual target)? Catholic (Roman Missal)?
   Lutheran? Others?
3. **Source material**: do you have digital copies ready, or is sourcing
   part of the scope?
4. **Judge calibration**: the register gate and canonical-grounding rules
   were authored against Anglican theological register. Do you expect
   similar calibration needed for each tradition, or will the existing
   rules transfer?

### Candidate shapes to price out after alignment

- **Fixture-only**: add 5-10 cross-tradition lit_pos seeds to the existing
  fixture, run the judge against them, see what breaks. No new ingest.
  Needs existing data in the DB for the target tradition (so this
  presupposes at least one more tradition ingested). ~1 session of
  fixtures + replay tuning.
- **One-tradition pilot**: Jordanville (Orthodox) parser + seed file +
  handler + small LFF-equivalent for Orthodox commemorations + 10
  cross-tradition seeds + judge calibration. Multi-session wave.
  Complexity roughly mirrors Phase 7 Wave 1B–1C (BCP parsers).
- **Cross-tradition architecture review**: audit whether `tradition` as a
  column is the right shape, or whether we need something like a "canonical
  equivalents" table that links Anglican Collect-for-X to Orthodox
  equivalent. Scoping-only session, no code. Pairs with conversation #1
  — the feedback data could inform the equivalence mapping.

## Operating notes

- Prior-session pace: commit per shipped unit + push to origin; user does
  `git push` manually **unless** they've already green-lit autonomous
  push (they did in the prior session — check `git log origin/main..HEAD`
  for local-only commits before assuming).
- Gates still: `.venv/bin/python -m pytest -m "not live" -q` (~108s on
  native arm64, 1773 baseline as of `9116e8f`), ruff + mypy on touched
  files.
- Five WIP-ish items you'll still see in `build/`: untracked replay
  diagnostics (`4_7_replay_results_run1.json`, `run2.json`,
  `4_12_replay_results.json`), empty `build/commonplace.db` stub, and
  this `next_session_prompt.md`. Preserve all of them — do not stage or
  delete.
- Kindle session cookies are expired — any backfill conversation needs
  the user to run `make kindle-cookies-refresh` first.

## Authority

- **Facilitate, don't implement.** Get to a yes/no decision on each
  conversation, scope the chosen option, **then** ask explicit approval
  before writing code.
- Pivot freely inside a conversation if the user's answer reframes the
  question.
- Escalate if either conversation opens into a roadmap-level question
  (e.g., "should commonplace support cross-device sync") — that's
  product scoping that needs a separate dedicated session.

## First move

Open with conversation #1 (higher leverage, clearer path). Summarize the
gap in 2-3 sentences, then ask question 1 from the "Open questions"
list. Let the user redirect from there.
