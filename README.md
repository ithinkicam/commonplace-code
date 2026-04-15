# commonplace-code

Public-safe code for **Commonplace** — a personal commonplace book and reading companion.

The design, execution model, and build phases live in `docs/`:

- [`docs/plan.md`](docs/plan.md) — what Commonplace is (single source of truth for design)
- [`docs/execution-plan.md`](docs/execution-plan.md) — how agents execute the build
- [`docs/phase-0-0.md`](docs/phase-0-0.md) — the build-the-build-system phase
- [`docs/decisions/`](docs/decisions/) — architectural decision records

Agent operating guide: [`AGENTS.md`](AGENTS.md).

## Repo layout

```
commonplace-code/
├── AGENTS.md                # Agent operating guide
├── Makefile                 # Common tasks (run `make help`)
├── pyproject.toml           # Python 3.12 + deps
├── commonplace_server/      # FastMCP server + /capture endpoint
├── commonplace_worker/      # Ingestion + synthesis worker
├── skills/                  # Synthesis skill files (claude -p)
├── tests/                   # pytest
├── scripts/                 # safe-mode, smoke-test, new-skill
├── docs/                    # Plans, ADRs
├── build/                   # State templates (STATE.md, state.json)
└── .github/workflows/       # CI
```

Private content (notes, highlights, profile, library corpus) lives in a separate `commonplace-vault` repo at `~/commonplace/` on the Mac mini — never in this repo.

## Getting started

Requires Python 3.12.

```bash
make help            # list common tasks
make test            # run tests
make lint            # ruff + mypy
make safe-mode       # panic button: stop services, snapshot, drop to shell
```

## Status

Phase 0.0 — scaffolding complete. Next: Phase 0 setup.

See `build/STATE.md` for current build state.
