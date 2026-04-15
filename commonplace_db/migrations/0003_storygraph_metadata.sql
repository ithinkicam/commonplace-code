-- Migration 0003: StoryGraph metadata columns
--
-- Adds rating, read_date, and source_id to documents for StoryGraph CSV imports.
-- StoryGraph rows carry no body text so they are never embedded; these columns
-- are reference metadata consumed by book-note generation and book-classification
-- skills that need to know what the user has actually read.
--
-- SQLite does not allow ADD COLUMN ... UNIQUE in a single statement, so
-- uniqueness across (content_type, source_id) is enforced by a partial index
-- that only covers rows where source_id IS NOT NULL.

ALTER TABLE documents ADD COLUMN rating    REAL;   -- 0-5 in 0.25 increments (StoryGraph scale)
ALTER TABLE documents ADD COLUMN read_date TEXT;   -- ISO 8601 date string or NULL
ALTER TABLE documents ADD COLUMN source_id TEXT;   -- StoryGraph internal book ID (if present)

-- Partial UNIQUE index: prevents re-importing the same StoryGraph book.
-- Only rows with a non-NULL source_id participate; rows without one fall back
-- to the existing content_hash uniqueness check.
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_source_id
    ON documents(content_type, source_id)
    WHERE source_id IS NOT NULL;
