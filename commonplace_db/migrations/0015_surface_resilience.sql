-- Migration 0015: In-progress surface telemetry and stage tracking.
--
-- The original surface telemetry row was inserted only after an invocation
-- finished. A process crash or indefinitely blocked search therefore left no
-- record at all. These columns let the server insert at invocation start and
-- update the same row as it moves through embedding, retrieval, and judging.

ALTER TABLE surface_invocations
    ADD COLUMN invocation_status TEXT NOT NULL DEFAULT 'running';

ALTER TABLE surface_invocations
    ADD COLUMN stage TEXT NOT NULL DEFAULT 'started';

ALTER TABLE surface_invocations
    ADD COLUMN judge_error_kind TEXT;

ALTER TABLE surface_invocations
    ADD COLUMN updated_at TEXT;

ALTER TABLE surface_invocations
    ADD COLUMN completed_at TEXT;

-- Rows created before this migration necessarily represent completed calls.
UPDATE surface_invocations
   SET invocation_status = 'complete',
       stage = 'complete',
       updated_at = created_at,
       completed_at = created_at;

CREATE INDEX IF NOT EXISTS idx_surface_invocations_status_created
    ON surface_invocations(invocation_status, created_at);

CREATE INDEX IF NOT EXISTS idx_surface_invocations_stage_created
    ON surface_invocations(stage, created_at);
