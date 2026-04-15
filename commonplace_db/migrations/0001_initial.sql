-- Migration 0001: Initial schema
-- Establishes core tables: documents, chunks, embeddings, job_queue.
--
-- Design notes / deliberate choices (see also ADR-0003):
--
--   documents  — one row per ingested item (book, capture, bluesky post, etc.).
--                content_type discriminates the source; status tracks ingestion
--                pipeline progress so the worker can resume after a crash.
--
--   chunks     — paragraphs / passages extracted from a document for embedding.
--                chunk_index is the ordinal within the document (0-based).
--                TODO (Phase 2): confirm chunk granularity with plan owner.
--                Plan v5 implies passage/paragraph chunks but leaves exact
--                token budget unspecified.
--
--   embeddings — one vector blob per chunk, keyed to chunks(id).
--                vector_blob stores a raw float32 array as BLOB.
--                TODO (Phase 2): confirm vector dimension (nomic-embed-text
--                default is 768) and decide whether to use sqlite-vec extension
--                or a parallel index file.  sqlite-vec is referenced in plan.md
--                but the exact integration pattern (VIRTUAL TABLE vs. BLOB +
--                external ANN index) is unspecified.  Column kept as BLOB so
--                the table schema survives either choice unchanged.
--
--   job_queue  — all async work (ingestion, synthesis, serendipity judging).
--                Backed by submit_job / get_job_status / cancel_job MCP tools
--                (task 1.4).  Index on (status, created_at) supports the
--                worker's polling query and the status-inspection tools.

-- -------------------------------------------------------------------------
-- documents
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY,
    content_type    TEXT    NOT NULL,   -- 'book' | 'capture' | 'bluesky' | 'kindle_highlight'
    source_uri      TEXT,               -- original URL, file path, or OLID
    title           TEXT,
    author          TEXT,
    content_hash    TEXT    UNIQUE,     -- SHA-256 hex; prevents re-ingest of identical content
    raw_path        TEXT,               -- path under ~/commonplace/ to raw source file (if any)
    status          TEXT    NOT NULL DEFAULT 'pending',
                                        -- pending | ingesting | embedded | failed
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- -------------------------------------------------------------------------
-- chunks
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chunks (
    id              INTEGER PRIMARY KEY,
    document_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,   -- 0-based ordinal within parent document
    text            TEXT    NOT NULL,   -- verbatim passage text
    -- TODO (Phase 2): add token_count column once chunk-size budget is decided.
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks (document_id);

-- -------------------------------------------------------------------------
-- embeddings
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS embeddings (
    id              INTEGER PRIMARY KEY,
    chunk_id        INTEGER NOT NULL UNIQUE REFERENCES chunks(id) ON DELETE CASCADE,
    model           TEXT    NOT NULL,   -- e.g. 'nomic-embed-text:v1.5'
    -- vector_blob: raw little-endian float32 array.
    -- TODO (Phase 2): confirm vector dimension (768 for nomic-embed-text default).
    -- TODO (Phase 2): decide sqlite-vec VIRTUAL TABLE vs. BLOB+external ANN index.
    vector_blob     BLOB    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- -------------------------------------------------------------------------
-- job_queue
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_queue (
    id              INTEGER PRIMARY KEY,
    kind            TEXT    NOT NULL,   -- e.g. 'ingest_capture' | 'generate_book_note' | …
    payload         TEXT    NOT NULL DEFAULT '{}',  -- JSON object; handler-specific params
    status          TEXT    NOT NULL DEFAULT 'queued'
                                        CHECK(status IN ('queued','running','complete','failed','cancelled')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    error           TEXT,               -- last error message or traceback (NULL if none)
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    started_at      TEXT,               -- set when worker picks up the job
    completed_at    TEXT                -- set on complete | failed | cancelled
);

-- Primary query pattern: worker polls for next queued job; tools query by status.
CREATE INDEX IF NOT EXISTS idx_job_queue_status_created
    ON job_queue (status, created_at);
