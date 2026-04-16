-- Migration 0005: Video metadata columns
--
-- Adds columns to documents for movie and TV show ingestion (Phase 5b).
-- These columns support filesystem-walker ingest with TMDB enrichment.
--
-- media_type:      'movie' or 'tv_show' (NULL for non-video documents)
-- release_year:    year the title was first released
-- season_count:    number of seasons (TV shows only)
-- director:        primary director name (movies)
-- genres:          JSON array string e.g. '["Drama","Comedy"]'
-- plot:            plot summary from TMDB — the serendipity candidate text
-- tmdb_id:         TMDB numeric ID for re-fetching or linking
-- filesystem_path: absolute path to the top-level directory or file on disk

ALTER TABLE documents ADD COLUMN media_type      TEXT;
ALTER TABLE documents ADD COLUMN release_year    INTEGER;
ALTER TABLE documents ADD COLUMN season_count    INTEGER;
ALTER TABLE documents ADD COLUMN director        TEXT;
ALTER TABLE documents ADD COLUMN genres          TEXT;
ALTER TABLE documents ADD COLUMN plot            TEXT;
ALTER TABLE documents ADD COLUMN tmdb_id         INTEGER;
ALTER TABLE documents ADD COLUMN filesystem_path TEXT;

-- Index to support idempotency checks by filesystem path
CREATE INDEX IF NOT EXISTS idx_documents_filesystem_path
    ON documents (filesystem_path)
    WHERE filesystem_path IS NOT NULL;
