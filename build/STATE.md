# Commonplace Build State

**Current phase:** Phase 6.C — Funnel path-routed on :443 live; **Accept-header 406 fixed via local ASGI middleware; end-to-end handshake via Funnel with SSE-only Accept returns 200**
**Phase 2 started:** 2026-04-15T14:45:00-04:00
**Phase 3 started:** 2026-04-15T17:25:00-04:00
**Phase 3 completed:** 2026-04-15T18:00:00-04:00
**Phase 4 started:** 2026-04-16T10:00:00-04:00
**Phase 4 wave 2 committed:** 2026-04-16T (commit 5e06102, tag phase-4-wave-2-complete)
**Phase 4.6 prep committed:** 2026-04-16T (commit 1c3934d — correct_judge + custom-instructions draft)
**Phase 5b+5c shipped:** 2026-04-16T (parallel waves — movies/TV via TMDB + book enrichment via OL/GB)
**Phase 5b+5c enqueued:** 2026-04-16T15:45 (435 movie/TV jobs + 670 enrichment jobs; TMDB key confirmed in keychain)
**Phase 6 MCP exposure — path A shipped:** 2026-04-16T17:45 (`.mcp.json` + launchd plist for MCP server)
**Phase 6 MCP exposure — path A verified:** 2026-04-16T18:11 (MCP tools live in CLI; healthcheck 200 via mcp__commonplace__healthcheck)
**Phase 6 MCP exposure — auth shipped:** 2026-04-16T18:45 (URL-path secret; mounted at `/mcp/<token>`; bare `/mcp` 404; healthcheck stays public)
**Phase 6.C Funnel path-routed :443:** 2026-04-16T20:05 (coexists with Plex at `/`; end-to-end MCP initialize handshake verified via curl; claude.ai reaches server but 406s)
**Phase 6.C Accept-header fix shipped:** 2026-04-16T20:50 (`accept_middleware.py` — ASGI shim normalises inbound Accept on `/mcp/...` paths before SDK validator; 17 new tests; 836/836 suite; launchd kickstarted; curl with `Accept: text/event-stream` → 200 via Funnel)
**Last update:** 2026-04-16T20:55:00-04:00
**Status:** in_progress (6.C Funnel live; Accept-header unblocked; claude.ai UI expected to pick up fresh session on next connector toggle / new chat)

Phase 4 wave 2 complete. `correct` MCP tool extended with `target_type='judge_serendipity'` so users can tune ambient surfacing in-chat. 4.6 custom-instructions draft sitting at `build/4_6_custom_instructions_draft.md` for user to refine in claude.ai. Worker restarted (pid 96671); migration 0004 already applied; new HANDLERS keys (`ingest_audiobook`, `regenerate_profile`) registered. Audiobook scan enqueued 335 jobs. Library drain resumed: 44/98 books complete, 1 running, 52 queued; 1 historic Ollama-500 failure not retried. Audiobook jobs (335) sit behind library jobs in FIFO order — they'll process once library drain completes (metadata-only, no Ollama contention from audiobooks themselves). 5b (435 movie/TV) + 5c (670 enrichment) queued behind audiobooks; 5c is no-Ollama metadata, 5b hits TMDB API.

**Phase 6 context (updated).** MCP server is exposed to Claude Code via project-scope `.mcp.json` at repo root and survives reboots via `com.commonplace.mcp-server.plist` (launchd, `.venv/bin/python`). **Auth landed via URL-path secret:** server now mounts at `/mcp/<token>` where the 44-char urlsafe token lives in macOS keychain (service `commonplace-mcp-token`, account `mcp`). Bare `/mcp` returns 404. `/healthcheck` stays public. **Why URL-path instead of header:** claude.ai's custom-connector UI only supports OAuth 2.1 or bare URL — no custom-header field — and OAuth shim is overkill for a single-user system. Funnel's TLS encrypts the path in transit. Token rotation: `make mcp-token-rotate`. Path C (Funnel on port 8443 → claude.ai custom connector) is now unblocked. Port 443 stays on Plex/audiobookshelf Funnel (→127.0.0.1:13378) — do not disturb. Ports 8443 and 10000 free.

