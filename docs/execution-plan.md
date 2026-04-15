# Commonplace: Agent Execution Plan

Companion document to `commonplace-plan-v5.md`. v5 specifies *what* to build. This specifies *how agents execute* the build.

The goal: you can kick off a phase from your phone, walk away, and come back to a phase that's either complete-and-verified or paused at a specific decision point with a clear question. No babysitting.

---

## Operating principles

1. **Primary agent holds the plan; subagents execute tasks.** The primary maintains state, decides what to dispatch next, and integrates results. Subagents take a focused task, run it to completion, and return a structured summary — never a full transcript.
2. **Every task has explicit acceptance criteria.** "Done" is not a vibe. Done means tests pass, validation gates clear, state file updated.
3. **Deterministic before generative.** Plumbing (file I/O, schema, subprocess management) is built and tested first. Model-driven behavior (synthesis, classification, judgment) is layered on after the foundation works.
4. **Idempotent always.** Every task can be safely re-run. Re-running a completed task is a no-op; re-running a partial task picks up where it stopped.
5. **State lives in a file, not in agent memory.** A persistent `STATE.md` (and supporting `state.json`) is the source of truth for phase progress. Agents read it on start and write to it on completion.
6. **Parallelize ruthlessly when work is independent.** Sequential only when there's a real data dependency.
7. **Stop and surface, don't guess.** When facing ambiguity that wasn't in the plan, agents pause and write a question to `STATE.md` rather than improvising.
8. **Observability over self-reporting.** Logs are the truth. Agent summaries are convenience.

---

## State file structure

Lives at `~/commonplace/build/STATE.md` and `~/commonplace/build/state.json`. The markdown is human-readable status; the JSON is machine-parseable state.

### `STATE.md` — human view

```markdown
# Commonplace Build State

**Current phase:** Phase 1 — Foundation
**Started:** 2026-04-15 09:23 EDT
**Last update:** 2026-04-15 11:47 EDT
**Status:** in_progress

## Phase progress
- [x] Tailscale setup verified
- [x] Dedicated user created
- [x] GitHub repos initialized
- [/] FastMCP skeleton — in progress (subagent: agent-mcp-skel)
- [/] Worker skeleton — in progress (subagent: agent-worker-skel)
- [ ] SQLite schema
- [ ] Job queue
- [ ] Day One MCP connection
- [ ] First round-trip test

## Active subagents
- agent-mcp-skel: building FastMCP server skeleton, started 11:42
- agent-worker-skel: building worker skeleton, started 11:42

## Open questions for human
(none currently)

## Blocked tasks
(none currently)

## Recent completions
- 11:40 — GitHub repos initialized (commonplace-code, commonplace-vault)
- 11:32 — Dedicated commonplace user created with scoped home
- 11:18 — Tailscale verified on phone, iPad, Mac mini
```

### `state.json` — machine view

```json
{
  "phase": "phase_1",
  "phase_started": "2026-04-15T09:23:00-04:00",
  "last_update": "2026-04-15T11:47:00-04:00",
  "tasks": {
    "tailscale_setup": {"status": "complete", "verified_at": "..."},
    "mcp_skeleton": {"status": "in_progress", "agent": "agent-mcp-skel", "started_at": "..."},
    "...": "..."
  },
  "active_agents": [...],
  "open_questions": [],
  "blocked": []
}
```

The primary agent updates both on every task completion. Subagents only write to their own task entries; the primary owns global structure.

---

## Agent roles

### Primary agent (you in your interactive session)

Your Claude Code session on the Mac mini. Holds the plan, coordinates subagents, integrates results, makes dispatch decisions. Doesn't do execution work directly except in the rare case where a single small task is faster than spawning a subagent.

The primary's loop:
1. Read `STATE.md` and `state.json`
2. Identify next dispatchable task (no unmet dependencies, no other agent working it)
3. Spawn subagent with focused task contract
4. While waiting, identify and dispatch other parallelizable tasks
5. When subagents return, validate outputs, run gates, update state
6. Repeat until phase is complete or a blocker surfaces

