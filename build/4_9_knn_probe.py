#!/usr/bin/env python3
"""Task 4.9 pass 1 — read-only KNN probe for liturgical surfacing diagnosis.

For each target seed, embed via Ollama, run KNN top-100 on chunk_vectors,
join to chunks+documents+liturgical_unit_meta, and print candidate table.
No writes, no ingest.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import commonplace_db  # noqa: E402
from commonplace_server.embedding import embed, pack_vector  # noqa: E402

SEEDS = {
    "lit_pos_01 (marian kenosis)": (
        "I'm trying to articulate why the Annunciation feels like the "
        "blueprint for every act of consent to God. Mary's 'let it be to "
        "me' is not passive — it's the hinge on which the Incarnation turns."
    ),
    "lit_pos_02 (mercy without acknowledgment)": (
        "What does it mean to show mercy to someone who has not asked for "
        "it? Not forgiveness, exactly — more like a decision to carry the "
        "weight of another person's failure without demanding they "
        "acknowledge it."
    ),
    "lit_pos_10 (psalm 23 / grief)": (
        "My grandmother died last week. The twenty-third psalm keeps "
        "returning, but not as comfort exactly — more as a kind of "
        "insistence. 'Yea, though I walk through the valley' does not "
        "promise the valley ends; it insists that someone is there in it."
    ),
    # Prose seed_02 — the one that pass 2e showed surfacing 5773:6 + 6132:9.
    # Pulled from tests/fixtures/prose_regression.json (seed_02 mercy).
    "prose seed_02 (mercy / Bloom)": None,  # load below
}

PROSE_SEED_02_ID = "seed_02"


def _load_prose_seed_02() -> str:
    import json

    path = REPO_ROOT / "tests" / "fixtures" / "prose_regression.json"
    with open(path) as f:
        j = json.load(f)
    for s in j["seeds"]:
        if s["id"] == PROSE_SEED_02_ID:
            return s["content"]
    raise KeyError(PROSE_SEED_02_ID)


def probe(label: str, seed: str, conn, top_n: int = 100) -> None:
    print(f"\n{'=' * 78}\n{label}\nseed: {seed[:120]}{'...' if len(seed) > 120 else ''}\n{'=' * 78}")

    vec = embed([seed.strip()])[0]
    blob = pack_vector(vec)

    knn = conn.execute(
        "SELECT chunk_id, distance FROM chunk_vectors "
        "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (blob, top_n),
    ).fetchall()
    if not knn:
        print("  (no KNN results)")
        return

    dist_by_chunk = {r["chunk_id"]: r["distance"] for r in knn}
    chunk_ids = list(dist_by_chunk.keys())
    placeholders = ",".join("?" * len(chunk_ids))

    rows = conn.execute(
        f"SELECT c.id AS chunk_id, c.text AS chunk_text, c.chunk_index, "
        f"       d.id AS document_id, d.content_type, d.title, d.source_uri, "
        f"       lum.category, lum.genre, lum.tradition, "
        f"       f.primary_name AS feast_name "
        f"FROM chunks c "
        f"JOIN documents d ON c.document_id = d.id "
        f"LEFT JOIN liturgical_unit_meta lum ON lum.document_id = d.id "
        f"LEFT JOIN feast f ON f.id = lum.calendar_anchor_id "
        f"WHERE c.id IN ({placeholders})",
        chunk_ids,
    ).fetchall()

    by_chunk = {r["chunk_id"]: r for r in rows}

    # print top 30 first
    print(f"\nTop 30 (of {top_n}) candidates by distance:")
    print(f"{'rk':>3} {'dist':>6} {'doc_id':>6} {'ctype':<16} {'genre/kind':<18} {'title':<36} text-head")
    print("-" * 140)

    lit_hits_top30: list[tuple[int, dict]] = []
    lit_hits_top100: list[tuple[int, dict]] = []

    for rank, k in enumerate(knn, start=1):
        row = by_chunk.get(k["chunk_id"])
        if row is None:
            continue
        ct = row["content_type"] or ""
        gk = (row["genre"] or "") if ct == "liturgical_unit" else ""
        title = (row["title"] or "")[:34]
        txt = (row["chunk_text"] or "").replace("\n", " ")[:80]
        doc_id = row["document_id"]
        chunk_idx = row["chunk_index"]
        is_lit = ct == "liturgical_unit"
        flag = " *L*" if is_lit else "    "
        if rank <= 30:
            print(f"{rank:>3}{flag}{k['distance']:>6.3f} {doc_id:>6} {ct:<16} {gk:<18} {title:<36} {txt}")
        if is_lit:
            info = {
                "rank": rank,
                "distance": k["distance"],
                "doc_id": doc_id,
                "chunk_idx": chunk_idx,
                "cand_id": f"{doc_id}:{chunk_idx}",
                "genre": row["genre"],
                "category": row["category"],
                "tradition": row["tradition"],
                "feast_name": row["feast_name"],
                "title": row["title"],
                "source_uri": row["source_uri"],
                "text_head": txt,
            }
            if rank <= 30:
                lit_hits_top30.append((rank, info))
            lit_hits_top100.append((rank, info))

    print(f"\nLITURGICAL HITS IN TOP-30: {len(lit_hits_top30)}")
    for rank, info in lit_hits_top30:
        print(
            f"  rank={rank} dist={info['distance']:.3f} "
            f"cand_id={info['cand_id']} genre={info['genre']} "
            f"feast={info['feast_name']} title={info['title']!r}"
        )
    print(f"\nLITURGICAL HITS IN TOP-100: {len(lit_hits_top100)}")
    if len(lit_hits_top100) > len(lit_hits_top30):
        for rank, info in lit_hits_top100[len(lit_hits_top30):]:
            print(
                f"  rank={rank} dist={info['distance']:.3f} "
                f"cand_id={info['cand_id']} genre={info['genre']} "
                f"feast={info['feast_name']} title={info['title']!r}"
            )
    # also report the best liturgical distance vs the 10th-place distance
    tenth_dist = knn[9]["distance"] if len(knn) >= 10 else knn[-1]["distance"]
    best_lit = lit_hits_top100[0][1]["distance"] if lit_hits_top100 else None
    print(
        f"\nBenchmark: 10th-ranked distance = {tenth_dist:.3f}; "
        f"best liturgical distance = {best_lit if best_lit is not None else 'N/A'}"
    )


def main() -> None:
    SEEDS["prose seed_02 (mercy / Bloom)"] = _load_prose_seed_02()
    conn = commonplace_db.connect()
    try:
        # quick sanity counts
        total_docs = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
        lit_docs = conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE content_type = 'liturgical_unit'"
        ).fetchone()["n"]
        lit_meta = conn.execute("SELECT COUNT(*) AS n FROM liturgical_unit_meta").fetchone()["n"]
        lit_vecs = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks c "
            "JOIN documents d ON d.id = c.document_id "
            "JOIN chunk_vectors cv ON cv.chunk_id = c.id "
            "WHERE d.content_type = 'liturgical_unit'"
        ).fetchone()["n"]
        print(
            f"DB sanity: documents={total_docs}, liturgical_unit docs={lit_docs}, "
            f"liturgical_unit_meta rows={lit_meta}, lit chunks with vectors={lit_vecs}"
        )

        for label, seed in SEEDS.items():
            probe(label, seed, conn, top_n=100)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
