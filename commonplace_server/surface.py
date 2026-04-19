"""Surface MCP tool implementation — serendipity engine.

Two-pass filter:
  1. Semantic search returns top ~10 candidates.
  2. Similarity floor drops weak matches. If none pass, skip silently.
  3. Judge pass via judge_serendipity (Haiku) rejects shallow matches.
  4. Cap at 2 accepted items total.

Supports ambient (stingy) and on_demand (permissive) modes.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import commonplace_db
from commonplace_server.embedding import embed, pack_vector
from commonplace_server.search import SearchResult, search

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JUDGE_SKILL_MD = Path(__file__).parent.parent / "skills" / "judge_serendipity" / "SKILL.md"
JUDGE_PARSER_PATH = Path(__file__).parent.parent / "skills" / "judge_serendipity" / "parser.py"
DIRECTIVES_PATH = Path("~/commonplace/skills/judge_serendipity/directives.md").expanduser()

JUDGE_TIMEOUT = 30  # seconds — ambient surfacing must not block chat

_MAX_DISTANCE_FOR_SIMILARITY = 1.0  # treat distance > 1 as similarity 0


# ---------------------------------------------------------------------------
# Parser loading (importlib dance to avoid cross-skill collision)
# ---------------------------------------------------------------------------


def _load_judge_parser() -> Any:
    """Load judge_serendipity/parser.py under a unique module name."""
    spec = importlib.util.spec_from_file_location(
        "surface_judge_parser", JUDGE_PARSER_PATH
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load judge parser from {JUDGE_PARSER_PATH}")
    mod = importlib.util.module_from_spec(spec)
    if "surface_judge_parser" not in sys.modules:
        sys.modules["surface_judge_parser"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["surface_judge_parser"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _distance_to_similarity(distance: float) -> float:
    """Convert sqlite-vec cosine distance to a 0-1 similarity score."""
    return max(0.0, min(1.0, 1.0 - distance))


def _days_since(created_at: str) -> int | None:
    """Compute how many days ago a document was created.

    Returns None if created_at is missing or unparseable.
    """
    if not created_at:
        return None
    try:
        # Parse ISO 8601 — may be "2025-03-15T10:00:00Z" or "2025-03-15"
        if "T" in created_at:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            doc_date = dt.date()
        else:
            doc_date = date.fromisoformat(created_at[:10])
        today = datetime.now(UTC).date()
        return max(0, (today - doc_date).days)
    except (ValueError, TypeError):
        return None


def _load_directives() -> list[str]:
    """Load accumulated directives from the directives file. Empty list if missing."""
    try:
        text = DIRECTIVES_PATH.read_text(encoding="utf-8").strip()
        if not text:
            return []
        # Each non-empty line is a directive
        return [line.strip() for line in text.splitlines() if line.strip()]
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("Could not read directives file %s: %s", DIRECTIVES_PATH, exc)
        return []


def _build_candidate_id(result: SearchResult, chunk_idx: int) -> str:
    """Build a stable candidate id from document_id and chunk index."""
    return f"{result.document_id}:{chunk_idx}"


def _fetch_liturgical_meta(
    conn: sqlite3.Connection, document_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Look up liturgical metadata for the given document_ids.

    Returns a map ``{document_id: {"category", "genre", "tradition", "feast_name"}}``
    containing only the documents that have a row in ``liturgical_unit_meta``.
    Non-liturgical documents are absent from the returned map.

    ``feast_name`` is resolved via the optional ``calendar_anchor_id`` JOIN on
    the ``feast`` table; it is ``None`` when the unit has no calendar anchor
    (e.g., seasonal collects, Psalter verses).
    """
    if not document_ids:
        return {}
    placeholders = ",".join("?" * len(document_ids))
    sql = (
        "SELECT lum.document_id, lum.category, lum.genre, lum.tradition, "
        "       f.primary_name AS feast_name "
        "FROM liturgical_unit_meta lum "
        "LEFT JOIN feast f ON f.id = lum.calendar_anchor_id "
        f"WHERE lum.document_id IN ({placeholders})"
    )
    rows = conn.execute(sql, document_ids).fetchall()
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        out[row["document_id"]] = {
            "category": row["category"],
            "genre": row["genre"],
            "tradition": row["tradition"],
            "feast_name": row["feast_name"],
        }
    return out


