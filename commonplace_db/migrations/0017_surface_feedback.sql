-- Migration 0017: User feedback on surfaced results.
--
-- surface_invocations recorded whether an invocation ran, never whether the
-- surfaced items mattered. user_ack stores an explicit verdict supplied via
-- the surface_feedback MCP tool; it is the long-term quality signal and the
-- reason this table is exempt from purge_old_records.

ALTER TABLE surface_invocations
    ADD COLUMN user_ack TEXT
        CHECK (user_ack IS NULL OR user_ack IN ('used', 'ignored', 'wrong'));

ALTER TABLE surface_invocations
    ADD COLUMN user_ack_at TEXT;
