-- Migration 0008: Feast source + trial_use columns
--
-- Adds two provenance/status fields to the feast table introduced in 0007:
--   source      — canonical source of the entry (lff_2024 | bcp_1979 | menaion | local).
--                 Nullable for back-compat with rows inserted before this migration.
--   trial_use   — true for LFF "[bracketed]" trial-use commemorations; defaults to 0.
--
-- Both fields are authored in feasts.yaml; the feast importer (scripts/feast_import.py)
-- round-trips them into these columns.

ALTER TABLE feast ADD COLUMN source TEXT;
ALTER TABLE feast ADD COLUMN trial_use INTEGER NOT NULL DEFAULT 0;
