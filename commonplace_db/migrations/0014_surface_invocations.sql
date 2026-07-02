-- Migration 0014: Surface invocation telemetry.
--
-- Records real run_surface() calls so judge/retrieval tuning can be based on
-- actual use rather than synthetic replay fixtures alone. JSON columns keep
-- the first pass compact while preserving enough detail for later digest and
-- feedback tools.

CREATE TABLE IF NOT EXISTS surface_invocations (
    id                    INTEGER PRIMARY KEY,
    seed                  TEXT NOT NULL,
    mode                  TEXT NOT NULL CHECK(mode IN ('ambient', 'on_demand')),
    types                 TEXT NOT NULL DEFAULT '[]', -- JSON array of strings
    requested_limit       INTEGER NOT NULL,
    similarity_floor      REAL NOT NULL,
    recency_bias          INTEGER NOT NULL CHECK(recency_bias IN (0, 1)),
    raw_candidate_count   INTEGER NOT NULL DEFAULT 0,
    floor_candidate_count INTEGER NOT NULL DEFAULT 0,
    judge_status          TEXT NOT NULL CHECK(
                              judge_status IN (
                                  'not_called',
                                  'success',
                                  'embedding_failed',
                                  'judge_failed',
                                  'judge_unparseable'
                              )
                          ),
    note                  TEXT,
    error                 TEXT,
    rejected_count        INTEGER,
    accepted_json         TEXT NOT NULL DEFAULT '[]',
    triangulation_json    TEXT NOT NULL DEFAULT '[]',
    candidates_json       TEXT NOT NULL DEFAULT '[]',
    elapsed_ms            REAL NOT NULL,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_surface_invocations_created
    ON surface_invocations(created_at);

CREATE INDEX IF NOT EXISTS idx_surface_invocations_mode_created
    ON surface_invocations(mode, created_at);

CREATE INDEX IF NOT EXISTS idx_surface_invocations_judge_status
    ON surface_invocations(judge_status, created_at);
