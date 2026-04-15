# Pinned: Ollama + embedding model

**Pinned on:** 2026-04-15

## Ollama

- Version: `0.20.7`
- Install path: `/usr/local/opt/ollama/bin/ollama` (Homebrew formula, x86_64 via Rosetta)
- API: `http://localhost:11434`

## Embedding model

- Name: `nomic-embed-text:latest`
- Manifest ID: `0a109f422b47`
- Architecture: `nomic-bert`, 137M params
- Context length: 2048
- Embedding dim: **768**
- Quantization: F16
- Blob SHA-256: `970aa74c0a90ef7482477cf803618e776e173c007bf957f635f1015bfcfef0e6`

## Rule

**Do not upgrade Ollama or the embedding model without re-embedding the corpus.** Changing either means vectors in `library.db` may not sit next to vectors from new captures in the same space. If you need to bump, plan a batch re-embed of the whole corpus and regenerate the sqlite-vec index.

## Verification

```bash
curl -s http://localhost:11434/api/embeddings \
  -d '{"model":"nomic-embed-text","prompt":"hello commonplace"}' \
  | jq -r '.embedding | length'
# expected: 768
```
