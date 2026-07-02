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
import re
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import commonplace_db
from commonplace_server.embedding import embed, pack_vector
from commonplace_server.search import (
    SearchCancelledError,
    SearchResult,
    SearchTimeoutError,
    search,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JUDGE_SKILL_MD = Path(__file__).parent.parent / "skills" / "judge_serendipity" / "SKILL.md"
JUDGE_PARSER_PATH = Path(__file__).parent.parent / "skills" / "judge_serendipity" / "parser.py"
DIRECTIVES_PATH = Path("~/commonplace/skills/judge_serendipity/directives.md").expanduser()

JUDGE_TIMEOUT = 60  # seconds — an empty-payload claude -p call alone takes ~9s
SEARCH_TIMEOUT = 10.0  # seconds — leave headroom for the judge and MCP client

_MAX_DISTANCE_FOR_SIMILARITY = 1.0  # treat distance > 1 as similarity 0

JudgeErrorKind = Literal[
    "timeout", "exit_nonzero", "os_error", "cancelled", "rate_limited"
]

# Substrings (lowercased) in claude CLI output that mean the account budget is
# exhausted rather than the judge being broken. Ambient surfacing fires while
# the user is actively chatting — exactly when limits are most likely hit — so
# these must be distinguishable in telemetry from real judge failures.
_RATE_LIMIT_MARKERS = ("session limit", "rate limit", "usage limit")


@dataclass(frozen=True)
class JudgeFailure:
    """Structured judge failure for precise response and telemetry reporting."""

    kind: JudgeErrorKind
    message: str
    detail: str | None = None


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


# Metadata-assisted hydration ------------------------------------------------

_PHRASE_PREFIXES_TO_STRIP = (
    "An Order for ",
    "A Collect for ",
    "Collect for ",
    "A Prayer of ",
    "A Prayer for ",
    "A Litany of ",
)

_MIN_PHRASE_LEN = 4
_MIN_DERIVED_PHRASE_LEN = 6


def _derive_match_phrases(title: str) -> list[str]:
    """Return phrases from a document title worth substring-matching in a seed.

    Returns the full title plus, when applicable, the bare subject (after
    stripping liturgical prefixes like "Collect for ") and any parenthesized
    clause. Full titles need ≥``_MIN_PHRASE_LEN`` chars; derived phrases
    (prefix-stripped, paren-extracted) need ≥``_MIN_DERIVED_PHRASE_LEN`` chars
    — "Collect for Peace" → "Peace" (5) must NOT match any seed using the
    common word "peace", but "An Order for Compline" → "Compline" (8) is
    specific enough to hydrate on seeds mentioning the office by name.
    """
    if not title:
        return []
    full = title.strip()
    derived: list[str] = []
    for prefix in _PHRASE_PREFIXES_TO_STRIP:
        if title.startswith(prefix) and len(title) > len(prefix):
            derived.append(title[len(prefix):].strip())
    paren = re.search(r"\(([^)]+)\)", title)
    if paren:
        derived.append(paren.group(1).strip())
    seen: set[str] = set()
    out: list[str] = []
    if len(full) >= _MIN_PHRASE_LEN:
        seen.add(full.lower())
        out.append(full)
    for p in derived:
        key = p.lower()
        if len(p) >= _MIN_DERIVED_PHRASE_LEN and key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _phrase_in_seed(phrase: str, seed_lower: str) -> bool:
    """Case-insensitive word-boundary substring match."""
    return bool(re.search(r"\b" + re.escape(phrase.lower()) + r"\b", seed_lower))


def _hydrate_title_matches(
    conn: sqlite3.Connection, seed: str, limit: int = 5
) -> list[SearchResult]:
    """Find liturgical units or LFF commemoration bios whose canonical title
    (or derived phrase) appears as a word-boundary match in ``seed``.

    When the user's seed textually reaches for a canonical unit — "Julian of
    Norwich", "Compline", "Psalm 23", "Ash Wednesday" — vector search can
    still miss it if the unit's own text centres on different vocabulary
    (e.g., Julian's LFF collect focuses on "offering" rather than "love as
    meaning"). A textual title match is unambiguous evidence the user is
    reaching for the canonical unit, so we surface it independently.

    Returns up to ``limit`` synthetic ``SearchResult`` entries (score=0.0)
    preferring the longest-matched phrase per document. Only liturgical
    units and LFF bios are eligible — ordinary prose / book / capture
    documents are excluded to avoid false positives on arbitrary titles.
    Caller deduplicates against existing vector hits by ``document_id``.
    """
    seed_lower = seed.lower()
    sql = """
        SELECT
            d.id AS document_id,
            d.content_type,
            d.source_id,
            d.source_uri,
            d.title,
            d.created_at,
            (SELECT c.text FROM chunks c
             WHERE c.document_id = d.id
             ORDER BY c.chunk_index LIMIT 1) AS chunk_text
        FROM documents d
        WHERE d.title IS NOT NULL
          AND LENGTH(d.title) >= ?
          AND (
              d.content_type = 'liturgical_unit'
              OR d.id IN (SELECT document_id FROM commemoration_bio)
          )
    """
    matches: list[tuple[int, SearchResult]] = []
    for row in conn.execute(sql, (_MIN_PHRASE_LEN,)):
        best_len = 0
        for phrase in _derive_match_phrases(row["title"]):
            if _phrase_in_seed(phrase, seed_lower):
                best_len = max(best_len, len(phrase))
        if best_len > 0:
            matches.append(
                (
                    best_len,
                    SearchResult(
                        score=0.0,
                        document_id=row["document_id"],
                        content_type=row["content_type"],
                        source_id=row["source_id"],
                        source_uri=row["source_uri"],
                        title=row["title"],
                        chunk_text=row["chunk_text"] or "",
                        created_at=row["created_at"],
                    ),
                )
            )
    matches.sort(key=lambda m: -m[0])
    return [sr for _, sr in matches[:limit]]


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


def _invoke_judge_subprocess(
    judge_json: str,
    cancel_event: threading.Event | None = None,
) -> tuple[str | None, JudgeFailure | None]:
    """Run claude -p with judge_serendipity SKILL.md (single attempt).

    Returns raw stdout on success and a structured failure otherwise.
    """
    if cancel_event is not None and cancel_event.is_set():
        return None, JudgeFailure("cancelled", "surface invocation cancelled")

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
            detail = (
                f"exit_code={result.returncode}; "
                f"stderr_tail={result.stderr[-500:]!r}; "
                f"stdout_tail={result.stdout[-500:]!r}"
            )
            logger.warning(
                "judge_serendipity returned non-zero exit %d: stderr_tail=%r "
                "stdout_tail=%r",
                result.returncode,
                result.stderr[-500:],
                result.stdout[-500:],
            )
            combined_output = f"{result.stdout}\n{result.stderr}".lower()
            if any(marker in combined_output for marker in _RATE_LIMIT_MARKERS):
                return None, JudgeFailure(
                    "rate_limited",
                    "judge unavailable: claude session/usage limit exhausted",
                    detail,
                )
            return None, JudgeFailure(
                "exit_nonzero",
                f"judge exited with code {result.returncode}",
                detail,
            )
        if cancel_event is not None and cancel_event.is_set():
            return None, JudgeFailure("cancelled", "surface invocation cancelled")
        return result.stdout, None
    except subprocess.TimeoutExpired as exc:
        logger.warning("judge_serendipity timed out after %ds", JUDGE_TIMEOUT)
        return None, JudgeFailure(
            "timeout",
            f"judge timed out after {JUDGE_TIMEOUT}s",
            str(exc),
        )
    except OSError as exc:
        logger.error("Failed to invoke claude for judge_serendipity: %s", exc)
        return None, JudgeFailure("os_error", "judge process failed to start", str(exc))


def _invoke_judge(
    judge_json: str,
    cancel_event: threading.Event | None = None,
) -> tuple[str | None, Any | None, JudgeFailure | str | None]:
    """Run the judge with parse-retry-once.

    Haiku judge stdout occasionally returns malformed JSON (~5% rate, 1–3
    invocations per 30-seed replay). Rerun the subprocess once on parse
    failure; log the tail of malformed stdout both times.

    Returns a ``(raw, judgment, error_note)`` triple:

    - ``(raw, judgment, None)`` — parse succeeded (attempt 1 or attempt 2).
    - ``(None, None, JudgeFailure(...))`` — subprocess failed.
    - ``(raw, None, 'judge output unparseable')`` — both attempts unparseable.
    """
    parser = _load_judge_parser()
    last_raw: str | None = None
    for attempt in (1, 2):
        raw, failure = _invoke_judge_subprocess(judge_json, cancel_event)
        if failure is not None:
            return None, None, failure
        assert raw is not None
        last_raw = raw
        try:
            cleaned = parser.strip_code_fences(raw)
            judgment = parser.parse(cleaned)
            if attempt == 2:
                logger.info("judge_serendipity recovered after parse retry")
            return raw, judgment, None
        except Exception as exc:
            logger.warning(
                "judge_serendipity output unparseable (attempt %d/2): %s; stdout_tail=%r",
                attempt,
                exc,
                raw[-500:],
            )
    return last_raw, None, "judge output unparseable"


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


def _candidate_for_telemetry(candidate: dict[str, Any]) -> dict[str, Any]:
    """Small candidate summary for surface telemetry."""
    out: dict[str, Any] = {
        "id": candidate.get("id"),
        "source_type": candidate.get("source_type"),
        "source_title": candidate.get("source_title"),
        "source_uri": candidate.get("source_uri"),
        "similarity_score": candidate.get("similarity_score"),
        "last_engaged_days_ago": candidate.get("last_engaged_days_ago"),
    }
    for liturgical_field in ("category", "genre", "feast_name", "tradition"):
        if liturgical_field in candidate:
            out[liturgical_field] = candidate[liturgical_field]
    return out


def _accepted_for_telemetry(item: dict[str, Any]) -> dict[str, Any]:
    """Small accepted-item summary for surface telemetry."""
    out = _candidate_for_telemetry(item)
    out["reason"] = item.get("reason")
    return out


def _triangulation_for_telemetry(group: dict[str, Any]) -> dict[str, Any]:
    """Small triangulation summary for surface telemetry."""
    return {
        "reason": group.get("reason"),
        "ids": [item.get("id") for item in group.get("items", [])],
        "items": [
            _candidate_for_telemetry(item)
            for item in group.get("items", [])
        ],
    }


def _begin_surface_invocation(
    *,
    db_path: str,
    seed: str,
    mode: str,
    types: list[str] | None,
    limit: int,
    similarity_floor: float,
    recency_bias: bool,
) -> int | None:
    """Insert telemetry before work starts so crashes leave an inspectable row."""
    if db_path == ":memory:":
        return None

    conn: sqlite3.Connection | None = None
    try:
        conn = commonplace_db.connect(db_path)
        conn.execute("PRAGMA busy_timeout=1000")
        commonplace_db.migrate(conn)
        cursor = conn.execute(
            """
            INSERT INTO surface_invocations (
                seed,
                mode,
                types,
                requested_limit,
                similarity_floor,
                recency_bias,
                judge_status,
                elapsed_ms,
                invocation_status,
                stage,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'not_called', 0, 'running', 'embedding',
                      strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (
                seed,
                mode,
                json.dumps(types or [], ensure_ascii=False),
                limit,
                similarity_floor,
                1 if recency_bias else 0,
            ),
        )
        conn.commit()
        if cursor.lastrowid is None:
            logger.warning("surface telemetry begin returned no row id")
            return None
        return int(cursor.lastrowid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("surface telemetry begin failed: %s", exc)
        return None
    finally:
        if conn is not None:
            conn.close()


def _set_surface_stage(
    *,
    db_path: str,
    invocation_id: int | None,
    stage: str,
    invocation_status: str = "running",
    note: str | None = None,
    error: str | None = None,
    judge_error_kind: str | None = None,
) -> None:
    """Best-effort stage/status update for an in-progress invocation."""
    if invocation_id is None:
        return

    completed_sql = (
        "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
        if invocation_status != "running"
        else "completed_at"
    )
    conn: sqlite3.Connection | None = None
    try:
        conn = commonplace_db.connect(db_path)
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute(
            f"""
            UPDATE surface_invocations
               SET stage = ?,
                   invocation_status = ?,
                   note = COALESCE(?, note),
                   error = COALESCE(?, error),
                   judge_error_kind = COALESCE(?, judge_error_kind),
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                   completed_at = {completed_sql}
             WHERE id = ?
               AND invocation_status = 'running'
            """,
            (
                stage,
                invocation_status,
                note,
                error,
                judge_error_kind,
                invocation_id,
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "surface telemetry stage update failed: id=%s stage=%s error=%s",
            invocation_id,
            stage,
            exc,
        )
    finally:
        if conn is not None:
            conn.close()


def _complete_surface_invocation(
    *,
    db_path: str,
    invocation_id: int | None,
    started_at: float,
    raw_candidate_count: int = 0,
    floor_candidate_count: int = 0,
    judge_status: str = "not_called",
    invocation_status: str = "complete",
    stage: str = "complete",
    note: str | None = None,
    error: str | None = None,
    judge_error_kind: str | None = None,
    rejected_count: int | None = None,
    accepted: list[dict[str, Any]] | None = None,
    triangulation_groups: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> None:
    """Finish the row created by :func:`_begin_surface_invocation`."""
    if invocation_id is None:
        return

    elapsed_ms = (time.monotonic() - started_at) * 1000
    accepted_log = [_accepted_for_telemetry(item) for item in (accepted or [])]
    triangulation_log = [
        _triangulation_for_telemetry(group) for group in (triangulation_groups or [])
    ]
    candidate_log = [
        _candidate_for_telemetry(candidate) for candidate in (candidates or [])
    ]

    conn: sqlite3.Connection | None = None
    try:
        conn = commonplace_db.connect(db_path)
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute(
            """
            UPDATE surface_invocations
               SET raw_candidate_count = ?,
                   floor_candidate_count = ?,
                   judge_status = ?,
                   invocation_status = ?,
                   stage = ?,
                   note = ?,
                   error = ?,
                   judge_error_kind = ?,
                   rejected_count = ?,
                   accepted_json = ?,
                   triangulation_json = ?,
                   candidates_json = ?,
                   elapsed_ms = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                   completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
             WHERE id = ?
               AND invocation_status = 'running'
            """,
            (
                raw_candidate_count,
                floor_candidate_count,
                judge_status,
                invocation_status,
                stage,
                note,
                error,
                judge_error_kind,
                rejected_count,
                json.dumps(accepted_log, ensure_ascii=False),
                json.dumps(triangulation_log, ensure_ascii=False),
                json.dumps(candidate_log, ensure_ascii=False),
                elapsed_ms,
                invocation_id,
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "surface telemetry completion failed: id=%s error=%s",
            invocation_id,
            exc,
        )
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Core surface function
# ---------------------------------------------------------------------------


def run_surface(
    seed: str,
    mode: str = "ambient",
    types: list[str] | None = None,
    limit: int = 10,
    similarity_floor: float = 0.25,
    recency_bias: bool = True,
    db_path: str | None = None,
    invocation_id: int | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Core surface logic — DB + subprocess but no MCP framework glue.

    Separated from the MCP tool registration so it's directly testable.
    """
    # Step 0 — validate seed
    if not seed or not seed.strip():
        return {"accepted": [], "triangulation_groups": [], "note": "empty seed"}

    resolved_db = str(
        db_path or os.environ.get("COMMONPLACE_DB_PATH", commonplace_db.DB_PATH)
    )
    started_at = time.monotonic()
    if invocation_id is None:
        invocation_id = _begin_surface_invocation(
            db_path=resolved_db,
            seed=seed,
            mode=mode,
            types=types,
            limit=limit,
            similarity_floor=similarity_floor,
            recency_bias=recency_bias,
        )

    def finish(
        result: dict[str, Any],
        *,
        raw_candidate_count: int = 0,
        floor_candidate_count: int = 0,
        judge_status: str = "not_called",
        invocation_status: str = "complete",
        stage: str = "complete",
        error: str | None = None,
        judge_error_kind: str | None = None,
        candidates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        _complete_surface_invocation(
            db_path=resolved_db,
            invocation_id=invocation_id,
            started_at=started_at,
            raw_candidate_count=raw_candidate_count,
            floor_candidate_count=floor_candidate_count,
            judge_status=judge_status,
            invocation_status=invocation_status,
            stage=stage,
            note=result.get("note"),
            error=error,
            judge_error_kind=judge_error_kind,
            rejected_count=result.get("rejected_count"),
            accepted=result.get("accepted"),
            triangulation_groups=result.get("triangulation_groups"),
            candidates=candidates,
        )
        return result

    if cancel_event is not None and cancel_event.is_set():
        return finish(
            {
                "accepted": [],
                "triangulation_groups": [],
                "note": "surface invocation cancelled",
            },
            invocation_status="cancelled",
            stage="cancelled",
            error="surface invocation cancelled before embedding",
        )

    # Step 1 — embed seed
    try:
        vectors = embed([seed.strip()])
    except Exception as exc:
        logger.error("Embedding failed for surface seed: %s", exc)
        return finish(
            {
                "accepted": [],
                "triangulation_groups": [],
                "note": f"embedding failed: {exc}",
            },
            judge_status="embedding_failed",
            invocation_status="failed",
            stage="embedding",
            error=str(exc),
        )

    query_blob = pack_vector(vectors[0])
    _set_surface_stage(
        db_path=resolved_db,
        invocation_id=invocation_id,
        stage="search",
    )

    # Step 2 — vector search
    conn = commonplace_db.connect(resolved_db)
    liturgical_meta_by_doc: dict[int, dict[str, Any]] = {}
    try:
        commonplace_db.migrate(conn)
        search_deadline = time.monotonic() + SEARCH_TIMEOUT

        if types:
            # Fan out: run search per type and merge, then sort by score
            all_results: list[SearchResult] = []
            per_type_limit = limit * 2  # overfetch per type
            for t in types:
                remaining = search_deadline - time.monotonic()
                if remaining <= 0:
                    raise SearchTimeoutError(
                        f"semantic search exceeded {SEARCH_TIMEOUT:g}s"
                    )
                partial = search(
                    conn,
                    query_blob,
                    content_type=t,
                    limit=per_type_limit,
                    timeout_seconds=remaining,
                    cancel_event=cancel_event,
                )
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
            raw_results = search(
                conn,
                query_blob,
                limit=limit,
                timeout_seconds=SEARCH_TIMEOUT,
                cancel_event=cancel_event,
            )

        # Metadata-assisted hydration: inject liturgical / bio documents whose
        # canonical title (or derived phrase) appears as a word-boundary match
        # in the seed. Bypasses vector-retrieval gaps on seeds that explicitly
        # name a feast / saint / office (e.g., "Julian of Norwich", "Compline",
        # "Psalm 23") — the embedding doesn't always rank the named unit near
        # the seed, but a textual match is unambiguous evidence the user is
        # reaching for the canonical unit. See Phase 4 Wave 4.14 path R.
        metadata_hits = _hydrate_title_matches(conn, seed)
        existing_doc_ids = {r.document_id for r in raw_results}
        raw_results = list(raw_results) + [
            r for r in metadata_hits if r.document_id not in existing_doc_ids
        ]

        # Look up liturgical metadata for any liturgical_unit candidates before
        # the connection closes. Non-liturgical docs contribute nothing.
        liturgical_doc_ids = [
            r.document_id for r in raw_results if r.content_type == "liturgical_unit"
        ]
        liturgical_meta_by_doc = _fetch_liturgical_meta(conn, liturgical_doc_ids)

    except SearchTimeoutError as exc:
        logger.warning("surface semantic search timed out: %s", exc)
        return finish(
            {
                "accepted": [],
                "triangulation_groups": [],
                "note": str(exc),
            },
            invocation_status="failed",
            stage="search",
            error=str(exc),
        )
    except SearchCancelledError as exc:
        logger.info("surface semantic search cancelled")
        return finish(
            {
                "accepted": [],
                "triangulation_groups": [],
                "note": "surface invocation cancelled",
            },
            invocation_status="cancelled",
            stage="cancelled",
            error=str(exc),
        )
    except Exception as exc:
        _set_surface_stage(
            db_path=resolved_db,
            invocation_id=invocation_id,
            stage="search",
            invocation_status="failed",
            note="semantic search failed",
            error=str(exc),
        )
        raise
    finally:
        conn.close()

    if not raw_results:
        return finish(
            {
                "accepted": [],
                "triangulation_groups": [],
                "note": "no candidates above similarity floor",
            }
        )

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
        return finish(
            {
                "accepted": [],
                "triangulation_groups": [],
                "note": "no candidates above similarity floor",
            },
            raw_candidate_count=len(raw_results),
        )

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

    # Step 5 + 6 — invoke judge with parse-retry-once
    _set_surface_stage(
        db_path=resolved_db,
        invocation_id=invocation_id,
        stage="judge",
    )
    raw_output, judgment, judge_error = _invoke_judge(judge_json, cancel_event)
    if judge_error is not None:
        judge_error_kind: str
        if isinstance(judge_error, JudgeFailure):
            error_note = judge_error.message
            judge_error_kind = judge_error.kind
            error_detail = judge_error.detail or judge_error.message
            invocation_status = (
                "cancelled" if judge_error.kind == "cancelled" else "failed"
            )
            stage = "cancelled" if judge_error.kind == "cancelled" else "judge"
        else:
            error_note = judge_error
            judge_error_kind = "unparseable"
            error_detail = judge_error
            invocation_status = "failed"
            stage = "judge"
        error_result: dict[str, Any] = {
            "accepted": [],
            "triangulation_groups": [],
            "note": error_note,
        }
        if raw_output is not None:
            error_result["raw"] = raw_output[:200]
        judge_status = (
            "judge_unparseable"
            if error_note == "judge output unparseable"
            else "judge_failed"
        )
        return finish(
            error_result,
            raw_candidate_count=len(raw_results),
            floor_candidate_count=len(candidates_with_meta),
            judge_status=judge_status,
            invocation_status=invocation_status,
            stage=stage,
            error=error_detail,
            judge_error_kind=judge_error_kind,
            candidates=candidates_with_meta,
        )
    assert judgment is not None  # error_note is None ⇒ parse succeeded

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

    return finish(
        {
            "seed": seed,
            "mode": mode,
            "accepted": hydrated_accepted,
            "triangulation_groups": hydrated_triangulation,
            "rejected_count": rejected_count,
        },
        raw_candidate_count=len(raw_results),
        floor_candidate_count=len(candidates_with_meta),
        judge_status="success",
        candidates=candidates_with_meta,
    )