## Phase 2 progress

- [x] 2.1 — Embedding pipeline scaffold
- [x] 2.2 — `classify_book` skill
- [x] 2.3 — Bluesky historical handler (real backfill of 3,465 posts deferred — same Ollama contention)
- [x] 2.4 — Library watched-folder handler (import in flight)
- [x] 2.5 — Kindle scraper (real backfill of 18 books / 333 highlights deferred)
- [x] 2.6 — StoryGraph CSV importer — 619 rows landed (427 rated, avg 3.74)
- [x] 2.7 — Three book note skills
- [ ] 2.8 — Overnight book note batch — **blocked on library-import Ollama drain**

## Phase 3 progress

- [x] 3.1 — Article handler (Trafilatura==2.0.0; 9 tests; live wikipedia smoke 11 chunks; 266/266 suite)
- [x] 3.2 — Bluesky URL handler (atproto getPostThread depth=10/parent=10; 14 tests; <30-char reply filter; quote-post handling punted)
- [x] 3.3 — `summarize_capture` skill (Haiku; 30 tests; 3/3 live smoke; YAML-frontmatter+markdown format; quote verifier prevents fabrication)
- [x] 3.4 — YouTube handler (yt-dlp captions + quality heuristic + Whisper fallback; shared transcription.py; 36 tests; 361/361 suite)
- [x] 3.5 — Podcast handler (RSS discovery + Apple Podcasts API; feedparser podcast:transcript; Whisper fallback; 29 tests; 410/410)
- [x] 3.6 — Image handler (Tesseract 5.5.2; 15 tests; 3 input modes path/base64/URL; ocr_empty flag; image preserved)
- [x] 3.7 — Video file handler (ffmpeg + Whisper + keyframe OCR; 20 tests; Jaccard dedup 0.85; >2GB skip OCR; graceful degradation)
- [x] 3.8 — Capture dispatcher refactor (11 HANDLERS keys wired; kind→typed routing; text embeds inline; note→vault; unknown→fallback; 12 tests; 422/422)
- [x] 3.9 — Unified `search_commonplace` MCP tool (KNN via sqlite-vec; post-KNN filters; 5x overfetch; 16 tests; 438/438)

## Phase 4 progress

