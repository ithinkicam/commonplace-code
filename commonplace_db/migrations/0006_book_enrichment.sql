-- Migration 0006: Book enrichment columns
--
-- Adds public-data enrichment fields to documents for book-typed rows.
-- These fields are populated by the ingest_book_enrichment worker handler
-- which queries Open Library (primary) and Google Books (fallback).
--
-- description: Publisher/work description (plain text, may be multi-paragraph).
-- subjects: JSON array of subject strings, e.g. '["Fiction", "Science Fiction"]'.
-- first_published_year: Four-digit integer year of first publication.
-- isbn: ISBN-13 (preferred) or ISBN-10 if that's all that's available.
-- enrichment_source: Which API provided the data — 'open_library', 'google_books', or NULL.
-- enriched_at: ISO 8601 timestamp of when enrichment completed successfully.
--
-- All columns are nullable; existing rows are unaffected.
-- The partial index on (content_type, enriched_at) WHERE enriched_at IS NULL
-- supports efficient re-enrichment queries without scanning all documents.

ALTER TABLE documents ADD COLUMN description         TEXT;
ALTER TABLE documents ADD COLUMN subjects            TEXT;
ALTER TABLE documents ADD COLUMN first_published_year INTEGER;
ALTER TABLE documents ADD COLUMN isbn               TEXT;
ALTER TABLE documents ADD COLUMN enrichment_source  TEXT;
ALTER TABLE documents ADD COLUMN enriched_at        TEXT;

-- Partial index for re-enrichment queries: find all book-type docs not yet enriched.
CREATE INDEX IF NOT EXISTS idx_documents_book_enrichment_pending
    ON documents (content_type, enriched_at)
    WHERE enriched_at IS NULL;
