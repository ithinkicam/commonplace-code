# Phase 0.0: Build the Build System

Before any Commonplace code gets written, the rails that agents will run on need to exist. This phase produces the scaffolding — state file, task contracts, gates, logs, panic button, repos, CI — without which the rest of the plan can't run hands-off.

Half a session of focused work. Maybe 2-3 hours wall-clock. After this completes, every subsequent phase has the same operating environment to plug into.

---

## What this phase produces

A working repo with all the agent infrastructure in place. By the end:
- Two GitHub repos exist (`commonplace-code`, `commonplace-vault`) with initial structure
- `STATE.md` and `state.json` exist as templates the primary populates per phase
- `AGENTS.md` exists at repo root explaining the operating model to any agent
- `Makefile` exists with common tasks
- `tests/` scaffold exists with conftest, fixtures directory, and a single passing smoke test
- `docs/decisions/` exists with the first ADR (the decision to follow this execution model)
- `scripts/safe-mode.sh` exists and works
- `.github/workflows/` exists with a basic CI config that runs tests on push
- The primary agent has run a self-test that proves the scaffolding works

This is the "Phase 0.0 verification" — the primary writes a hello-world task contract, dispatches a subagent to fulfill it, validates with gates, updates state. If that loop works end-to-end on a trivial task, every later phase will work on real ones.

---

## Why this matters

Without Phase 0.0, the first time something in the agent execution model needs to exist, an agent will improvise it. Improvised state files are inconsistent. Improvised task contracts are ambiguous. Improvised gates get skipped under pressure. The whole "hands-off" promise breaks down quietly.

Building the rails first is the equivalent of writing tests before code. It feels slower at the start. It saves you ten times the cost later.

---

## Task list

Tasks are tagged with parallelism markers: `[seq]` must run sequentially with predecessors, `[par]` can run alongside others.

### Repository setup

