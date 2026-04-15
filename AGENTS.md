# AGENTS.md

Operating guide for any agent (primary or subagent) picking up this repo. Read this first. The full execution model lives at `docs/execution-plan.md`; this is the 5-minute version.

## What this repo is

Commonplace is a personal commonplace book and reading companion running on a Mac mini M1 behind a private Tailscale tailnet. Design: `docs/plan.md`. Execution model: `docs/execution-plan.md`. Phase 0.0 (this scaffolding): `docs/phase-0-0.md`. Those three documents are the source of truth; this file just tells you how to operate.

## The execution model in one paragraph

A **primary agent** holds the build plan and state; **subagents** execute discrete tasks and return structured summaries. State lives in `build/STATE.md` (human-readable) and `build/state.json` (machine-parseable). The primary dispatches, validates outputs through gates, and updates state. Subagents never modify state directly and never spawn other subagents. Tasks are idempotent and parallelizable by default.

## Where to find things

| What | Where |
|---|---|
| Current build state | `build/STATE.md`, `build/state.json` |
| Full design spec | `docs/plan.md` |
| Execution model (detailed) | `docs/execution-plan.md` |
| Phase 0.0 spec | `docs/phase-0-0.md` |
| Architectural decisions | `docs/decisions/` (ADRs) |
| Skill files (synthesis prompts) | `skills/` |
| MCP server code | `commonplace_server/` |
| Job-queue helper (pure DB functions for submit/status/cancel) | `commonplace_server/jobs.py` |
| Worker code | `commonplace_worker/` |
| Database package (schema, migrations, connect/migrate API) | `commonplace_db/` |
| Tests | `tests/` |
| Common tasks | `Makefile` (run `make help`) |
| Panic button | `scripts/safe-mode.sh` |

## Reading a task contract

Every task the primary dispatches uses this format (full spec in `docs/execution-plan.md`):

- **Description** — what to build in one sentence
- **Inputs** — relevant plan sections, repo paths, pinned tech
- **Outputs (acceptance criteria)** — concrete, verifiable conditions for "done"
- **Validation gates** — commands that must exit 0 (pytest, ruff, smoke)
- **Stop and report if** — conditions that mean "surface this, don't improvise"
- **Budget** — wall-clock + token ceiling
- **Do not** — out-of-scope adjacent work

If any section is missing or ambiguous, stop and surface rather than guessing.

## Writing a subagent summary

On completion, return:

- **Status** — `complete` / `blocked` / `failed`
- **Summary** — 1–2 paragraphs of what was done (not a transcript)
- **Files** — created/modified paths
- **Test results** — pass/fail counts
- **Discoveries** — anything unexpected worth flagging

Never paste file contents into the summary. The primary reads files from disk when it needs to.

## Parallelism rules

- **Default parallel.** Sequential only when task B reads files task A writes, task B needs state A produces, or both modify the same file.
- **Cap: 5 concurrent subagents.** Sweet spot is 3.
- **Shared-file writes serialize.** Don't race on `pyproject.toml` or similar.

## Validation gates (every task)

- **Code**: `pytest` exits 0; `ruff check` exits 0; mypy clean if types present
- **Behavioral**: documented smoke test passes; expected files present
- **Data**: expected record count within tolerance; spot-check reasonable
- **Integration**: healthcheck still passes; service starts and stops cleanly

A task isn't done until gates pass. No exceptions under pressure.

## Surfacing a blocker

When you hit a "stop and report" condition or ambiguity outside the plan:

1. Return status `blocked` with a one-sentence description and full context
2. Do not improvise; do not guess
3. The primary writes the blocker to `STATE.md` under "Open questions for human"
4. Other parallel work continues; the human resolves the blocker on check-in

## Things you must never do

- Modify `build/STATE.md` or `build/state.json` as a subagent (primary owns state)
- Spawn other subagents as a subagent
- Skip gates because "it'll be fine"
- Rewrite the design docs (`docs/plan.md`, `docs/execution-plan.md`) — propose changes via an ADR
- Auto-retry a failed task more than once without surfacing

## Model selection (for dispatch)

- **Opus**: planning, research, heavy reasoning, prompt iteration, cross-corpus analysis, root-cause debugging. Primary agent.
- **Sonnet**: default for code-writing subagents (servers, handlers, schemas, integration tests, synthesis skills over real content).
- **Haiku**: mechanical work (scaffolding, boilerplate config, CRUD plumbing, unit tests, classification/judge skills, pure-execution gates).

Pick the lowest tier that can do the job well. Default Sonnet when unsure.

## Model references (runtime skills)

Skills invoked via `claude -p <skill>` pin their model in the skill's frontmatter. Plan-specified pins:

- `classify_book`, `summarize_capture`, `judge_serendipity` → Haiku
- `generate_book_note`, `reconcile_book` → Sonnet
- `regenerate_profile` → Opus (monthly, low-volume, heavy reasoning)

## Further reading

- `docs/plan.md` — what Commonplace is and why
- `docs/execution-plan.md` — full agent operating model
- `docs/phase-0-0.md` — this phase's spec
- `docs/decisions/` — why things are the way they are
