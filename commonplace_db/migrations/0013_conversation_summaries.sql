-- Migration 0013: Curated AI conversation summaries.
--
-- Conversation summaries capture insights and shifts in the user's thinking
-- from Claude/ChatGPT conversations. The summary text is stored as document
-- chunks; type-specific metadata lives here for filtering and provenance.

CREATE TABLE IF NOT EXISTS conversation_summary_meta (
    document_id       INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    conversation_date TEXT NOT NULL,
    platform          TEXT NOT NULL CHECK(platform IN ('claude', 'chatgpt', 'other')),
    source_url        TEXT,
    model             TEXT,
    topics            TEXT, -- JSON array of strings
    captured_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_summary_meta_date
    ON conversation_summary_meta(conversation_date);

CREATE INDEX IF NOT EXISTS idx_conversation_summary_meta_platform
    ON conversation_summary_meta(platform);