def _build_judge_input(
    seed: str,
    mode: str,
    candidates: list[dict[str, Any]],
    directives: list[str],
) -> str:
    """Serialise the judge's input JSON."""
    return json.dumps(
        {
            "seed": seed,
            "mode": mode,
            "candidates": candidates,
            "accumulated_directives": directives,
        },
        ensure_ascii=False,
    )


def _invoke_judge(judge_json: str) -> str | None:
    """Run claude -p with judge_serendipity SKILL.md.

    Returns raw stdout on success, None on timeout or subprocess error.
    """
    claude_bin = os.environ.get("CLAUDE_BIN", "claude")
    cmd = [
        claude_bin,
        "-p",
        "--system-prompt-file",
        str(JUDGE_SKILL_MD),
        "--model",
        "haiku",
        judge_json,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=JUDGE_TIMEOUT,
        )
        if result.returncode != 0:
            logger.warning(
                "judge_serendipity returned non-zero exit %d: %s",
                result.returncode,
                result.stderr[:200],
            )
            return None
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.warning("judge_serendipity timed out after %ds", JUDGE_TIMEOUT)
        return None
    except OSError as exc:
        logger.error("Failed to invoke claude for judge_serendipity: %s", exc)
        return None


def _hydrate_item(
    candidate_map: dict[str, dict[str, Any]],
    item_id: str,
) -> dict[str, Any]:
    """Return a fully-hydrated item dict from the candidate map.

    Liturgical candidates carry four extra fields (``category``, ``genre``,
    ``feast_name``, ``tradition``) attached during candidate assembly; they
    surface here so the MCP response mirrors what the judge saw.
    """
    cand = candidate_map.get(item_id, {})
    item: dict[str, Any] = {
        "id": item_id,
        "source_type": cand.get("source_type", ""),
        "source_title": cand.get("source_title", ""),
        "source_uri": cand.get("source_uri", ""),
        "text": cand.get("full_text", ""),
        "similarity_score": cand.get("similarity_score", 0.0),
        "last_engaged_days_ago": cand.get("last_engaged_days_ago"),
    }
    for liturgical_field in ("category", "genre", "feast_name", "tradition"):
        if liturgical_field in cand:
            item[liturgical_field] = cand[liturgical_field]
    return item


# ---------------------------------------------------------------------------
# Core surface function
# ---------------------------------------------------------------------------


