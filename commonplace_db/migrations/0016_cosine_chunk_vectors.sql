-- Migration 0016: Rebuild chunk_vectors with cosine distance.
--
-- chunk_vectors was created (migration 0002) without a distance_metric, so
-- sqlite-vec defaulted to L2. Query code (surface.py, search.py) converts
-- distance to similarity as 1 - distance, which assumes cosine; with
-- unnormalized nomic-embed-text vectors (norm ~19-23) every L2 distance
-- exceeded 1.0 and every similarity clamped to 0.0.
--
-- chunk_vectors is a derived index; embeddings.vector_blob remains canonical,
-- so this drops, recreates with cosine, and repopulates. The length guard
-- mirrors the 768-dim float32 shape the index requires (768 * 4 bytes).

DROP TABLE IF EXISTS chunk_vectors;

CREATE VIRTUAL TABLE chunk_vectors
    USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[768] distance_metric=cosine);

INSERT INTO chunk_vectors(chunk_id, embedding)
    SELECT chunk_id, vector_blob
      FROM embeddings
     WHERE length(vector_blob) = 3072;