**Task 0.0.1 [seq]** — Create the two GitHub repos
- `commonplace-code` (public-safe scaffolding)
- `commonplace-vault` (private backup target)
- Both initialized with a README, `.gitignore`, and MIT license (vault repo's MIT is fine; the contents are private)
- Local clones at `~/code/commonplace-code` and `~/commonplace/` (vault is the live working directory)

**Task 0.0.2 [par after 0.0.1]** — Initial directory structure for `commonplace-code`
```
commonplace-code/
├── AGENTS.md
├── README.md
├── Makefile
├── pyproject.toml
├── .gitignore
├── .github/workflows/ci.yml
├── commonplace_server/
│   └── __init__.py
├── commonplace_worker/
│   └── __init__.py
├── skills/
│   └── README.md          (explains skill file format)
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   └── test_smoke.py
├── scripts/
│   └── safe-mode.sh
├── docs/
│   ├── decisions/
│   │   └── 0001-execution-model.md
│   └── README.md
└── build/
    ├── STATE.md.template
    └── state.json.template
```

### Operating documents

**Task 0.0.3 [par after 0.0.1]** — Write `AGENTS.md`
A condensed version of the execution plan oriented at agents reading the repo for the first time. Explains:
- The state file system and where to find it
- How to read a task contract
- How to write a subagent summary
- The parallelism rules
- The gate requirements
- How to surface a blocker
- Pointer to the full execution plan in `docs/`

Should be readable in 5 minutes. The full execution plan lives at `docs/execution-plan.md` for deeper reference.

**Task 0.0.4 [par after 0.0.1]** — Copy execution plan into repo
Place `commonplace-execution-plan.md` (this conversation's output) at `docs/execution-plan.md`. Place `commonplace-plan-v5.md` at `docs/plan.md`. Both are now version-controlled with the code.

**Task 0.0.5 [par after 0.0.1]** — `STATE.md.template` and `state.json.template`
Templates with placeholder fields the primary fills in at phase start. Include a header comment explaining how the file is updated.

**Task 0.0.6 [par after 0.0.1]** — First ADR
`docs/decisions/0001-execution-model.md` — Architectural Decision Record explaining why we adopted the primary/subagent execution model with state file coordination. Future-you (or future-Claude) reading the repo in six months can answer "why are we doing it this way?" in 2 minutes.

ADR template:
```markdown
# ADR-0001: Execution Model

## Status
Accepted, 2026-04-15

## Context
Building Commonplace requires a multi-phase project executed largely by AI agents...

## Decision
We adopt a primary-agent + subagent model with...

## Consequences
+ Hands-off operation possible
+ Parallelism without coordination overhead
- More upfront scaffolding
- State file becomes a single point of contention
```

### Tooling

**Task 0.0.7 [par after 0.0.2]** — `Makefile` with common tasks
```makefile
.PHONY: help test smoke lint format safe-mode new-skill clean

help:           ## Show this help
        @grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

test:           ## Run all tests
        pytest tests/ -v

smoke:          ## Run smoke tests against running services
        bash scripts/smoke-test.sh

lint:           ## Run linters
        ruff check commonplace_server commonplace_worker tests
        mypy commonplace_server commonplace_worker

format:         ## Format code
        ruff format commonplace_server commonplace_worker tests

safe-mode:      ## Stop services, take snapshot, drop to safe shell
        bash scripts/safe-mode.sh

new-skill:      ## Scaffold a new skill file (usage: make new-skill name=foo)
        bash scripts/new-skill.sh $(name)

clean:          ## Remove build artifacts
        find . -type d -name __pycache__ -exec rm -rf {} +
        rm -rf .pytest_cache .mypy_cache .ruff_cache
```

**Task 0.0.8 [par after 0.0.2]** — `scripts/safe-mode.sh`
The panic button. Stops services, takes a snapshot of the database and vault, drops you to a clean shell.

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "Entering safe mode..."

# Stop services
launchctl unload ~/Library/LaunchAgents/com.commonplace.server.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.commonplace.worker.plist 2>/dev/null || true

# Snapshot
SNAPSHOT_DIR=~/commonplace/snapshots/safe-mode-$(date +%Y%m%d-%H%M%S)
mkdir -p "$SNAPSHOT_DIR"
cp ~/commonplace/library.db "$SNAPSHOT_DIR/"
tar -czf "$SNAPSHOT_DIR/vault.tar.gz" -C ~/commonplace --exclude='*.db' --exclude='snapshots' .

echo "Snapshot saved to $SNAPSHOT_DIR"
echo "Services stopped. You're in a safe shell."
echo "To restart: launchctl load ~/Library/LaunchAgents/com.commonplace.server.plist"
exec $SHELL
```

**Task 0.0.9 [par after 0.0.2]** — `scripts/smoke-test.sh`
A scripted health check that exercises the system end-to-end. At Phase 0.0, this is a stub — checks that placeholder files exist. Real assertions get added as features land.

**Task 0.0.10 [par after 0.0.2]** — `scripts/new-skill.sh`
Scaffolds a new skill directory with a `SKILL.md` template. Used in later phases when adding synthesis skills.

### Test infrastructure

**Task 0.0.11 [par after 0.0.2]** — `pyproject.toml` with dependencies
- Python 3.12
- Pin: fastmcp, pytest, pytest-asyncio, ruff, mypy
- Dev dependencies separate from runtime
- Tool config sections for ruff and mypy

**Task 0.0.12 [par after 0.0.2]** — `tests/conftest.py` with shared fixtures
Stubs for fixtures we'll use later: temp vault directory, in-memory SQLite, mocked Claude Code subprocess. At Phase 0.0 these are skeletons; later phases flesh them out.

**Task 0.0.13 [par after 0.0.2]** — `tests/test_smoke.py`
A single passing test that imports the package and asserts version. Proves the test infrastructure works end-to-end. Anything more elaborate at Phase 0.0 is premature.

**Task 0.0.14 [par after 0.0.2]** — `.github/workflows/ci.yml`
- Triggers: push to main, pull requests
- Steps: checkout, setup Python, install deps, run tests, run linter
- Caches pip cache for speed
- Free safety net for personal repo; catches regressions automatically

### Verification (the actual proof Phase 0.0 worked)

**Task 0.0.15 [seq, last]** — Self-test of the agent execution loop

The primary agent does this end-to-end with itself as the test subject:

1. Initialize `STATE.md` and `state.json` for a fake "Phase 99: Self-Test"
2. Write a task contract for a trivial task: "Add a docstring to `commonplace_server/__init__.py` that says 'Commonplace MCP server.'"
3. Dispatch this to a subagent
4. Receive subagent summary
5. Run the gate: verify the docstring exists and matches
6. Update `state.json` to mark the task complete
7. Commit the change
8. Verify CI passes on the commit

If all 8 steps execute cleanly, the agent execution model works. The primary writes a final entry to `STATE.md`:
```
Phase 0.0 complete. Agent execution loop verified end-to-end.
Ready to begin Phase 0 of the build proper.
```

If any step fails, surface it as a blocker. The whole point of Phase 0.0 is to find these failures before they cost real work.

---

## Acceptance criteria for Phase 0.0

The phase is complete when:
- [ ] Both repos exist on GitHub with initial commits
- [ ] All directories and files in the structure above exist
- [ ] `make help` runs and shows all targets
- [ ] `make test` runs and passes
- [ ] `make lint` runs and passes
- [ ] `bash scripts/safe-mode.sh` runs without error (and you can recover by re-loading the launchd plists, even though they don't exist yet)
- [ ] CI workflow runs on the initial commit and passes
- [ ] `AGENTS.md` is readable and complete
- [ ] First ADR exists
- [ ] Self-test (task 0.0.15) completes successfully

---

## What NOT to build in Phase 0.0

The temptation will be to start building real Commonplace functionality "while we're in there." Resist.

Do not build:
- The MCP server itself (Phase 1)
- Any handlers (Phase 3)
- Any skills with actual content (later phases)
- launchd configs for services that don't exist yet
- Database schema (needs the rest of Phase 1's design)
- Anything in `commonplace-vault` beyond initialization

Phase 0.0's whole job is producing the rails. The trains come later.

---

## Dispatch pattern for Phase 0.0

The primary's loop for this phase:

1. Read this document
2. Initialize `STATE.md` for Phase 0.0
3. Dispatch task 0.0.1 (must come first — repos)
4. After 0.0.1 completes, dispatch tasks 0.0.2-0.0.14 in parallel batches of 3-5
5. After all parallel tasks complete and gates pass, dispatch 0.0.15 (self-test)
6. On 0.0.15 success, mark phase complete and tag commit `phase-0.0-complete`
7. Surface to you: "Phase 0.0 complete. Ready for Phase 0?"

Wall-clock estimate: 2-3 hours of agent work. Most tasks are small (write a config file, scaffold a directory) and parallelize easily.

---

## After Phase 0.0

You're set up for hands-off operation. Every subsequent phase:
1. Has somewhere to write state
2. Has a panic button if something goes wrong
3. Has CI to catch regressions
4. Has documented decisions to refer back to
5. Has a Makefile of common operations
6. Has tests that verify changes
7. Has a working agent execution loop

Phase 0 (the original Phase 0 from v5) follows immediately and uses all of this. From your perspective, the only difference is that now when you say "begin Phase 0," the agent has a real environment to operate in instead of inventing one.
