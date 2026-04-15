# ADR-0003: SQLite Schema Design for Phase 1

## Status

Accepted, 2026-04-15.

## Context

Task 1_1 required establishing the SQLite schema and migration system that all other Phase 1 tasks depend on. Plan v5 specifies the high-level storage layout (documents, chunks, embeddings, sqlite-vec) but leaves several implementation details unspecified or vague.

## Decisions and Rationale

### 1. Migration runner uses `schema_version` table + numbered `.sql` files

Each migration file is named `NNNN_<description>.sql` (zero-padded four-digit prefix). The runner applies them in lexicographic order, tracking applied versions in `schema_version(version INTEGER PRIMARY KEY, applied_at TEXT)`. Re-running is idempotent. This pattern is familiar, stdlib-only, and matches the plan's explicit requirement for "schema migrations from day one."

### 2. `documents.status` column with `pending | ingesting | embedded | failed`

Plan v5 mentions "stage-level checkpoints" and pipeline resumability but doesn't define a status enum. Choice: four values sufficient for Phase 1 ingestion pipeline. Additional values can be added in a later migration if Phase 2 handlers need them.

### 3. `documents.content_hash` as SHA-256 hex with UNIQUE constraint

Plan v5 mentions "dedup by content hash prevents replay duplicates." SHA-256 is the standard choice. UNIQUE constraint enforced at the DB layer so duplicate detection doesn't require application-level pre-checks.

### 4. Chunk granularity — **stubbed, TODO for Phase 2**

Plan v5 implies "passage/paragraph chunks" but leaves token budget unspecified. The `chunks` table has `chunk_index INTEGER` (0-based ordinal) and `text TEXT` columns. A `token_count` column is deferred with a `-- TODO (Phase 2)` comment. The chunk granularity decision (sliding window vs. paragraph boundary, target ~200–400 tokens) should be resolved when the embedding pipeline is built in Phase 2.

### 5. Embedding storage as BLOB — **sqlite-vec integration stubbed for Phase 2**

Plan v5 explicitly names sqlite-vec for vector search but doesn't specify whether to use a `VIRTUAL TABLE` (sqlite-vec extension's ANN index) or raw `BLOB` storage with a separate ANN index. Decision: store raw little-endian float32 arrays in `embeddings.vector_blob BLOB`. This column schema survives either Phase 2 integration pattern unchanged. The vector dimension (768 for `nomic-embed-text` default) is noted in a `-- TODO` comment but not encoded in the schema.

### 6. `job_queue.status` CHECK constraint

The task contract specifies `CHECK(status IN ('queued','running','complete','failed','cancelled'))`. This is enforced at the DB layer as specified.

### 7. `connect()` accepts `str | Path | None`

`None` resolves to `DB_PATH` (the env-var-overridable default). This matches the task contract signature and allows callers to pass pathlib.Path objects naturally.

### 8. `commonplace_db` as a shared package

Both `commonplace_server` and `commonplace_worker` import from `commonplace_db`. The package lives at repo root alongside the two service packages and is registered in `pyproject.toml [tool.setuptools] packages`.

## Open Questions for Phase 2

- **Chunk granularity**: sliding window vs. paragraph boundary; target token budget. Resolve before embedding pipeline work begins.
- **sqlite-vec integration pattern**: VIRTUAL TABLE (full ANN index managed by sqlite-vec extension) vs. BLOB storage + external FAISS/hnswlib index. Plan v5 names sqlite-vec but the exact API shape affects both Phase 2 handler code and the search tool in Phase 3.
- **Vector dimension**: `nomic-embed-text` defaults to 768 dimensions; confirm with pinned model version before writing the embedding handler.

## References

- `docs/plan.md` — "Storage layout", "Architecture at a glance", "Reliability", "Serendipity"
- Task contract: `1_1_sqlite_schema`
- `commonplace_db/migrations/0001_initial.sql`
