# Commonplace Build State

**Current phase:** Phase 4 wave 2 complete; awaiting Phase 5b decision
**Phase 2 started:** 2026-04-15T14:45:00-04:00
**Phase 3 started:** 2026-04-15T17:25:00-04:00
**Phase 3 completed:** 2026-04-15T18:00:00-04:00
**Phase 4 started:** 2026-04-16T10:00:00-04:00
**Phase 4 wave 2 committed:** 2026-04-16T (commit 5e06102, tag phase-4-wave-2-complete)
**Phase 4.6 prep committed:** 2026-04-16T (commit 1c3934d — correct_judge + custom-instructions draft)
**Last update:** 2026-04-16T12:30:00-04:00
**Status:** in_progress (audiobook backfill enqueued; Phase 5b decision pending)

Phase 4 wave 2 complete. `correct` MCP tool extended with `target_type='judge_serendipity'` so users can tune ambient surfacing in-chat. 4.6 custom-instructions draft sitting at `build/4_6_custom_instructions_draft.md` for user to refine in claude.ai. Worker restarted (pid 96671); migration 0004 already applied; new HANDLERS keys (`ingest_audiobook`, `regenerate_profile`) registered. Audiobook scan enqueued 335 jobs. Library drain resumed: 36/98 books complete, 1 running, 60 queued; 1 historic Ollama-500 failure not retried. Audiobook jobs (335) sit behind library jobs in FIFO order — they'll process once library drain completes (metadata-only, no Ollama contention from audiobooks themselves).

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

- [x] 5a — Audiobookshelf filesystem handler shipped. 40 new tests (28 handler + 12 scanner), 607/607 suite green, ruff clean. Dry-run on real drive found 335 logical books. `mutagen==1.47.0` pinned. Migration 0004 adds `audiobook_path` + `narrator` columns to `storygraph_entry`. `ingest_audiobook` registered in worker HANDLERS. Jaccard 0.70 fuzzy merge against `storygraph_entry`. NOT YET RUN against real data — deferred until post-Phase-4 worker restart (applies migration 0004 at startup).

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

## Open questions for human

1. **Profile `current.md` bootstrap.** User is seeding this on the side. Regen handler (4.2) will need to handle both existing-current-md and first-run cases.
2. **Phase 5b direction (movies + TV).** Plan at `build/PHASE_5B_PLAN.md` — three options: (A) filesystem-only metadata mirror of audiobooks (no API), (B) filesystem + TMDB enrichment with embedded plot summaries (serendipity-capable), (C) defer until concrete need. Probe found 174 movies and 61 TV shows on `/Volumes/Expansion/`.

## Blocked tasks

- 2.8 overnight book note batch — waiting on library Ollama drain (not rolled into Phase 4; holds for batch dispatch once drain completes)
- 4.7 corpus judge tuning — waiting on library drain + Kindle + Bluesky backfills to complete (corpus-dependent quality)

## Deferred (will run after library drain)

- Kindle real backfill (18 books, 333 highlights — green-lit by user 2026-04-16)
- Bluesky real backfill (3,465 posts — green-lit by user 2026-04-16; app password rotation disregarded)

## Recent completions

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
