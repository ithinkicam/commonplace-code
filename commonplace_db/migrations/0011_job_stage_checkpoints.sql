-- Migration 0011: Stage-level job checkpoints.
--
-- Tracks handler-defined stage progress so a job that crashed mid-execution
-- (mid-Whisper, mid-ffmpeg, mid-yt-dlp) can resume from the last completed
-- stage on its next attempt instead of re-running every stage from scratch.
--
-- Design:
--   * Side table (not a column on job_queue) because stage tracking is a
--     1-to-many relationship with jobs, and we want to evolve the stage
--     vocabulary per-handler without schema churn.
--   * ON DELETE CASCADE from job_queue.id so stage rows disappear with
--     their parent job when the queue is pruned.
--   * Partial uniqueness on (job_id, stage) so each stage has one logical
--     row per job; updates upsert via ON CONFLICT ... DO UPDATE.
--   * payload is TEXT holding JSON — stage outputs (paths to durable
--     scratch files, content hashes, document ids) travel from one attempt
--     to the next here, not in tempfile.TemporaryDirectory which dies with
--     the crashed process.
--
-- Both indexes are cheap; the hot paths are
--   SELECT status FROM ... WHERE job_id=? AND stage=?  (is_complete)
--   SELECT payload FROM ... WHERE job_id=? AND stage=? AND status='complete'
-- which the unique index covers directly.

CREATE TABLE IF NOT EXISTS job_stage_checkpoints (
    id          INTEGER PRIMARY KEY,
    job_id      INTEGER NOT NULL
                  REFERENCES job_queue(id) ON DELETE CASCADE,
    stage       TEXT    NOT NULL,
    status      TEXT    NOT NULL
                  CHECK(status IN ('started','complete','failed')),
    payload     TEXT,
    attempt     INTEGER NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_job_stage_checkpoints_job_stage
    ON job_stage_checkpoints(job_id, stage);

CREATE INDEX IF NOT EXISTS idx_job_stage_checkpoints_job
    ON job_stage_checkpoints(job_id, status);
