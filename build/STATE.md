# Commonplace Build State

**Current phase:** Phase 1 — Foundation
**Started:** 2026-04-15T10:35:00-04:00
**Last update:** 2026-04-15T14:35:00-04:00
**Status:** complete

Phase 0 complete (toolchain, network, external integrations pinned).
1.1 complete: `commonplace_db` package migrates clean, 20/20 tests green, ruff + mypy + smoke pass.
Dispatching wave 2: 1.2 (MCP skeleton) + 1.3 (worker skeleton) in parallel. 1.4 serialized after 1.2 to avoid server.py conflicts.

## Phase progress

- [x] 1.1 — SQLite schema + migration system (ADR-0003 records schema choices)
- [x] 1.2 — FastMCP server skeleton (healthcheck via `mcp.tool` + `mcp.custom_route`; /capture deferred)
- [x] 1.3 — Worker skeleton + launchd config (atomic SQLite claim, 9/9 tests, plist linted)
- [x] 1.4 — Job queue tools (submit_job / get_job_status / cancel_job; 13 new tests, suite at 52/52)
- [x] 1.5 — Plex collision resolved via ADR-0004 (:8443 on existing hostname, tailnet-only)
- [x] 1.6 — /capture endpoint (bearer auth, atomic inbox write, job enqueue; 23 new tests; e2e curl 202)
- [x] 1.7 — Day One MCP wired at user scope (`dayone` via stdio; `claude mcp list` shows Connected)
- [x] 1.8 — Memory edits + perennials (user confirmed "memory done")
- [x] 1.9 — iPad Shortcut (user owns execution; instructions in Gmail Drafts; per user, count this done)
- [x] 1.10 — Round-trip test (stub `capture` handler + 2 integration tests; suite 77/77)

## Active subagents

(none — paused at human-gated blockers)

## Open questions for human

(none — Phase 1 closed)

## Blocked tasks

(none)

## Recent completions

- 14:35 — 1.10 Round-trip test complete; Phase 1 closed (77/77 tests, ruff + mypy clean)
- 13:40 — 1.6 /capture endpoint complete (23 new tests, e2e curl returned 202, suite 75/75)
- 13:30 — 1.5 Plex collision resolved (ADR-0004: :8443 on existing hostname + `make tailscale-serve`)
- 13:15 — Phase 1 wave 1 committed and tagged `phase-1-wave-1`
- 13:15 — Bearer token generated + stored in keychain (`commonplace-capture-bearer/capture`)
- 11:17 — 1.4 Job queue tools complete (commonplace_server/jobs.py + 3 MCP tools, 13 new tests; suite 52/52)
- 11:05 — 1.3 Worker skeleton complete (9/9 worker tests, plist linted, launchctl targets in Makefile)
- 11:05 — 1.2 MCP server skeleton complete (healthcheck MCP tool + HTTP route via `custom_route`; 3/3 tests)
- 10:48 — 1.1 SQLite schema complete (commonplace_db package, 20/20 tests, ADR-0003)
- 10:35 — Phase 1 opened; pre-flight passed (git clean, smoke green, phase-0-complete tag present)

## Schema ambiguities surfaced by 1.1 (resolve before Phase 2)

- Chunk granularity (paragraph vs sliding window; token budget) — affects embedding pipeline
- sqlite-vec integration pattern (VIRTUAL TABLE vs BLOB + external ANN) — affects search tool shape
- nomic-embed-text vector dimension (assumed 768) — confirm against pinned model

## Carried forward from Phase 0

- Plex Funnel collision at plex-server.tailb9faa9.ts.net — resolve before /capture endpoint lands
- Day One MCP wiring happens here in Phase 1
- Keychain-based secret storage (plan v5 locked decision)
