-- Migration 0007: Liturgical ingest
--
-- Adds three sidecar tables to support the new 'liturgical_unit' content type.
-- Each liturgical unit is ingested as a separate documents row (one-document-per-unit
-- per §2.2); these tables hang off that row rather than duplicating its core fields.
--
-- liturgical_unit_meta: per-unit liturgical metadata keyed on document_id.
-- feast: the calendar entries that anchor liturgical units in time and tradition.
-- commemoration_bio: biographical notes associated with feasts, optionally also
--   embedded as prose documents for retrieval purposes.
--
-- JSON-holding columns (alternate_names, theological_subjects, raw_metadata) use
-- TEXT, matching the existing convention of job_queue.payload being a JSON string
-- (Commonplace uses SQLite; no JSONB).

CREATE TABLE liturgical_unit_meta (
  document_id          INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  category             TEXT NOT NULL,    -- liturgical_proper | devotional_manual | psalter | hagiography
  genre                TEXT NOT NULL,    -- collect | troparion | kontakion | canticle | prayer | ...
  tradition            TEXT NOT NULL,    -- anglican | byzantine | roman | shared
  source               TEXT NOT NULL,    -- bcp_1979 | lff_2022 | jordanville
  language_register    TEXT,             -- rite_i | rite_ii | traditional | modern | NULL
  office               TEXT,             -- morning_prayer | evening_prayer | eucharist | compline | hours | other | NULL
  office_position      TEXT,             -- opening | general | after_communion | dismissal | NULL (source-specific allowed)
  calendar_anchor_id   INTEGER REFERENCES feast(id),
  canonical_id         TEXT,             -- shared across Rite I/II duplicates and cross-source copies
  raw_metadata         TEXT              -- JSON: tone, mode, page ref, pdf coords, etc.
);

CREATE INDEX idx_liturgical_meta_category  ON liturgical_unit_meta(category);
CREATE INDEX idx_liturgical_meta_genre     ON liturgical_unit_meta(genre);
CREATE INDEX idx_liturgical_meta_tradition ON liturgical_unit_meta(tradition);
CREATE INDEX idx_liturgical_meta_feast     ON liturgical_unit_meta(calendar_anchor_id);
CREATE INDEX idx_liturgical_meta_canonical ON liturgical_unit_meta(canonical_id);

CREATE TABLE feast (
  id                             INTEGER PRIMARY KEY,
  primary_name                   TEXT NOT NULL,
  alternate_names                TEXT,             -- JSON array
  tradition                      TEXT NOT NULL,    -- anglican | byzantine | shared
  calendar_type                  TEXT NOT NULL,    -- fixed | movable | commemoration
  date_rule                      TEXT NOT NULL,    -- 'MM-DD' | 'easter+0' | 'easter-46' | etc.
  precedence                     TEXT NOT NULL,    -- principal_feast | holy_day | lesser_commemoration | ferial
  theological_subjects           TEXT,             -- JSON array
  cross_tradition_equivalent_id  INTEGER REFERENCES feast(id),
  created_at                     TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at                     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_feast_tradition ON feast(tradition);
CREATE INDEX idx_feast_date_rule ON feast(date_rule);

CREATE TABLE commemoration_bio (
  id          INTEGER PRIMARY KEY,
  feast_id    INTEGER NOT NULL REFERENCES feast(id),
  document_id INTEGER REFERENCES documents(id),  -- if the bio is also embedded as prose
  text        TEXT NOT NULL,
  source      TEXT NOT NULL
);

CREATE INDEX idx_bio_feast ON commemoration_bio(feast_id);
