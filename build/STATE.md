# Commonplace Build State

**Current phase:** Phase 3 — Capture handlers (parallel with Phase 2 batch drain)
**Phase 2 started:** 2026-04-15T14:45:00-04:00
**Phase 3 started:** 2026-04-15T17:25:00-04:00
**Phase 3 completed:** 2026-04-15T18:00:00-04:00
**Last update:** 2026-04-15T18:00:00-04:00
**Status:** complete

Phase 2 wave 1 code-complete (commit 794dd1d, 257 tests). Library import draining via Ollama (worker pid 74073) — 98 books enqueued earlier this afternoon. 2.8 overnight book note batch is blocked on that drain. Per user instruction we proceed with Phase 3 in parallel: handler subagents do not contend with Ollama (they enqueue jobs; the worker drains).

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

## Active subagents

(none — Phase 3 complete)

## Open questions for human

1. **Install tesseract for image OCR?** `brew install tesseract` (~50MB). Blocks 3.6 and the OCR pass of 3.7. Green-light or skip image/video OCR for now.
2. **Whisper engine choice for audio fallback (3.4, 3.5, 3.7).** Recommend `faster-whisper` (CTranslate2, ~4× faster on M1, pip-only, no system dep). Alternative is `openai-whisper`. Confirm `faster-whisper` or pick.
3. **Pre-existing Phase 2 questions still open:**
   - Bluesky app password rotation pending
   - Kindle real backfill green-light (still waiting on library drain)
   - Bluesky real backfill green-light (still waiting on library drain)

## Blocked tasks

- 2.8 overnight book note batch — waiting on library Ollama drain
- 3.6 image handler — waiting on tesseract install decision
- 3.4 / 3.5 / 3.7 — soft-blocked on Whisper engine confirmation (default faster-whisper if no answer by wave-2 dispatch)

## Recent completions

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
