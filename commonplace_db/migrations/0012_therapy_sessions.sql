-- Migration 0012: Therapy session ingestion metadata and watcher runs.
--
-- Therapy sessions are one document per Notion child page. Type-specific
-- metadata lives in a dedicated table keyed by documents.id so range queries
-- can index session_date without adding more sparse columns to documents.

CREATE TABLE IF NOT EXISTS therapy_session_meta (
    document_id           INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    session_date          TEXT NOT NULL,
    therapist             TEXT NOT NULL,
    session_type          TEXT NOT NULL CHECK(session_type IN ('individual', 'couples')),
    notion_page_id        TEXT NOT NULL UNIQUE,
    notion_url            TEXT,
    notion_last_edited_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_therapy_session_meta_session_date
    ON therapy_session_meta(session_date);

CREATE INDEX IF NOT EXISTS idx_therapy_session_meta_last_edited
    ON therapy_session_meta(notion_last_edited_at);

CREATE TABLE IF NOT EXISTS scheduled_runs (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    status       TEXT NOT NULL,
    details      TEXT,
    started_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    completed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_scheduled_runs_name_completed
    ON scheduled_runs(name, completed_at);
