# Commonplace Build State

**Current phase:** Phase 2 — Ingestion
**Started:** 2026-04-15T14:45:00-04:00
**Last update:** 2026-04-15T15:02:00-04:00
**Status:** in_progress

2.1 embedding pipeline closed (129/129 tests, ruff+mypy clean, live Ollama returns 768d, sqlite-vec vec0 live). 2.2 still running. Handler wave dispatched: 2.4 library watcher, 2.6 StoryGraph CSV, 2.7 book note skills. 2.3 Bluesky and 2.5 Kindle held pending user credentials.

## Phase progress

- [x] 2.1 — Embedding pipeline scaffold (52 new tests; sqlite-vec 0.1.9, tiktoken 0.12.0)
- [x] 2.2 — `classify_book` skill (18 offline tests, 8/8 live smoke; invocation gotcha pinned)
- [x] 2.3 — Bluesky handler (19 tests, dry-run auth + 3,465 posts enumerated; atproto==0.0.65 pinned)
- [x] 2.4 — Library watched-folder handler (16 tests, 98 books scanned, epub smoke OK; calibre needed for 13 mobi/azw3)
- [x] 2.5 — Kindle scraper code (51 tests; blocked_on_cookies — awaiting non-encrypted export)
- [x] 2.6 — StoryGraph CSV importer — **619 rows landed** (427 rated, avg 3.74)
- [x] 2.7 — Three book note skills (16 tests, 3/3 smoke; SKILL.md hardened vs. haiku preamble)
- [ ] 2.8 — Overnight book note batch (blocked by 2.2, 2.4, 2.7)

## Active subagents

(none)

## Open questions for human

1. Rotate Bluesky app password (it was typed in chat; paste a new one and I'll rekey the keychain).
2. Kindle cookies: Cookie-Editor won't save an unencrypted export. Switch to "Get cookies.txt LOCALLY" (Chrome) or let me read Chrome cookies directly via `pycookiecheat` — your call.
3. Real Bluesky backfill (~3,465 posts, will run for a while with live Ollama embeds) — run now or after library-import finishes?

## Blocked tasks

(none currently — questions above are gating 2.3 and 2.5 only)

## Recent completions

- 17:05 — 2.3 Bluesky handler complete (19 tests; dry-run shows 3,465 posts ready; atproto==0.0.65 pinned)
- 17:05 — 2.5 Kindle scraper code complete (51 tests; blocked_on_cookies pending non-encrypted export)
- 16:32 — StoryGraph import landed (619 rows from user CSV)
- 16:30 — Calibre 9.7.0 confirmed installed (ebook-convert on PATH)
- 15:36 — 2.7 book note skills complete (16 tests, 3/3 live smoke; Phase 2 wave 1 code-complete)
- 15:28 — 2.4 library handler complete (188/188 suite, 98 books scanned, epub smoke 61 chunks; calibre needed for mobi/azw3)
- 15:20 — 2.6 StoryGraph importer complete (27 tests, migration 0003 applies; real CSV pending from user)
- 15:14 — 2.2 classify_book skill complete (18 tests, 8/8 smoke; claude -p invocation shape pinned)
- 15:00 — 2.1 embedding pipeline validated (129/129, migration idempotent, vec0 live, Ollama 768d)
- 14:42 — ADR-0005 accepted
- 14:40 — Phase 2 opened; pre-flight green
- 14:35 — Phase 1 closed (77/77)
