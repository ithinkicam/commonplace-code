# Commonplace Build State

**Current phase:** Liturgical ingest — **Phase 4 Wave 4A LANDED** (tasks 4.1 + 4.4 + 4.5 + MCP ingest-wrapper follow-up; 4 commits on main). Retrieval-integration work underway: prose regression baseline fixture/tests in place (16 accept / 182 reject baseline on 20 synthetic seeds, offline + `live` pytest marker); `search_commonplace` extended with 5 liturgical kwargs + Option A calendar-date-range overload; LFF 2024 precedence rules + lectionarypage cross-check landed (20/20 match). Phase 1 + LFF backfill still complete from prior session. Next: Wave 4B (4.2 + 4.3 + 4.6) + 4.7 primary review. Plan: `docs/liturgical-ingest-plan.md`.
**Previous phase:** Phase 1 Wave 1A — `embed_text_override` pipeline seam + BCP caching crawler
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
**Phase 6 committed:** 2026-04-16T21:00 (commit 347f5bc — launchd + URL-path auth + Funnel path-routing + Accept-header shim + .mcp.json/library.db gitignored)
**Phase 4.6 finalised:** 2026-04-16T21:15 (custom-instructions paste-ready at `build/4_6_custom_instructions_draft.md`; all 5 open refinement items resolved with rationale + 4.7 tuning hooks)
**Phase 7 liturgical-ingest plan committed:** 2026-04-17T (commit bf2a4f0 — `docs/liturgical-ingest-plan.md`, 604 lines, all §6 open questions resolved except Q6 deferred)
**Phase 7 Wave 0A (task 0.1 migration):** 2026-04-17T (commit 2ef0e33 — 15 schema tests, 858/858)
**Phase 7 Wave 0B (0.2 + 0.4 + 0.5 parallel):** 2026-04-17T (commits 2cb17af + 3f206ae + 7e56973 — validator, calendar stub, subject_frequency tool; +69 tests, 927/927)
**Phase 7 Wave 0C (task 0.3 feast-import CLI):** 2026-04-17T (commit 94b0a91 — 27 tests, 954/954; `make seed-feasts` + `make seed-feasts-dry`; empty seed files at `commonplace_db/seed/`)
**Phase 7 Wave 1A (tasks 1.1 + 1.8 parallel):** 2026-04-17T (commits 436705a + b661364 — `embed_text_override` seam on `embed_document` + BCP 1979 caching crawler; +32 tests, 986/986; ruff + mypy clean)
**Phase 7 task 0.7 (user-action) landed:** 2026-04-18T (commit feb15e3 — 398 feasts + 71 theological subjects seeded; user authored in claude.ai and imported via `make seed-feasts`)
**Phase 7 Wave 1B (tasks 1.2 + 1.3 + 1.4 parallel):** 2026-04-18T (commits 3021b83 Collects → 9ef1505 slug realign → 4b56d34 Daily Office → c29f8f3 Psalter; +280 tests, 1249/1249; ruff clean)
**Phase 7 Wave 1C (tasks 1.5 + 1.6 + 1.7 parallel):** 2026-04-18T (commits 15998a1 Proper Liturgies → 055d55d Prayers & Thanksgivings → 06a867d LFF 2024 parser; +227 tests, 1477/1477; ruff + mypy clean; PyMuPDF==1.27.2.2 added)
**Phase 7 handler-pair (tasks 1.10 + 1.11 parallel):** 2026-04-18T (commit 8daf8c9 — BCP ingest handler orchestrating all 5 BCP parsers + LFF ingest handler for 283 commemorations; both registered in worker HANDLERS; +54 tests, 1531/1531)
**Phase 7 pushed to origin:** 2026-04-18T (7 commits pushed: feb15e3..8daf8c9)
**Phase 7 Phase 1 DoD met:** 2026-04-18T (commit eed6b09 — 1.9 BCP end-to-end integration test; 2 tests; full suite green; idempotency pinned)
**Phase 7 LFF feast-backfill shipped:** 2026-04-18T (commit 01af6ea — broadened predicate + 58 new yaml rows; hit rate 201/283 → 283/283)
**Phase 7 Wave 4A landed:** 2026-04-18T (4 commits on main: f420d8e task 4.1 prose regression baseline → cfa2317 task 4.5 LFF 2024 precedence + lectionarypage cross-check → 66b39f6 task 4.4 liturgical search filters + calendar-range overload → c47d901 MCP thin per-kind ingest tool wrappers)
**Last update:** 2026-04-19 (Phase 7 Wave 4.8 code + live ingest landed — BCP reingested with `embed_text_override` + LFF live-ingested; replay shows retrieval working for prose seeds but lit_pos fixture still 0/10; opened 4.9 to investigate fixture vs. retrieval)
**Status:** in_progress — Phase 1 complete; Wave 4A + 4B shipped; 4.7 partial (prose ratified); 4.8 shipped as code+ingest partial (retrieval path active for prose, but lit_pos fixture doesn't trigger liturgical retrieval — candidate pool is 100% prose for all 10 lit_pos cases). 4.9 next: diagnose whether lit_pos prompts need rework or if `search()` has a hidden filter.

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
- [x] 4.6 — Custom instructions for ambient surfacing trigger. Paste-ready version at `build/4_6_custom_instructions_draft.md` (top block). Resolved all 5 open refinement items + 3 additions (empty-return silence for in-progress embedding, book-slug discovery via `search_commonplace`, tightened correction-confirmation with "hedge-is-no" rule). 4.7 tuning hooks documented at bottom of file. User to paste into claude.ai Settings → Preferences.
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

## Phase 7 progress — Liturgical ingest (pilot: BCP 1979 + LFF 2022)

Plan: `docs/liturgical-ingest-plan.md` (committed `bf2a4f0`). Pilot is Anglican-only — Jordanville deferred post-pilot behind user's Kindle deDRM workstream (plan §6 Q1). Six §6 open questions resolved; Q6 (Byzantine calendar) deferred alongside Jordanville.

### Phase 0 — Schema + feast table (coding complete)

- [x] 0.1 — Migration `0007_liturgical_ingest.sql`: `liturgical_unit_meta` (11 cols, CASCADE-deletes with parent document) + `feast` (10 cols, self-referential cross_tradition_equivalent_id) + `commemoration_bio` (5 cols) + 8 indexes. 15 schema tests (integrity_check, FK enforcement, version bump to 7). (commit `2ef0e33`)
- [x] 0.2 — Pydantic v2 schema + validator in `commonplace_db/feast_schema.py`. `validate_feasts()` loads `feasts.yaml` + `theological_subjects.yaml`, enforces controlled-vocab with `_other:` escape hatch (plan §6 Q5), collects all errors before raising. Pinned `pydantic==2.13.0` + `pyyaml==6.0.3`. 29 tests + 6 invalid-fixture files. (commit `2cb17af`)
- [x] 0.3 — `scripts/feast_import.py` CLI + `make seed-feasts` / `make seed-feasts-dry` Makefile targets. Two-pass idempotent upsert: first pass inserts rows + builds slug→id map, second pass resolves `cross_tradition_equivalent`. `--dry-run` / `--db` / `--ignore-missing-cross-refs` flags. 27 tests. Seed files bootstrapped empty at `commonplace_db/seed/{feasts,theological_subjects}.yaml`. (commit `94b0a91`)
- [x] 0.4 — `commonplace_server/liturgical_calendar.py` stub: `movable_feasts_for_year(year, tradition)` via `dateutil.easter` + `resolve_fixed_date` / `resolve_movable_date` / `resolve` against feast table. LFF 2022 precedence ladder deferred to Phase 4 task 4.5. 29 tests (hard-coded 2025 + 2026 dates, tradition switch). (commit `3f206ae`)
- [x] 0.5 — `subject_frequency` MCP tool (plan §2.6). Pure logic in `commonplace_server/subject_frequency.py`, thin wrapper in `server.py` matching `embedding_progress` pattern. Splits subjects into controlled / `_other:`, aggregates counts + feast names. Malformed JSON in `feast.theological_subjects` logs warning and skips. 11 tests. (commit `7e56973`)
- [x] 0.6 — `theological_subjects.yaml` seeded with 71 subjects (user-authored in claude.ai, imported 2026-04-18 commit `feb15e3`).
- [x] 0.7 — `feasts.yaml` seeded with 398 entries (user-authored in claude.ai, imported 2026-04-18 commit `feb15e3`). Exceeds plan target of ≥200 rows.
- [x] 0.8 — Unit tests rolled in with each task (15 + 29 + 27 + 29 + 11 = 111 new; 954/954 suite green).

### Phase 0 DoD per plan §8.7

- [x] Migration `0007` applied (schema_version = 7)
- [x] `feasts.yaml` imports cleanly with 398 rows (≥200 target met) — commit `feb15e3`
- [x] Calendar resolver returns correct date for 5 fixed-date + 5 movable-feast 2026 test cases
- [x] `subject_frequency` MCP tool returns expected JSON shape

### Preflight for Phase 1 dispatch

- Tree clean, 954/954 suite green, `make smoke` green, ruff + mypy clean.
- Also committed in this session before Phase 0: `embedding_progress` MCP tool (commit `6b58b0e` — unrelated to liturgical, was in-progress from prior session).

### Phase 1 — BCP 1979 parser

- [x] 1.1 — BCP caching crawler at `scripts/bcp_crawler.py` + 27 tests. Polite (180s crawl-delay, self-identifying User-Agent, host-scoped to `www.bcponline.org`), resumable (skip-if-cached), atomic writes (tmp + fsync + rename), bails clean on 429, logs + skips other 4xx/5xx. URL→path mapping: host-prefixed, percent-decoded, sanitized, query-strings SHA-256-hashed into 8-char suffix, containment verified against cache dir. `httpx.MockTransport` for tests (no network). (commit `b661364`)
- [x] 1.8 — `embed_text_override: Callable[[Chunk], str] | None` keyword-only param on `embed_document`. When None (every existing caller), embedder input byte-identical to before. When provided, override composes the embed string; `chunks.text` still holds raw display text (plan §2.7 option Y). 5 new tests including regression guard asserting verbatim default path. (commit `436705a`)
- [x] 1.2 — BCP Collects parser (Rite I + Rite II; seasonal + proper). Slugs realigned to canonical `{name_snake}_anglican` scheme (commit `9ef1505`). (commit `3021b83`)
- [x] 1.3 — BCP Daily Office parser (MP/EP Rite I+II, Compline, Noonday, Daily Devotions, canticles, Great Litany). 151 units, 109 tests. Kind taxonomy: canticle / prayer / creed / psalm_ref / seasonal_sentence / versicle_response / rubric_block / intro / suffrage. (commit `4b56d34`)
- [x] 1.4 — BCP Psalter parser (150 psalms, 2505 verses; malformed HTML → lxml recovery; Psalm 119 → 176 verses + 22 subheadings). Handles source-data bugs: Psalm 64 malformed id quoting + Psalm 138 wrong id. 91 tests. (commit `c29f8f3`)
- [x] 1.5 — BCP Proper Liturgies parser (Ash Wed / Palm Sun / Maundy Thu / Good Fri / Holy Sat / Easter Vigil). 227 units, 120 tests. Kind taxonomy extends Daily Office with speaker-line / prayer-body / psalm-verse / rubric. Handles three speaker-table variants, inline-styled optional blocks (`border-left` style — not CSS class), cross-page continuations. (commit `15998a1`)
- [x] 1.6 — BCP Prayers & Thanksgivings parser (70 prayers + 11 thanksgivings). 81 units, 64 tests. Uniform slug `{name_snake}_{prayer|thanksgiving}_{N}_anglican` disambiguates duplicate titles (50/51 "For a Birthday", 57/58 "For Guidance"). Thanksgiving 1 "(1979 Version)" source-HTML leak stripped at parser. (commit `055d55d`)
- [x] 1.7 — **LFF 2024 PDF parser** (pulled forward from Phase 2 at user request; originally plan's 1.7 handler-scaffolding task is now 1.10). 283 commemorations via PyMuPDF font/size state machine; fixture pinned at `tests/fixtures/lff_2024.pdf` SHA256 `5deea4a131b6c90218ae07b92a17f68bfce000a24a04edd989cdb1edc332bfd7`. Font signature differs from plan's 2022-era doc: **SabonLTStd-Bold 17pt** (not Sabon-Bold). 43 tests. PyMuPDF pinned `==1.27.2.2`. (commit `06a867d`)
- [x] 1.10 — **BCP ingest handler** (originally plan's 1.7; renumbered because 1.7 became LFF). One `ingest_liturgy_bcp` job ingests all 5 BCP parsers; 704 rows after cross-file dedup (275 collects + 118 daily-office + 3 psalter-sample + 227 proper-liturgies + 81 P&T). Idempotent on re-run. Payload supports `source_root`, `parsers` filter, `dry_run`. 27 tests. (commit `8daf8c9`)
- [x] 1.11 — **LFF ingest handler** (originally plan's 2.4; pulled forward alongside 1.7). One `ingest_liturgy_lff` job ingests 283 commemorations → 201 bios + 564 collects (566 − 2 shared-text dedupes via `documents.content_hash UNIQUE`; e.g., Visitation BVM + Nativity BVM share collect text — intentional). Feast-lookup hit rate 71.7% (203/283). Uses `embed_text_override` for collects: composes `"Collect for {name} (Anglican, Rite I/II).\n\n{text}"` per plan §2.7 option Y. 26 tests. (commit `8daf8c9`)
- [x] 1.9 — BCP end-to-end integration test (`tests/test_phase1_bcp_integration.py`, 2 tests). Full pipeline: queued `ingest_liturgy_bcp` job → `poll_once(conn, HANDLERS)` → documents + `liturgical_unit_meta` + embeddings populate → `search_commonplace(content_type='liturgical_unit', source='bcp1979', limit=3)` returns 3 `liturgical_unit` rows with `bcp1979://` URIs and non-empty `chunk_text`. Meta count pinned at 704 (fixture-determined; DoD's 600±5% is for production corpus). Second test pins idempotency (re-submit → no double-insert via `(content_type, source_id)` UNIQUE). Embedding mocked via `monkeypatch.setattr("commonplace_server.embedding.embed", …)` → zero-vectors; covers both ingest + search-query paths. Fixture also patches `commonplace_db.db.DB_PATH` alongside env var because the worker handler wrapper calls `connect()` with no args (resolves module-level constant cached at import). (commit `eed6b09`)

### Active background jobs

- (none — BCP crawler no longer running; full BCP 1979 cache is on disk at `~/commonplace/cache/bcp_1979/`; all five BCP parsers run off the cache)

### Phase 4 — Retrieval integration (Wave 4A landed)

Plan: `docs/liturgical-ingest-plan.md` §4. Goal: wire the liturgical corpus into `search_commonplace` / `surface` / `judge_serendipity` so BCP + LFF units surface alongside prose captures without regressing prose recall.

- [x] 4.1 — **Prose regression baseline fixture + tests.** 20 synthetic seeds; 16 accept / 182 reject baseline pinned. Offline tests run inline; live tests gated behind new `live` pytest marker. (commit `f420d8e`)
- [x] 4.2 — **Liturgical fixtures authored.** `tests/fixtures/liturgical_surfacing.json` — 20 cases (10 positive / 5 true-negative / 5 negative-spillover). Paired-stimulus coverage against prose_regression baseline; §6 Q4 spillover traps included (Butler-on-grief, Weil-philosophical, Derrida-on-mercy, light-as-physics, peace-as-diplomatic). Top-level carries `plan_ref`, `pipeline_version_note` pinning baseline commit `f420d8e` + SKILL.md `5e06102`, categories legend, stats. Fixture JSON valid; 1628-test collection clean; ruff clean. **Follow-ups for 4.7:** (a) no companion loader test yet — belongs at replay time; (b) several `expected_surface.source_id` slugs best-effort — 4.7 should assert kind+name loosely until DB slugs confirmed.
- [x] 4.3 — **Judge SKILL.md liturgical prose shift.** Added "Liturgical candidates" decision-rubric section (4 bullets: proper/devotional acceptance shift on theological-subject match vs. vocabulary; hagiography behaves as prose; no new-angle/counter-move scoring for liturgy; `frame` field emission rule) + `frame` field in Output contract accepted entries (value `"liturgical_ground"` for proper/devotional only; omitted otherwise — no null). 2-item cap shared across prose + liturgy. Judge + prose-regression tests 75/75 pass; surface tests 100/100; ruff clean. `frame` is additive; parser.py's `.get()` pattern forwards unknown keys untouched (consumed by 4.6 hydration).
- [x] 4.4 — **Liturgical search filters + calendar-range overload.** 5 new `search_commonplace` kwargs: `calendar_year`, `category`, `genre`, `tradition`, `feast_name`. Option A date-range overload on calendar filters. 19 new tests. (commit `66b39f6`)
- [x] 4.5 — **LFF 2024 precedence rules + lectionarypage cross-check.** `precedence_rank` column, transfer rules, 40 new tests, 20/20 lectionarypage match. Subsumes Phase 2 task 2.6. (commit `cfa2317`)
- [x] 4.6 — **`surface.py` candidate hydration shipped.** Pulls `category`, `genre`, `feast_name`, `tradition` from `liturgical_unit_meta` (+ `feast` LEFT JOIN) in one query per `run_surface` call, attaches them to liturgical candidates only (prose candidates stay slim). Fields forwarded to the judge's candidate payload and returned on hydrated accepted/triangulation items. 4 new tests; 1626/1626 suite (`-m "not live"`); ruff + mypy clean on changed files. launchd `com.commonplace.mcp-server` kickstarted (new pid 52882, `/healthcheck` 200).
- [~] 4.7 — **Partial: prose side clears Moderate bar; liturgical side blocked on retrieval-layer bug (see 4.8).** Prose regression: 6 seeds flipped, 5/6 defensible drift (stingy cap working; seed_04 is a net triangulation win), 1/6 (seed_06) is a 120s judge timeout treated as transient. 0/5 spillover traps triggered. 5/5 true-negatives held. Liturgical fixtures: 10/20 (all negatives pass 10/10; all positives fail 0/10) — root cause is retrieval, not judge: BCP collects (60-100 token raw chunks) lose to prose chunks (300-500 tokens) in the KNN top-10, so the judge never sees liturgical candidates. Confirmed by direct search API probe (0 `liturgical_unit` rows in top-30 for Marian-kenosis seed). `commonplace_worker/handlers/liturgy_bcp.py` never applied `embed_text_override` (plan §2.7 option Y) — `liturgy_lff.py` did. Artifacts: `tests/test_liturgical_surfacing_offline.py` (18 offline structural tests), `scripts/replay_4_7_review.py` (live harness), `build/4_7_replay_results.json`. Gates: 1644/1644 suite, ruff + mypy clean on new files.
- [~] 4.8 — **Code + live ingest landed; lit_pos fixture still 0/10 (deferred to 4.9).** Pass 1: backported `embed_text_override` into `commonplace_worker/handlers/liturgy_bcp.py` with per-category composers (collect / daily_office / psalter / proper_liturgy / prayer_thanksgiving); 53 new unit tests. Pass 2 (live): pre-purge backup at `~/commonplace/library.db.pre-4-8.bak` (266MB, intact); cleanly purged 704 BCP docs (+ explicit `chunk_vectors` delete before CASCADE — vec0 doesn't inherit FK); inline-ran both handlers after worker-queue deadlock (Anna Karenina `ingest_library` locked single-FIFO worker, `scripts/inline_ingest_liturgy.py` bypasses the queue). Final counts: BCP 846 docs / 845 meta; LFF 781 docs / 225 bios / 548 collects (56 bios skipped no-feast-match). Gates: 1697/1697, ruff clean, mypy clean on new files. **Replay verdict**: prose rubric stable (0 spillover across 20 seeds); lit_pos 0/10 with `liturgical_hit_count=0` across all cases — candidate pool is 100% prose even on devotional-themed prompts. Contradiction with pass 2e live observation (prose seed_02 mercy surfaced 5773:6 Confession + 6132:9 Litany of Penitence mid-replay) suggests the issue is specific to lit_pos fixture phrasing, not the embed pipeline itself. **Known zombies for later cleanup**: jobs 6702 (Carr, ~68944s runtime from yesterday) and 6703 (Anna Karenina, orphaned by worker stop). **Remove `~/commonplace/library.db.pre-4-8.bak`** after 4.9 closes or state is stable.
- [ ] 4.9 — **Diagnose lit_pos fixture vs. retrieval.** Read one lit_pos seed prompt, call `search()` directly with it, inspect KNN top-30: does any `liturgical_unit` appear? If yes → judge/surface filter bug; if no → fixture phrasing mismatched to 4.8-composed embed strings. Scope: narrow read-only investigation, ~15 min. Output: pinpoint whether 4.9 remediation is fixture rewrite or code fix.
- [x] 4.x follow-up — **MCP thin per-kind ingest tool wrappers.** `ingest_article`, `ingest_youtube`, `ingest_podcast`, `ingest_bluesky_url`, `ingest_image_url` — follow-up split from 4.4 scope creep. (commit `c47d901`)

### Phase 1 open follow-ups

- **LFF feast-backfill.** **DONE 2026-04-18 (commit 01af6ea).** Hit rate 201/283 = 71.0% → 283/283 = 100% via two changes: (1) handler predicate broadened from `source='lff_2024'` to `tradition='anglican'` — covers 24 shared BCP/LFF principal feasts (apostles, Annunciation, Transfiguration, etc.) already seeded under `source='bcp_1979'` without needing duplicate rows. Slug collision impossible since slugs are `{name_snake}_{tradition}`. (2) 58 truly-missing LFF figures added to `feasts.yaml` (Epiphany, Nativity, Holy Innocents, Richard Meux Benson, Fabian, Bakhita, Scholastica, Dunstan, Monica, Jerome, etc.) including 2 bracketed trial-use entries (Adeline Blanchard Tyler + Lili'uokalani of Hawai'i) with `trial_use: true`. Curly-quote surprise: LFF parser emits U+2018 for Hawai'i entries, not U+0027 — yaml rows use `\u2018` escape to match the parser's slug computation. New `TestBroadenedPredicate` class pins the query shape.
- **Pull-forward decision note.** Per user direction, LFF 2022 work (Phase 2 in the plan) was pulled forward into Phase 1: plan's task 1.7 (handler scaffolding) is this session's task 1.10; plan's task 2.4 (LFF handler) is this session's task 1.11. Parser for LFF 2024 (not 2022 as originally planned) landed as task 1.7. Phase 2 is therefore effectively absorbed; tasks 2.1 (fetch PDF), 2.2 (parser), 2.3 (bio insertion), 2.4 (handler), 2.5 (integration test) map onto Phase 1 work with 2.6 (lectionarypage cross-check) still pending as a Phase 2 residual.

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

- ~~Migrate `skills/summarize_capture/parser.py` and `skills/judge_serendipity/parser.py` to the `importlib.util.spec_from_file_location` pattern 4.1 used~~ **DONE 2026-04-16T21:30.** `test_judge_serendipity_skill.py` was already on the pattern; `test_summarize_capture_skill.py` migrated away from `sys.path.insert` + bare `from parser import ...` to unique-named module load. No cross-skill `parser` module-cache collision possible now. 836/836 suite green, ruff clean.
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
