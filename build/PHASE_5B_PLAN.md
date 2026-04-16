# Phase 5b — Movies & TV Shows

## Real corpus

- `/Volumes/Expansion/Movies/` — ~174 entries (mix of single files and folders, ~80% torrent-named).
- `/Volumes/Expansion/TV Shows/` — ~61 entries (folders, all torrent-named).

Filenames: `Andor (2022) Season 2 S02 (2160p HDR DSNP WEB-DL x265 HEVC 10bit DDP 5.1 Vyndros)`.
Parsing this cleanly is harder than the audiobook case because the canonical (title, year) is buried under release noise.

## Two options

### Option A — Filesystem-only, mirrors audiobooks (5a)

- New worker handler: `ingest_movie`, `ingest_tv_show` (or single `ingest_video_metadata`).
- Walker script: `scripts/movies_scan.py` enqueues one job per top-level entry.
- Filename parser using a battle-tested library (`PTN` — parse-torrent-name, MIT-licensed)
  to extract `title`, `year`, `season`, `episode`, `quality`.
- Migration 0005: add `media_type` ('movie' | 'tv_show'), `year`, `season_count`,
  `path` columns to `documents` (or new `media_entry` table — TBD).
- No external API. No plot summaries. No embedding (no body text to embed).
- Indexed for direct retrieval via `search_commonplace(content_type='movie')`,
  but **not useful for serendipity** — there's nothing to embed.

**Tradeoff:** matches audiobook precedent (no-api, filesystem-only). Fast. But
you can't surface *"have I seen anything that bears on the discussion of free
will?"* because there's no plot/genre signal. Serendipity tool will skip
movies/TV by default unless we add explicit type-filter behavior.

### Option B — Filesystem + TMDB enrichment

- Same handler/walker as A.
- After filename parse, query TMDB API (`/search/movie`, `/search/tv`) using
  (title, year) → fetch canonical title, genres, plot summary, director/cast.
- Embed the plot summary so movies/TV become first-class serendipity candidates.
- Requires TMDB API key (free tier: 50 req/sec, plenty for one-time backfill of 235 items).
- Adds a dependency: `requests` (already pinned via Trafilatura?), TMDB v3 API.
- Same migration 0005 plus `genres TEXT`, `plot TEXT` columns.

**Tradeoff:** adds external API but unlocks serendipity ("you watched *Andor*,
which sits next to Hannah Arendt's *On Revolution*..."). Backfill: ~3 minutes
of API calls + Ollama embed time for 235 short summaries.

### Option C — Defer, like Plex was originally

- Same logic that originally deferred Phase 5: speculative until we hit a
  specific moment where it would have helped.
- The audiobook pull-forward was justified by *audiobooks are primary reading*.
  Movies/TV are entertainment, not a thinking corpus. Asking *"connect this to
  what I watched"* is rarer than *"connect this to what I read."*

## Recommendation

**Option B is what makes movies/TV actually useful** for the commonplace
mission (serendipity + cross-corpus connection). Option A indexes
metadata you can already see by looking at the file system; the only thing
it adds is searchability of titles, which Spotlight already does.

If user wants movies/TV to *do* something for the project, B. If they just
want completeness ("I want to know this corpus exists when chatting"),
C is more honest.

## Open questions for user

1. **A, B, or C?**
2. If B: TMDB or OMDB? (TMDB has better TV data; OMDB is single-source).
3. If B: also enrich audiobooks the same way (Open Library API) so books
   that aren't in StoryGraph still get plot/genre signal? Or leave audiobooks
   metadata-only?
4. Sub-tracking: is per-episode (TV) granularity needed, or per-show is enough?
   Per-show is much simpler and probably fine for serendipity.
5. Anything to **exclude**? E.g. kids' movies (Disney/Pixar/Ghibli) might not be
   conversation-relevant; might want to flag them for de-ranking in surfacing.

## Estimated wall-clock if B

- 1 wave, single agent, sonnet (similar shape to 5a).
- ~30-45 min including migration, walker, handler, parser, TMDB client,
  tests, dry-run on the real drive.
- Backfill (handler runtime once enqueued): ~1-2 hrs depending on Ollama queue.
