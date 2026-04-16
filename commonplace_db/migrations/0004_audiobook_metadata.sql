-- Migration 0004: Audiobook metadata columns
--
-- Adds audiobook_path and narrator to documents.
-- audiobook_path: absolute path to the audiobook directory or file on the
--   external drive; set on storygraph_entry rows when a filesystem match is
--   found, and on audiobook-typed documents as their primary identifier.
-- narrator: the audiobook narrator extracted from tags or directory name.
--
-- Both columns are nullable — existing rows are unaffected.
-- SQLite ADD COLUMN is idempotent when guarded by the schema_version table;
-- but to be safe the statements are written to fail gracefully if re-applied
-- (SQLite raises "duplicate column name" on ADD COLUMN, which executescript
-- will surface — the migration runner skips already-applied versions via
-- schema_version, so this is safe in practice).

ALTER TABLE documents ADD COLUMN audiobook_path TEXT;
ALTER TABLE documents ADD COLUMN narrator       TEXT;

-- Index to support lookups by audiobook path (for idempotency checks in the handler)
CREATE INDEX IF NOT EXISTS idx_documents_audiobook_path
    ON documents (audiobook_path)
    WHERE audiobook_path IS NOT NULL;