### Subagents (spawned via Claude Code Task tool)

Focused workers. Each gets:
- A single discrete task with clear inputs and acceptance criteria
- Read access to relevant code, skill files, and the state file
- Write access only to the files they're explicitly working on
- A budget (token/time) and a clear "stop and report" condition

Subagents return:
- Status (complete / blocked / failed)
- Summary of what was done (1-2 paragraphs, not a transcript)
- List of files created or modified
- Test results (passed/failed counts)
- Any unexpected discoveries worth flagging

Subagents do not call other subagents. They do not modify the state file directly — they return their summary and the primary updates state.

---

## Task contract format

Every dispatched task uses this format. The primary writes one of these per dispatch:

```markdown
## Task: <short_id>

**Description:** Build the FastMCP server skeleton with healthcheck endpoint.

**Inputs:**
- v5 plan section: "Architecture at a glance" and "MCP tool surface"
- Repo: ~/code/commonplace-code (already initialized)
- Tech: Python 3.12, FastMCP latest pinned version

**Outputs (acceptance criteria):**
- File: `commonplace_server/server.py` exists and runs
- `healthcheck()` MCP tool returns valid status
- `/healthcheck` HTTP endpoint returns 200 with JSON status
- Server starts via `python -m commonplace_server` without errors
- Test file: `tests/test_server_skeleton.py` passes

**Validation gates:**
- `pytest tests/test_server_skeleton.py -v` exits 0
- `ruff check commonplace_server/` exits 0
- Manual smoke test: `curl http://localhost:8765/healthcheck` returns 200

**Stop and report if:**
- FastMCP version pin causes incompatibility
- Tailscale-bound port conflicts arise
- Any acceptance criterion can't be met within the budget

**Budget:** 30 min wall-clock, ~50K tokens

