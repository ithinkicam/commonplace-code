# ADR-0005: Chunk granularity, vector store pattern, embedding dimension

## Status

Accepted, 2026-04-15. Resolves the three open questions left by ADR-0003.

## Context

Phase 1 stubbed three decisions about the embedding/retrieval path (ADR-0003 §4, §5, plus the vector dimension). Phase 2 (ingestion) needs them locked before the embedding pipeline scaffold (task 2.1) or any handler (2.3–2.6) can be built. All three affect the `chunks` / `embeddings` schema, the search tool shape in Phase 3, and every handler's downstream wiring.

## Decisions

### 1. Chunk granularity — hybrid paragraph / sliding window

Paragraph-first chunker (split on `\n\n`, merge short paragraphs up to a ~400-token floor), falling back to a **sliding window of 512 tokens with 64-token overlap** whenever a single paragraph exceeds a 1500-token cap. `token_count` is recorded per chunk.

**Why:** produces clean semantic units for articles and book chapters (the common case) without degrading on long prose that lacks clear paragraph breaks (older books, transcripts, poetry). 512/64 is the conventional default for retrieval over mixed corpora and sits well under `nomic-embed-text`'s 2048-token native context. Uniform sliding (Option B in the decision memo) would have inflated chunk counts 2–3× for no quality gain at this corpus size.

### 2. Vector store — sqlite-vec `vec0` virtual table

Load the `sqlite-vec` extension at every `connect()` and expose vectors through a `vec0` virtual table keyed on `chunks.id`. The existing `embeddings.vector_blob` column stays as the canonical store; the `vec0` table is a derived ANN index populated at embed time and rebuildable from `embeddings`.

**Why:** corpus is single-digit thousands to low tens of thousands of chunks — well below where an external FAISS/hnswlib index would earn its cost. One SQLite file remains the single source of truth; backups stay `cp library.db`. The `vector_blob` column is preserved so a future switch to an external ANN index is a migration, not a rewrite.

### 3. Vector dimension — 768

Confirmed by `ollama show nomic-embed-text` against the pinned `nomic-embed-text:latest` (id `0a109f422b47`): `embedding length 768`. The `vec0` virtual table is declared with `embedding float[768]`, and the embedder asserts the returned vector length equals 768 on every call (loud failure if Ollama ever returns something else).

## Consequences

- Migration 0002 adds `chunks.token_count INTEGER`, creates the `vec0` virtual table, and backfills nothing (no embeddings exist yet).
- `commonplace_db.db.connect()` loads the `sqlite-vec` extension unconditionally; failure to load is a hard error (no silent degradation to linear scan).
- Handlers call one chunker and one embedder — they do not each re-implement chunking.
- Retrieval in Phase 3 uses `SELECT chunk_id, distance FROM vec0 WHERE embedding MATCH :q ORDER BY distance LIMIT :k` joined back to `chunks`/`documents`.

## References

- ADR-0003 §4, §5 (the stubbed decisions this ADR closes)
- `commonplace_db/migrations/0001_initial.sql` — columns this migration extends
- Plan v5: "Architecture at a glance", "Serendipity", "Storage layout"
- Pinned model: `build/pins/ollama.md`
