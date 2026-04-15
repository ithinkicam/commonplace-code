# Commonplace Build State

**Current phase:** Phase 0 — Setup & Prerequisites
**Started:** 2026-04-15T09:15:00-04:00
**Last update:** 2026-04-15T10:30:00-04:00
**Status:** complete

Phase 0 complete. Toolchain, network, and external integrations pinned.
Ready to begin Phase 1 (Foundation: FastMCP + worker skeleton).

## Phase progress

- [x] 0.1 — Login password set; sudo functional
- [x] 0.2 — Homebrew toolchain (gh, python@3.12, git) operational
- [x] 0.3 — Claude Code 2.1.109 pinned (build/pins/claude-code.md)
- [x] 0.4 — Ollama 0.20.7 + nomic-embed-text pinned (build/pins/ollama.md)
- [x] 0.5 — Tailscale (MAS 1.96.2) + CLI shim; tailnet facts pinned (build/pins/tailscale.md)
- [x] 0.6 — Tailscale installed on Android phone
- [x] 0.7 — Tailscale installed on iPad
- [x] 0.8 — Google Drive for Desktop syncing books folder (build/pins/drive-and-dayone.md)
- [x] 0.9 — Day One v2026.8 present (MCP wiring deferred to Phase 1)
- [x] 0.10 — Dedicated service user skipped (ADR-0002)

## Active subagents

(none)

## Open questions for human

(none)

## Blocked tasks

(none)

## Recent completions

- 10:30 — ADR-0002 written: skip dedicated service user (tailnet + FileVault + interactive trust)
- 10:15 — Day One + Drive pins written
- 10:00 — Tailscale CLI shim working via MAS app bundle
- 09:45 — Claude Code + Ollama pins written
- 09:30 — Homebrew toolchain ready (x86_64 under Rosetta)
- 09:15 — Phase 0 opened

## Carried forward to Phase 1

- Plex Funnel collision at plex-server.tailb9faa9.ts.net — resolve port/path scheme before /capture lands
- Day One MCP wiring happens with Claude Code MCP config setup
- Keychain-based secret storage (locked decision from plan v5)