- [x] 4.1 — `regenerate_profile` skill (48 tests; live opus 3/3; directive preservation byte-for-byte; prompt tightened re: inbox→inferred)
- [x] 4.3 — `correct` MCP tool (33 tests; atomic writes; profile + book targets)
- [x] 4.4 — `judge_serendipity` skill (52 tests; live haiku 6/6; Haiku code-fence discovered + tolerance helper added)
- [x] 4.2 — Profile regen worker handler + monthly launchd cron (30 tests; `_invoke_skill` testing seam; plutil clean; corpus sampler covers both `kindle` and `kindle_highlight` content_types)
- [x] 4.5 — `surface` MCP tool (two-pass filter; uses judge's `strip_code_fences` tolerance helper)
- [x] 4.3+ — `correct` extended with `judge_serendipity` target (14 new tests, 680/680 suite, ruff clean, commit 1c3934d)
- [/] 4.6 — Custom instructions for ambient surfacing trigger — **DRAFT delivered** at `build/4_6_custom_instructions_draft.md`; user to refine in claude.ai chat
- [ ] 4.7 — Real corpus-driven testing + judge prompt iteration (depends on library drain + Kindle + Bluesky backfills)

## Phase 5a progress (pulled forward from deferred Phase 5)

- [x] 5a — Audiobookshelf filesystem handler shipped. 40 new tests (28 handler + 12 scanner), 607/607 suite green, ruff clean. Dry-run on real drive found 335 logical books. `mutagen==1.47.0` pinned. Migration 0004 adds `audiobook_path` + `narrator` columns to `documents`. `ingest_audiobook` registered in worker HANDLERS. Jaccard 0.70 fuzzy merge against `storygraph_entry`. **335 jobs enqueued 2026-04-16T12:30.**

## Phase 5b progress — Movies + TV via TMDB

- [x] 5b — Filesystem walker + TMDB enrichment. 76 new tests (parser 20 + TMDB client 24 + handler 12 + scanner 20). Migration 0005 adds `media_type`, `release_year`, `season_count`, `director`, `genres`, `plot`, `tmdb_id`, `filesystem_path` to `documents`. `parse-torrent-title==2.8.2` pinned. `ingest_movie` + `ingest_tv` registered. Dry-run on real drive found 374 movies + 61 TV = 435 items, 0 unparseable. **ENQUEUED 2026-04-16T15:45** — TMDB key present in keychain (`commonplace-tmdb-api-key`/`tmdb`). 435 jobs queued behind library drain + audiobooks + enrichment in FIFO order.

## Phase 5c progress — Book enrichment via Open Library + Google Books

- [x] 5c — Public-data enrichment handler. 52 new tests (OL 14 + GB 18 + handler 14 + scanner 8). Migration 0006 adds `description`, `subjects`, `first_published_year`, `isbn`, `enrichment_source`, `enriched_at` to `documents`. No API keys required. `ingest_book_enrichment` registered. Google Books calls cached at `~/.cache/commonplace/google_books/<key>.json` to protect 1000 req/day anonymous quota. Dry-run: 670 eligible docs (619 storygraph + 41 book + 10 kindle_book + 0 audiobook since those 335 jobs haven't drained yet). **ENQUEUED 2026-04-16T15:42–15:45** — 670 jobs queued behind library drain + audiobooks in FIFO order.

## Phase 6 progress — MCP exposure

- [x] 6.A — Claude Code CLI integration. `.mcp.json` created at repo root with `commonplace` → `http://127.0.0.1:8765/mcp`. Fresh CLI started from `/Users/cameronlewis/code/commonplace-code` will prompt once to approve project-scope config, then expose 7 tools: `healthcheck`, `search_commonplace`, `surface`, `correct`, `submit_job`, `get_job_status`, `cancel_job`.
- [x] 6.launchd — `com.commonplace.mcp-server.plist` installed at `~/Library/LaunchAgents/` and loaded. Uses `.venv/bin/python -m commonplace_server`. KeepAlive + RunAtLoad. Env: `COMMONPLACE_DB_PATH`, `COMMONPLACE_HOST=127.0.0.1`, `COMMONPLACE_PORT=8765`. Logs at `~/Library/Logs/commonplace-mcp-server.{out,err}.log`. Previous hand-started PID 72399 killed cleanly before load.
- [ ] 6.B — Claude Desktop path. Redundant once 6.C works; skipping.
- [x] 6.C — claude.ai web via Tailscale Funnel on port 443 (path-routed). **Funnel + Accept-header fix both shipped.** Local ASGI middleware (`commonplace_server/accept_middleware.py`) normalises inbound `Accept` on `/mcp/...` paths to `application/json, text/event-stream` before the SDK's strict validator. `/healthcheck` + `/capture` untouched. 17 new tests (including one regression guard that asserts upstream still 406s without the shim — alerts us to remove the workaround once python-sdk #2349 merges). Full suite 836/836, ruff clean. End-to-end curl with claude.ai's header shape returns 200 through Funnel. Config: `tailscale funnel --bg --https=443 --set-path=/mcp http://127.0.0.1:8765/mcp` coexists with existing `/` → Plex Funnel (most-specific-path-wins routing via Go ServeMux under the hood). Plex unaffected (verified). Full MCP `initialize` handshake verified via curl with spec-compliant Accept header: `POST https://plex-server.tailb9faa9.ts.net/mcp/<token>` → 200 OK + server capabilities. **Connector URL format:** `https://plex-server.tailb9faa9.ts.net/mcp/<token>` (no port, no trailing slash). **:8443 Funnel taken down** — claude.ai client silently rewrites non-443 ports. Pasted URL in claude.ai connector config; claude.ai backend (160.79.106.35) now reaches server but every POST 406s.
- [x] 6.auth — URL-path secret. 11 new tests (819/819 suite, ruff clean). `commonplace_server/mcp_token.py` resolver (env var → keychain). `scripts/init_mcp_token.py` (idempotent) + `scripts/rotate_mcp_token.py` wired as `make mcp-token-init` / `make mcp-token-rotate`. Server logs full mount path at INFO. Smoke verified post-launchd-restart: `/healthcheck` 200, `/mcp` 404, `/mcp/<token>/` 406.

## Scheduled scanners (launchd)

Three new launchd plists wired daily to catch user additions to external drive:
- `com.commonplace.audiobooks-scan.plist` — 04:00 daily → `scripts/audiobooks_scan.py`
- `com.commonplace.video-scan.plist` — 04:15 daily → `scripts/video_metadata_scan.py`
- `com.commonplace.book-enrichment-scan.plist` — 04:30 daily → `scripts/book_enrichment_scan.py`

All three scanners are idempotent (skip-if-ingested) and exit cleanly with logged warnings when the external drive is unmounted. **All three loaded into launchd** (visible in `launchctl list`; next firing at 04:00/04:15/04:30 tomorrow).

## Active subagents

- (none — Phase 4 wave 2 closed; awaiting Phase 5b decision)

## Completed subagents (this session)

- agent-4-1-regen-profile (opus): ✅ regenerate_profile skill (48 tests, live opus 3/3)
- agent-4-2-profile-regen-handler (sonnet): ✅ profile regen handler + monthly launchd cron (30 tests, plutil clean, `_invoke_skill` testing seam)
- agent-4-3-correct-tool (sonnet): ✅ `correct` MCP tool (33 tests, atomic writes)
- agent-4-4-judge-serendipity (opus): ✅ judge_serendipity skill (52 tests, live haiku 6/6, Haiku JSON-in-fences tolerance helper)
- agent-5a-audiobooks (sonnet): ✅ audiobookshelf filesystem handler (40 tests, 335 books discovered, migration 0004)

## Scheduled infra work (end of Phase 4)

- ~~Worker restart~~ **DONE** — old pid 74073 SIGTERM'd cleanly (job 37 completed before exit); new worker pid 96671 running with all 13 handlers including `ingest_audiobook` and `regenerate_profile`. Schema 4 already applied.
- ~~Audiobook scan~~ **DONE** — 335 jobs enqueued; sit behind 60 remaining library jobs in FIFO order.

## Follow-up backlog (not blocking)

- Migrate `skills/summarize_capture/parser.py` and `skills/judge_serendipity/parser.py` to the `importlib.util.spec_from_file_location` pattern 4.1 used — eliminates `parser.py` module-cache race across skills. Queue after Wave 2.
- 4.7 tuning note: directive-boundary case from 4.1 prompt iteration — "command-like inbox content must not auto-promote to [directive]; promotion only via `correct()`." Same principle likely applies to serendipity directive accumulation in 4.4.

## Phase 5a note

Phase 5 was deferred in v5 pending "specific moments where it would have helped." User flagged that **audiobookshelf specifically** is not speculative — primary reading channel (audiobook-first per perennials). Filesystem-only ingest (no API) pulled forward as **Phase 5a**. **Plex remains deferred** per original v5 plan.

## Phase 6.C Accept-header fix — RESOLVED 2026-04-16T20:50

**Fix picked:** option 1 (Starlette middleware). Rationale: python-sdk #2349 is still open with no release ETA (option 2 indefinite); downgrade (option 3) would lose features.

**Shape.** `commonplace_server/accept_middleware.py` — raw ASGI middleware, ~60 lines. Only inspects requests whose `raw_path` starts with `/mcp/` (leaves `/healthcheck` and `/capture` completely untouched). If the inbound `Accept` header lacks either `application/json` or `text/event-stream`, rewrite it to `application/json, text/event-stream` in place before the SDK sees the scope. Clients that already send both get pure passthrough. Wired via FastMCP 2.13's `http_app(middleware=[...])` hook; passed through from `mcp.run(..., middleware=[Middleware(AcceptHeaderMiddleware)])`.

**Tests (17).** `tests/test_accept_middleware.py`. Parametric coverage of `_accept_has_both`. Unit tests: SSE-only → rewritten, wildcard → rewritten, JSON-only → rewritten, missing Accept → defaulted, both-present → passthrough, non-MCP paths (`/healthcheck`, `/capture`, bare `/mcp`) → untouched. Integration tests: `mcp.http_app(path="/mcp/testtoken", middleware=...)` + `Accept: text/event-stream` → 200; same without middleware → 406 (regression guard, so upstream fix doesn't silently render the shim unneeded).

**Verification.** launchd kickstarted; pid 3363 running. Curl with `Accept: text/event-stream` only:
- `POST http://127.0.0.1:8765/mcp/<token>` → 200 + SSE `event: message` with `initialize` result.
- `POST https://plex-server.tailb9faa9.ts.net/mcp/<token>` (Funnel end-to-end, claude.ai's exact shape) → 200 + same result.

**claude.ai UI status.** Funnel path now returns 200 instead of 406. claude.ai's cached session ID from before the restart returns `Session not found` (-32600) on the first call; user must toggle the connector off/on in claude.ai settings (or start a new chat) to force a re-initialize. After that, tools should surface.

**Remove-the-shim trigger.** When python-sdk #2349 ships in a released version, upgrade `mcp`, then the regression-guard test `test_integration_sse_only_without_middleware_still_406` will start failing — at that point, delete `accept_middleware.py`, remove the `middleware=` arg in `server.main()`, drop the test file.

---

## Phase 6.C blocker — Accept-header 406 (HISTORICAL)

**Symptom.** claude.ai's backend POSTs to `https://plex-server.tailb9faa9.ts.net/mcp/<token>` and every request returns `406 Not Acceptable: Client must accept both application/json and text/event-stream`. claude.ai UI reports "Couldn't reach the MCP server" with ref `ofid_afee5c5fe965c375`.

**Root cause (confirmed by curl repro).** claude.ai sends `Accept: text/event-stream` only — not the spec-required `application/json, text/event-stream`. MCP python-sdk's `_validate_accept_header()` uses strict AND logic and rejects. Attested in [python-sdk #2349](https://github.com/modelcontextprotocol/python-sdk/issues/2349). Related wildcard case fixed in [PR #2442](https://github.com/modelcontextprotocol/python-sdk/pull/2442) merged for v1.20.0, but the `text/event-stream`-only case (#2349) appears still open.

**Reproduction (curl).**
```bash
TOKEN=$(security find-generic-password -s commonplace-mcp-token -w)
curl -sS -X POST "https://plex-server.tailb9faa9.ts.net/mcp/${TOKEN}" \
  -H "Accept: text/event-stream" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0.1"}}}'
# → HTTP 406, error: "Client must accept both application/json and text/event-stream"
```
With `-H "Accept: application/json, text/event-stream"` the same POST returns 200 + full server capabilities.

**Current deps.** `mcp==1.27.0`, `fastmcp==2.13.1` in `.venv`.

**Fix options (pick one next session).**
1. **Starlette middleware** (fastest, local workaround): rewrite incoming `Accept: text/event-stream` and `Accept: */*` to `application/json, text/event-stream` before FastMCP's validator sees the request. Wire into `commonplace_server/server.py` via FastMCP's `http_app()` hook. No upstream dependency.
2. **Upgrade `mcp`** if/when #2349 merges a fix in a release past 1.27.0. Check release notes. Simpler but dependent.
3. **Downgrade `mcp`** to pre-strict-validation era — unclear if such a version exists without losing other features; not recommended.

**Expected outcome of fix.** Once Accept validation is relaxed, claude.ai POSTs complete the MCP handshake and the connector surfaces 7 tools (healthcheck, search_commonplace, surface, correct, submit_job, get_job_status, cancel_job) in claude.ai chats. Claude Code CLI (already working) is unaffected since it sends the spec-compliant Accept header.

## Open questions for human

1. **Profile `current.md` bootstrap.** User is seeding this on the side. Regen handler (4.2) will need to handle both existing-current-md and first-run cases.
2. ~~**MCP server auth approach.**~~ **RESOLVED 2026-04-16.** Settled on URL-path secret (option not in original list — emerged after research found claude.ai UI doesn't support custom headers). User confirmed claude.ai web + iOS app are non-negotiable, ruling out Tailscale-only. OAuth shim deemed overkill for single user. Implemented in 6.auth.

## Blocked tasks

- 2.8 overnight book note batch — waiting on library Ollama drain (not rolled into Phase 4; holds for batch dispatch once drain completes)
- 4.7 corpus judge tuning — waiting on library drain + Kindle + Bluesky backfills to complete (corpus-dependent quality)

## Deferred (will run after library drain)

- Kindle real backfill (18 books, 333 highlights — green-lit by user 2026-04-16)
- Bluesky real backfill (3,465 posts — green-lit by user 2026-04-16; app password rotation disregarded)

## Recent completions

- 20:30 (2026-04-16) — **Phase 6.C Funnel reshuffled to path-routed :443.** `:8443` Funnel taken down (claude.ai client silently rewrites to 443 regardless — confirmed by zero inbound log entries from its backend IP during :8443 test). Added `tailscale funnel --bg --https=443 --set-path=/mcp http://127.0.0.1:8765/mcp` which coexists with existing `/` → Plex Funnel via Go ServeMux most-specific-path-wins. Plex verified untouched (curl `/` → 200). MCP verified end-to-end: curl `POST https://plex-server.tailb9faa9.ts.net/mcp/<token>` with spec-compliant Accept header → full MCP initialize response. User pasted new URL in claude.ai connector; claude.ai backend (160.79.106.35) now reaches server but every POST 406s — see **Phase 6.C blocker** above for root cause and fix options.
- 18:45 (2026-04-16) — **Phase 6.auth shipped.** URL-path secret auth on MCP server. 11 new tests (819/819). `commonplace_server/mcp_token.py` reads token from env var → macOS keychain (service `commonplace-mcp-token`, account `mcp`, mirrors TMDB pattern). Server mounts at `/mcp/<token>`; bare `/mcp` 404; `/healthcheck` public. `make mcp-token-init` (idempotent) + `make mcp-token-rotate` (regen + kick launchd). `.mcp.json` regenerated with token-suffixed URL by both scripts. Launchd service kickstarted (pid 1977); smoke verified `/healthcheck` 200, `/mcp` 404, `/mcp/<token>/` 406. Discovery: FastMCP 2.13.1's `mcp.run(path=...)` parameter handles dynamic mount paths cleanly — no middleware shim needed. **Path C now unblocked.** User must restart Claude Code CLI from repo root to reload `.mcp.json` and pick up MCP tools (they disconnected when the server restarted).
- 18:11 (2026-04-16) — **Phase 6 path A verified.** MCP tools loaded into Claude Code session from `.mcp.json`; `mcp__commonplace__healthcheck` returned 200 with `schema_version: 6` (confirms launchd-managed server is on `.venv/bin/python` with all 6 migrations applied). All 7 tools (`healthcheck`, `search_commonplace`, `surface`, `correct`, `submit_job`, `get_job_status`, `cancel_job`) callable from CLI.
- 15:45 (2026-04-16) — **Phase 5b + 5c enqueued.** 435 movie/TV jobs (`ingest_movie` 374 + `ingest_tv` 61) and 670 `ingest_book_enrichment` jobs queued. TMDB key confirmed in login keychain (service `commonplace-tmdb-api-key`, account `tmdb`). Three scanner plists (`audiobooks-scan`, `video-scan`, `book-enrichment-scan`) loaded into launchd at same window; daily firings begin tomorrow 04:00/04:15/04:30.
- 17:45 (2026-04-16) — **Phase 6 path A shipped.** `.mcp.json` written at repo root (project-scope; `commonplace` → `http://127.0.0.1:8765/mcp`). `com.commonplace.mcp-server.plist` installed in `~/Library/LaunchAgents/` (uses `.venv/bin/python`, KeepAlive, RunAtLoad). Hand-started PID 72399 stopped; launchd-managed process running and healthy (`/healthcheck` 200). User to restart CLI from repo root to pick up `.mcp.json`.
- 17:30 (2026-04-16) — Recon: confirmed `/mcp` is correct endpoint suffix, Funnel currently only on 443 (Plex), ports 8443/10000 free. MCP server was running hand-started (PPID 1, system Python) — no plist, no reboot persistence.
- 12:30 (2026-04-16) — Worker pid 74073 SIGTERM'd; finished job 37 cleanly; new worker pid 96671 started; audiobook scan enqueued 335 jobs (`ingest_audiobook`).
- 12:25 (2026-04-16) — `4.6 prep` commit (1c3934d): `correct(target_type='judge_serendipity')` + 14 new tests + custom-instructions draft. 680/680 suite, ruff clean.
- 11:40 (2026-04-16) — Phase 4 wave 2 + 5a tagged (commit 5e06102, `phase-4-wave-2-complete`): regen_profile + correct + judge_serendipity + profile-regen-handler + surface + audiobookshelf handler. 666/666 suite at tag time.
- 10:00 (2026-04-16) — Phase 4 opened. Pre-flight 438/438 green. Wave 1 dispatched (4.1 regen_profile skill w/ opus, 4.3 correct tool w/ sonnet, 4.4 judge_serendipity skill w/ opus). Kindle + Bluesky backfills deferred until library drain completes to avoid Ollama contention.
- 18:00 — **Phase 3 complete.** 438/438 tests, ruff clean. 9 tasks, 5 waves, ~35 min wall-clock. 181 new tests, 13 new files, 4 deps pinned.
- 17:58 — 3.9 search_commonplace complete (16 tests, 438/438; unified KNN search; MCP tool registered)
- 17:57 — 3.8 capture dispatcher complete (12 tests, 422/422; 11 HANDLERS keys: noop, capture, ingest_library/bluesky/kindle/article/youtube/podcast/image/video, bluesky_url)
- 17:54 — Wave 3 dispatched (3.8 dispatcher + 3.9 search_commonplace); all 7 handlers complete; 410/410 verified
- 17:53 — 3.5 podcast handler complete (29 tests, 410/410 suite; RSS + Apple Podcasts + Whisper fallback)
- 17:51 — 3.7 video handler complete (20 tests; 3 podcast test failures from parallel write race — resolved)
- 17:46 — Wave 2b dispatched (3.5 podcast + 3.7 video; transcription.py now available)
- 17:45 — 3.4 YouTube handler complete (36 tests, 361/361; shared transcription.py created)
- 17:42 — 3.6 image handler complete (15 tests, 333/333 suite; tesseract 5.5.2 live OCR verified)
- 17:37 — Phase 3 wave 1 complete (3.1, 3.2, 3.3); 53 new tests across the wave (310/310 suite); waiting on user for wave 2 deps decisions
- 17:37 — 3.3 summarize_capture skill complete (30 offline tests, 3/3 live haiku smoke, no fabricated quotes)
- 17:32 — 3.2 bluesky URL handler complete (14 tests, 280/280 suite; quote-post + rate-limit punted to later hardening)
- 17:29 — 3.1 article handler complete (trafilatura==2.0.0; 9 tests; wikipedia live smoke 11 chunks; 266/266 suite)
- 17:25 — Phase 3 opened; pre-flight 257/257 green; wave 1 (3.1, 3.2, 3.3) dispatched
- 17:18 — 2.5 Kindle dry-run live (18 books, 333 highlights via pycookiecheat)
- 17:15 — Library import kicked off (98 books enqueued; worker pid 74073 draining via Ollama)
- 17:10 — Phase 2 wave 1 tagged (commit 794dd1d, 258 tests, ruff+mypy clean)
- 17:05 — 2.3 Bluesky handler complete; 2.5 Kindle code complete
- 16:32 — StoryGraph import landed (619 rows)
- 16:30 — Calibre 9.7.0 confirmed installed
- 15:36 — 2.7 book note skills complete
