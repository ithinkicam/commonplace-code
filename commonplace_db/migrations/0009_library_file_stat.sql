-- Migration 0009: Library file stat fast-path
--
-- Adds two columns to the documents table so the 15-min library scan
-- (scripts/library_scan.py) can skip already-ingested books without opening
-- and hashing the file.  On Google Drive On-Demand this is critical: hashing
-- forces every file to materialize locally on every scan cycle.
--
--   file_size   — bytes, from Path.stat().st_size at ingest time.
--   file_mtime  — Unix seconds (float), from Path.stat().st_mtime.
--
-- Both are nullable for back-compat with rows inserted before this migration;
-- the scan falls back to the existing content_hash lookup when stats are absent
-- or stale.
--
-- idx_documents_source_uri: partial index keyed on source_uri (non-null only),
-- supporting the scan's per-file "has this path been ingested?" lookup.
-- source_uri is populated with the absolute file path for 'book' content_type.

ALTER TABLE documents ADD COLUMN file_size  INTEGER;
ALTER TABLE documents ADD COLUMN file_mtime REAL;

CREATE INDEX IF NOT EXISTS idx_documents_source_uri
    ON documents (source_uri)
    WHERE source_uri IS NOT NULL;
