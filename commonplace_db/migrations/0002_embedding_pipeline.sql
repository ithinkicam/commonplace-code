-- Migration 0002: Embedding pipeline tables
--
-- Adds token_count to chunks (nullable; pre-existing rows keep NULL).
-- Creates the sqlite-vec vec0 virtual table for ANN search over chunk embeddings.
-- The vec0 table is a derived index; embeddings.vector_blob remains canonical.

ALTER TABLE chunks ADD COLUMN token_count INTEGER;

-- vec0 virtual table: keyed on chunks.id, stores 768-dim float32 embeddings.
-- sqlite-vec extension must be loaded before this migration runs.
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vectors
    USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[768]);