def run_surface(
    seed: str,
    mode: str = "ambient",
    types: list[str] | None = None,
    limit: int = 10,
    similarity_floor: float = 0.55,
    recency_bias: bool = True,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Core surface logic — DB + subprocess but no MCP framework glue.

    Separated from the MCP tool registration so it's directly testable.
    """
    # Step 0 — validate seed
    if not seed or not seed.strip():
        return {"accepted": [], "triangulation_groups": [], "note": "empty seed"}

    # Step 1 — embed seed
    try:
        vectors = embed([seed.strip()])
    except Exception as exc:
        logger.error("Embedding failed for surface seed: %s", exc)
        return {
            "accepted": [],
            "triangulation_groups": [],
            "note": f"embedding failed: {exc}",
        }

    query_blob = pack_vector(vectors[0])

    # Step 2 — vector search
    resolved_db = db_path or os.environ.get("COMMONPLACE_DB_PATH", commonplace_db.DB_PATH)
    conn = commonplace_db.connect(resolved_db)
    liturgical_meta_by_doc: dict[int, dict[str, Any]] = {}
    try:
        commonplace_db.migrate(conn)

        if types:
            # Fan out: run search per type and merge, then sort by score
            all_results: list[SearchResult] = []
            per_type_limit = limit * 2  # overfetch per type
            for t in types:
                partial = search(conn, query_blob, content_type=t, limit=per_type_limit)
                all_results.extend(partial)
            # Sort by distance (ascending = best match first), deduplicate by document_id
            seen_docs: set[int] = set()
            merged: list[SearchResult] = []
            for r in sorted(all_results, key=lambda x: x.score):
                if r.document_id not in seen_docs:
                    seen_docs.add(r.document_id)
                    merged.append(r)
            raw_results = merged[:limit]
        else:
            raw_results = search(conn, query_blob, limit=limit)

        # Look up liturgical metadata for any liturgical_unit candidates before
        # the connection closes. Non-liturgical docs contribute nothing.
        liturgical_doc_ids = [
            r.document_id for r in raw_results if r.content_type == "liturgical_unit"
        ]
        liturgical_meta_by_doc = _fetch_liturgical_meta(conn, liturgical_doc_ids)

    finally:
        conn.close()

    if not raw_results:
        return {
            "accepted": [],
            "triangulation_groups": [],
            "note": "no candidates above similarity floor",
        }

    # Step 3 — similarity floor filter
    # SearchResult.score is a distance (lower = better); convert to similarity
    candidates_with_meta: list[dict[str, Any]] = []
    for idx, result in enumerate(raw_results):
        sim = _distance_to_similarity(result.score)
        if sim < similarity_floor:
            continue
        days_ago = _days_since(result.created_at) if recency_bias else None
        cid = _build_candidate_id(result, idx)
        cand: dict[str, Any] = {
            "id": cid,
            "source_type": result.content_type,
            "source_title": result.title or "",
            "source_uri": result.source_uri or "",
            "text": result.chunk_text[:500],
            "full_text": result.chunk_text,
            "similarity_score": round(sim, 4),
            "last_engaged_days_ago": days_ago,
        }
        # Hydrate liturgical fields per task 4.6 contract. Only attach when
        # the candidate is a liturgical_unit AND we have a meta row — skipping
        # the fields entirely for non-liturgical candidates keeps the judge
        # payload minimal for prose.
        lit_meta = liturgical_meta_by_doc.get(result.document_id)
        if lit_meta is not None:
            cand["category"] = lit_meta["category"]
            cand["genre"] = lit_meta["genre"]
            cand["feast_name"] = lit_meta["feast_name"]
            cand["tradition"] = lit_meta["tradition"]
        candidates_with_meta.append(cand)

    if not candidates_with_meta:
        return {
            "accepted": [],
            "triangulation_groups": [],
            "note": "no candidates above similarity floor",
        }

    # Step 4 — build judge input
    directives = _load_directives()
    judge_candidates: list[dict[str, Any]] = []
    for c in candidates_with_meta:
        judge_cand: dict[str, Any] = {
            "id": c["id"],
            "source_type": c["source_type"],
            "source_title": c["source_title"],
            "text": c["text"],
            "similarity_score": c["similarity_score"],
            "last_engaged_days_ago": c["last_engaged_days_ago"],
        }
        # Forward liturgical fields to the judge when present so it can
        # reason about category/genre/feast/tradition alongside prose.
        for liturgical_field in ("category", "genre", "feast_name", "tradition"):
            if liturgical_field in c:
                judge_cand[liturgical_field] = c[liturgical_field]
        judge_candidates.append(judge_cand)
    judge_json = _build_judge_input(seed, mode, judge_candidates, directives)

    # Step 5 — invoke judge
    raw_output = _invoke_judge(judge_json)
    if raw_output is None:
        # Timeout or subprocess error — fail silently
        return {"accepted": [], "triangulation_groups": [], "note": "judge timed out or failed"}

    # Step 6 — parse judge output (strip code fences first)
    try:
        parser = _load_judge_parser()
        cleaned = parser.strip_code_fences(raw_output)
        judgment = parser.parse(cleaned)
    except Exception as exc:
        logger.warning("judge_serendipity output unparseable: %s", exc)
        return {
            "accepted": [],
            "triangulation_groups": [],
            "note": "judge output unparseable",
            "raw": raw_output[:200],
        }

    # Step 7 — hydrate accepted/triangulation items from candidate map
    candidate_map: dict[str, dict[str, Any]] = {c["id"]: c for c in candidates_with_meta}

    hydrated_accepted = []
    for entry in judgment.accepted:
        item = _hydrate_item(candidate_map, entry.id)
        item["reason"] = entry.reason
        hydrated_accepted.append(item)

    hydrated_triangulation = []
    for group in judgment.triangulation_groups:
        group_items = [_hydrate_item(candidate_map, gid) for gid in group.ids]
        hydrated_triangulation.append(
            {
                "reason": group.reason,
                "items": group_items,
            }
        )

    rejected_count = len(judgment.rejected)

    return {
        "seed": seed,
        "mode": mode,
        "accepted": hydrated_accepted,
        "triangulation_groups": hydrated_triangulation,
        "rejected_count": rejected_count,
    }
