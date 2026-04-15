# Commonplace Build State

**Current phase:** Phase 1 — Foundation
**Started:** 2026-04-15T10:35:00-04:00
**Last update:** 2026-04-15T11:17:00-04:00
**Status:** in_progress

Phase 0 complete (toolchain, network, external integrations pinned).
1.1 complete: `commonplace_db` package migrates clean, 20/20 tests green, ruff + mypy + smoke pass.
Dispatching wave 2: 1.2 (MCP skeleton) + 1.3 (worker skeleton) in parallel. 1.4 serialized after 1.2 to avoid server.py conflicts.

## Phase progress

- [x] 1.1 — SQLite schema + migration system (ADR-0003 records schema choices)
- [x] 1.2 — FastMCP server skeleton (healthcheck via `mcp.tool` + `mcp.custom_route`; /capture deferred)
- [x] 1.3 — Worker skeleton + launchd config (atomic SQLite claim, 9/9 tests, plist linted)
- [x] 1.4 — Job queue tools (submit_job / get_job_status / cancel_job; 13 new tests, suite at 52/52)
- [ ] 1.5 — Resolve Plex Funnel collision (carried forward from Phase 0)
- [ ] 1.6 — /capture endpoint (after 1.5)
- [ ] 1.7 — Day One MCP wiring (deferred from Phase 0.9)
- [ ] 1.8 — Memory edits + perennials (manual)
- [ ] 1.9 — Android HTTP Shortcut + iPad Shortcut (manual)
- [ ] 1.10 — Round-trip integration test

## Active subagents

(none — paused at human-gated blockers)

## Open questions for human

1. **Plex Funnel collision (blocks 1.5→1.6→1.9→1.10):** resolve by (a) distinct MagicDNS hostname e.g. `commonplace.tailb9faa9.ts.net`, (b) non-443 port on the existing plex-server host, or (c) move Plex. Recommendation: (a), since commonplace and Plex are independent services and MagicDNS is free.
2. **Bearer token (blocks 1.6):** generate now + store in macOS keychain under service name `commonplace-capture-bearer`, account `capture`?
3. **Day One MCP wiring (1.7):** need the Day One MCP server path — is it the `dayone` CLI or a separate MCP binary? Share the install location and I'll wire it into `~/.claude.json`.
4. **Memory + perennials (1.8):** this is your interactive work via the Claude memory tool + preferences panel; say when you've done it.
5. **Mid-phase commit?** suite is at 52/52 with 7 tracked files worth of real code — worth tagging a `phase-1-wave-1` checkpoint before the capture endpoint work? Low-risk, easy rollback. Say the word and I'll commit.

## Blocked tasks

(none)

## Recent completions

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
