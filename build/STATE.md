# Commonplace Build State

**Current phase:** Phase 1 — Foundation
**Started:** 2026-04-15T10:35:00-04:00
**Last update:** 2026-04-15T13:40:00-04:00
**Status:** in_progress

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
- [ ] 1.7 — Day One MCP wiring (blocked — Day One toggle on but no listener; need config shape from app settings)
- [ ] 1.8 — Memory edits + perennials (human)
- [ ] 1.9 — Android HTTP Shortcut + iPad Shortcut (human; docs at `docs/phone-ipad-shortcuts.md`)
- [ ] 1.10 — Round-trip integration test (blocks on 1.9)

## Active subagents

(none — paused at human-gated blockers)

## Open questions for human

1. **Day One MCP config (1.7):** Day One toggle is on in-app but Day One is not listening on any TCP/UDP port, has no visible UNIX socket, and no MCP helper binary is present in the app bundle. Day One's settings screen should display either a command to run (stdio MCP) or a URL (HTTP MCP) — paste whatever Day One's UI shows so I can wire it into `~/.claude.json`.
2. **Memory + perennials (1.8):** your Claude memory-tool + preferences-panel work. Reply "memory done" when finished.
3. **Phone + iPad shortcuts (1.9):** step-by-step doc at `docs/phone-ipad-shortcuts.md`. Needs Tailscale active on phone + iPad; token from keychain. Reply "1.9 done" when both devices successfully hit /capture.

## Blocked tasks

(none)

## Recent completions

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