**Do not:**
- Add tools beyond `healthcheck` (other tools are separate tasks)
- Implement the capture endpoint (separate task)
- Modify the state file
```

This format is verbose, but it's what makes the system actually hands-off. Vague tasks produce vague results.

---

## Parallelization rules

**Default is parallel.** Tasks dispatched to subagents run concurrently unless explicitly ordered.

**Sequential only when:**
- Task B reads files that task A writes
- Task B requires state that A produces
- Both modify the same file

**Common parallelizable groups in this build:**
- Setting up GitHub repos + Tailscale + dedicated user (no dependencies among them)
- Building the MCP server skeleton + the worker skeleton (independent files)
- Writing handlers (YouTube + podcast + article + image — all independent)
- Generating book notes (each book is independent; can spawn 5-10 in parallel)
- Writing test fixtures (one per module, independent)

**Concurrency cap:** Primary should not spawn more than 5 subagents at once. More than that and (a) Claude Code's parallel session handling gets shaky, (b) Mac mini resource contention starts to matter, (c) the primary loses ability to track what's happening. Five is the ceiling; 3 is the sweet spot.

**Coordination rule:** if two parallelizable tasks both need to write to a shared file (e.g., `pyproject.toml`), serialize them. Diff conflicts in shared files are not worth the parallelism gain.

---

## Validation gates

**Every task has automated verification before being marked complete.**

The primary runs gates after a subagent returns. If a gate fails, the task is *not* complete — the primary either re-dispatches with the failure context or surfaces the failure as a blocker.

### Gate types

**Code gates** (for any task that writes code):
- `pytest <relevant test file>` exits 0
- `ruff check` exits 0 on modified files
- `mypy` exits 0 if type hints are present
- File compiles / imports without error

**Behavioral gates** (for any task that builds a feature):
- A documented smoke test passes
- An end-to-end test for the feature exists and passes
- Expected files are present at expected paths

**Data gates** (for ingestion or synthesis tasks):
- Expected number of records were produced (within tolerance)
- Spot-check sample is reasonable (LLM-judged: "does this book note look right?")
- No unhandled errors in the run log

**Integration gates** (for cross-system tasks):
- The new component talks to existing components correctly
- Health check still passes
- Service can start and stop cleanly

**No task is complete until all relevant gates pass.** This is firm — if you skip gates "because it'll be fine," you accumulate broken state that's expensive to debug later.

---

## Testing strategy

This is a personal system, not a public one. The testing investment is calibrated accordingly: enough to catch regressions and validate behavior, not enough to certify against arbitrary inputs.

### Test pyramid

**Unit tests (most numerous):**
- Each module has a corresponding `tests/test_<module>.py`
- Pure functions: test edge cases and happy path
- Database operations: test against an in-memory SQLite
- Skill invocation: mock `claude -p`, verify input formatting and output parsing

**Integration tests (fewer):**
- End-to-end capture: POST to `/capture` → verify file lands in vault → verify embedding lands in DB
- End-to-end synthesis: trigger book note regen → verify skill invoked → verify output written
- End-to-end search: insert known content → query → verify retrieval

**Smoke tests (manual, scripted):**
- A `make smoke` target runs a sequence: start services, run a capture, run a search, verify a healthcheck. Output is human-readable pass/fail.

**Live validation (LLM-judged):**
- For synthesis outputs (book notes, profile, summaries): a separate Claude invocation reviews a sample and reports "does this look right" with reasoning. Catches quality regressions that unit tests can't.

### Test coverage philosophy

Don't chase percentage. Chase confidence in the parts that would silently break things:
- All database write paths
- All file system write paths
- All external API calls (Bluesky, OpenLibrary, Whisper, etc.)
- All subprocess invocations (Claude Code, ffmpeg, yt-dlp, calibre)
- All schema migrations

Skip tests for:
- Glue code that's obviously correct
- Configuration loading
- Most logging

---

## Idempotency and resumability

**Every task is safe to re-run.** This is non-negotiable for hands-off operation.

Mechanisms:
- **Database operations** use `INSERT OR IGNORE` / `INSERT ... ON CONFLICT` — re-running an insert doesn't duplicate
- **File writes** check existence before creating; updates are atomic (`.tmp` + fsync + rename)
- **External fetches** check for existing local copy before re-fetching
- **Synthesis jobs** check whether the target output is current before re-running
- **Schema migrations** use a `schema_version` table; only un-applied migrations run

**Resumability** means a crash mid-phase doesn't require a clean reset:
- The primary checks `state.json` on start and resumes from the last in-progress task
- In-progress tasks are re-dispatched (subagent re-runs idempotently)
- Completed tasks are skipped
- Open questions are surfaced before any new work begins

The smoke test for resumability: kill the primary mid-phase, restart, watch it pick up where it stopped without duplicating work.

---

## Failure modes and recovery

Every task contract specifies "stop and report if" conditions. When those trigger:

1. Subagent returns status `blocked` with a clear description
2. Primary writes the blocker to `STATE.md` under "Open questions for human"
3. Primary continues dispatching other parallelizable tasks if any remain
4. When you return, you read the open questions, resolve them, and tell the primary to continue

**Hard failures** (subagent crashed, returned malformed output, gates failed twice in a row):
- Primary marks the task `failed` with full context
- Primary does NOT auto-retry beyond once
- Primary surfaces as a blocker
- You decide whether to re-dispatch with a different approach, fix manually, or skip

**Rollback paths:**
- Every phase ends with a git commit tagged `phase-N-complete`
- If a phase produces broken state and forward-fix isn't viable, `git checkout phase-(N-1)-complete` restores known-good
- Database has a backup before any destructive migration; restoring is `cp library.db.backup library.db`
- The vault has nightly git commits; recovering a single deleted file is `git checkout <SHA> -- path/to/file`

---

## Context window management

**The whole point of aggressive delegation is to keep the primary's context clean.** A phase should fit in a single session.

Discipline:
- Primary reads v5 once at start, references specific sections by name afterward
- Primary holds: state file, current dispatch list, recent subagent summaries
- Primary does NOT hold: subagent transcripts, code being written, full file contents
- When primary needs to inspect a file, it reads only the relevant section
- Subagent summaries are 1-2 paragraphs, not blow-by-blow

**Token budgets per role:**
- Primary: roughly 80K-120K tokens of accumulated context per phase, sustainable across many hours
- Subagent: 30K-60K tokens per task, completes and exits
- Skill files: <5K tokens each

**When primary's context gets heavy** (over 150K), it should:
1. Write a checkpoint summary to `state.json` (where it is, what's pending)
2. Suggest you start a fresh primary session that picks up from state
3. Not try to soldier on with degraded reasoning

This is a stopping condition, not a failure. The state file is designed precisely so this is a clean handoff.

---

## Logging and observability

**Three log streams, all human-readable:**

1. **`build.log`** — chronological log of all task dispatches, completions, and gate results. Tail this to watch progress.
2. **`agent-<id>.log`** — per-subagent log of what that agent did, written by the agent on completion. Useful for forensics.
3. **`errors.log`** — only errors, blockers, and unexpected events. Short and high-signal.

The primary writes to `build.log` after every state transition. Subagents write their own `agent-<id>.log` before returning.

When you check in after stepping away, the order to read:
1. `STATE.md` (current status)
2. `errors.log` (anything bad?)
3. `build.log` tail (what happened recently)
4. Specific `agent-<id>.log` files only if you need detail

---

## Specific agent dispatch patterns for each phase

### Phase 0 — Setup

Mostly sequential because most tasks are environment configuration with dependencies.

**Sequential:**
- Pin Claude Code version (must precede anything else)
- Create dedicated user (must precede repo init)

**Parallel after that:**
- Tailscale setup × 3 (mini, phone, iPad — primary can dispatch all three checks in parallel)
- Ollama install + model pull
- Drive for Desktop sync verification
- Day One CLI verification
- GitHub repo init × 2

Estimated wall-clock: 2-3 hours, much of it waiting on installs.

### Phase 1 — Foundation

Higher parallelism opportunity.

**Sequential foundations:**
- SQLite schema and migration system (everything else builds on this)

**Parallel after schema:**
- FastMCP server skeleton with healthcheck
- Worker skeleton with launchd config
- Job queue tables and tools
- Memory entries via Claude memory tool
- Day One MCP connection
- Phone HTTP Shortcut configuration
- iPad Apple Shortcut configuration

**Sequential at the end:**
- First round-trip integration test (depends on all of the above)

Estimated wall-clock: 6-8 hours of agent work, much of it parallel.

### Phase 2 — Ingestion

High parallelism — handlers are mostly independent.

**Sequential:**
- Embedding pipeline scaffold (handlers depend on this)
- Book classification skill (note generation depends on this)

**Parallel:**
- Bluesky handler
- Library watched-folder handler
- Kindle scraper
- StoryGraph CSV importer
- Three book note skills (argument, narrative, poetry templates)

**Long-running batch (kicks off late in phase):**
- Book note generation across the library
- Runs overnight, primary checks in the morning

Estimated wall-clock: 8-12 hours of agent work spread across 1-2 days due to overnight batch.

### Phase 3 — Capture handlers

Highly parallel.

**Parallel from the start:**
- YouTube handler
- Podcast handler with RSS detection
- Bluesky URL handler
- Article handler with Trafilatura
- Image handler with Tesseract
- Video file handler with ffmpeg + Whisper
- Capture summary skill

**Sequential at end:**
- Unified `search_commonplace` tool wiring (depends on all handlers existing)
- Pinned Haiku chat configuration (manual)

Estimated wall-clock: 6-8 hours.

### Phase 4 — Synthesis and serendipity

Mixed; the judge prompt requires iteration.

**Parallel up front:**
- Profile regen skill
- `correct` tool implementation
- Serendipity judge skill (initial version)
- `surface` tool implementation
- Custom instructions for surfacing trigger

**Sequential and iterative:**
- Real corpus-driven testing of serendipity (you doing actual chats and watching what surfaces)
- Judge prompt iteration based on directives accumulated from real use

Estimated wall-clock: 4-6 hours for code, then ongoing iteration over your first month.

---

## Pre-flight checklist before starting any phase

The primary should verify before dispatching:
- [ ] `state.json` is readable and current
- [ ] No leftover in-progress tasks from a prior session
- [ ] Open questions from prior session have been resolved
- [ ] Git working directory is clean
- [ ] Required services from prior phases are running
- [ ] Pre-phase smoke test passes (everything that worked before still works)

If any check fails, the primary surfaces it before starting work.

---

## What "great software engineering" adds beyond what you named

Things I'd flag that weren't in your initial list but matter:

**Reproducibility.** Every dependency pinned. Every config explicit. The state of the system at any commit can be reproduced from the repo + lock files. This is what saves you when something breaks in three months and you need to roll back.

**A "release" notion even for personal software.** Tag commits at meaningful states (`phase-1-complete`, `mvp`, etc.). When something breaks, you have known-good points to bisect against.

**Schema migrations from day one.** Adding a column later is easy if you have migrations; agonizing if you don't.

**A `Makefile` (or `justfile`) of common tasks.** `make smoke`, `make backup`, `make new-skill`, `make test`. Discoverable, low-friction, hard to forget.

**Documentation as code, not as prose.** README is short and points to where things actually live. Each module has a docstring. Each skill has its own README. When future-you (or future-Claude) needs to understand something, the answer is one grep away.

**A "panic button" — `make safe-mode`** that stops all services, takes a snapshot, and gives you a clean shell on the Mac mini. For the times when something is going badly wrong and you want to stop digging the hole.

**Telemetry of synthesis costs.** Even though we're on flat-rate, log Claude Code invocations and approximate token usage so we can see if something is consuming way more than expected. A skill that's silently producing 5x the output it should is otherwise invisible.

**A "trial run" mode for destructive operations.** Even with non-destructive defaults, some operations (mass re-embedding, full library re-scan) have real cost. Add a `--dry-run` flag that reports what would happen without doing it.

---

## What this all unlocks

If everything above is in place, your typical phase looks like:

1. You read `STATE.md` from your phone in the morning, see the plan
2. You message Claude on the Mac mini: "begin Phase N per the spec"
3. You go do other things
4. You check in periodically; `STATE.md` shows progress
5. If a blocker surfaces, you resolve it from your phone with a one-line answer
6. Phase completes; you get a summary, a tagged commit, and verified state
7. You decide whether to start the next phase or take a break

You're not in the loop on individual file edits, individual subagent dispatches, individual gate runs. You're in the loop on real decisions and real blockers. That's the experience this execution plan is designed to produce.

---

## Phase 0.0 comes first

Before any Commonplace code, build the rails the agents will run on. This is its own phase, documented in `commonplace-phase-0-0.md`. It produces:

- Both GitHub repos initialized with proper structure
- `STATE.md` and `state.json` templates
- `AGENTS.md` at repo root
- `Makefile`, test scaffold, CI config
- `scripts/safe-mode.sh` panic button
- First ADR documenting the execution model
- A self-test that verifies the agent execution loop works end-to-end

Half a session. After this completes, every later phase has the operating environment it expects.

**Skip Phase 0.0 at your peril.** Improvised state files are inconsistent. Improvised gates get skipped. The "hands-off" promise breaks down quietly. Build the rails first.

---

## Companion artifacts to create at Phase 0

These should exist before any real work begins:

1. **`AGENTS.md`** at repo root — short version of this document, oriented at agents reading it. Tells any agent that picks up the codebase how to operate.
2. **`STATE.md` template** — empty initial state file the primary populates
3. **`Makefile`** — common tasks
4. **`tests/` scaffold** — directory structure with conftest.py
5. **`docs/decisions/`** — ADR-style decision log so future-you (or future-Claude) understands why things are the way they are
6. **`scripts/safe-mode.sh`** — the panic button
7. **`.github/workflows/`** — even for a personal repo, run tests on every commit. Free safety net.

These are part of Phase 0.0 setup work, fully specified in `commonplace-phase-0-0.md`.
