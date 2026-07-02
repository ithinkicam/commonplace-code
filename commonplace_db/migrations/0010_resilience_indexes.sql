-- Migration 0010: Resilience / perf indexes
--
-- Adds two indexes that match real hot-path query patterns discovered in
-- an audit of commonplace_server/ and commonplace_worker/ handlers:
--
--   1. chunks(document_id, chunk_index)
--      - commonplace_worker/handlers/profile.py joins chunks ON
--        (document_id, chunk_index = 0) three times per profile-regen pass
--        to pull the first-chunk snippet for recent highlights/captures/books.
--      - commonplace_server/surface.py ORDER BY chunk_index LIMIT 1 on each
--        surface hit to grab a representative snippet.
--      The existing idx_chunks_document_id covers document_id alone; adding
--      chunk_index to the index lets SQLite satisfy both the join/filter
--      and the ordering without a sort step.
--
--   2. documents(content_type, status)
--      - commonplace_server/progress.py groups by status with an optional
--        content_type filter (GROUP BY status; WHERE status='pending' with
--        content_type predicate).
--      - Future queries like "find all books with status='pending' for
--        enrichment" follow the same shape. The existing partial UNIQUE on
--        (content_type, source_id) doesn't help because source_id may be
--        NULL for many rows.
--
-- Both indexes are additive — no existing data or queries change behaviour.
-- `IF NOT EXISTS` keeps the migration idempotent if a future environment
-- has already created them manually.

CREATE INDEX IF NOT EXISTS idx_chunks_document_chunk_index
    ON chunks(document_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_documents_content_type_status
    ON documents(content_type, status);
