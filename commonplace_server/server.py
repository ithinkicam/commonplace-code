"""Commonplace MCP server — FastMCP app with healthcheck tool and HTTP route.

Bind address defaults to 127.0.0.1:8765 (Tailscale terminates locally).
Override via COMMONPLACE_HOST / COMMONPLACE_PORT environment variables.
"""

from __future__ import annotations

import importlib.metadata
import logging
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

import commonplace_db
import commonplace_server.jobs as jobs
import commonplace_server.progress as progress
from commonplace_server.accept_middleware import AcceptHeaderMiddleware
from commonplace_server.capture import handle_capture, resolve_bearer
from commonplace_server.corrections import correct_book, correct_judge, correct_profile
from commonplace_server.mcp_token import resolve_mcp_token
from commonplace_server.search import results_to_dicts
from commonplace_server.search import search as search_commonplace_impl
from commonplace_server.subject_frequency import report as _subject_frequency_report
from commonplace_server.surface import run_surface

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastMCP app instance (module-level, but no network/DB side effects on import)
# ---------------------------------------------------------------------------

mcp: FastMCP = FastMCP(name="commonplace")

# ---------------------------------------------------------------------------
# Capture bearer token (resolved once at import time)
# ---------------------------------------------------------------------------

_CAPTURE_BEARER: str | None = resolve_bearer()

if _CAPTURE_BEARER is None:
    logger.warning(
        "COMMONPLACE_CAPTURE_BEARER env var not set and keychain lookup failed. "
        "POST /capture will reject all requests with 503 until the bearer is configured."
    )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_version() -> str:
    """Return the package version from package metadata."""
    try:
        return importlib.metadata.version("commonplace")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.1"


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Run migrations against *conn* and return the resulting schema version."""
    return commonplace_db.migrate(conn)


def _build_health_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "commonplace",
        "version": _get_version(),
        "schema_version": _get_schema_version(conn),
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------


def healthcheck() -> dict[str, Any]:
    """Return service health, version, and DB schema version."""
    db_path = os.environ.get(
        "COMMONPLACE_DB_PATH",
        commonplace_db.DB_PATH,
    )
    conn = commonplace_db.connect(db_path)
    try:
        return _build_health_payload(conn)
    finally:
        conn.close()


# Register the function as an MCP tool.
# mcp.tool(fn) accepts a bare function and returns a FunctionTool;
# the original `healthcheck` name stays callable for tests.
mcp.tool(healthcheck)


# ---------------------------------------------------------------------------
# Job-queue MCP tools
# ---------------------------------------------------------------------------


def submit_job(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Submit a new job to the job queue.

    Parameters
    ----------
    kind:
        Non-empty string ≤ 64 characters identifying the job type.
    payload:
        A JSON-serialisable dict of handler-specific parameters.

    Returns
    -------
    ``{"id": <int>, "status": "queued", "kind": kind}``
    """
    db_path = os.environ.get("COMMONPLACE_DB_PATH", commonplace_db.DB_PATH)
    conn = commonplace_db.connect(db_path)
    try:
        commonplace_db.migrate(conn)
        return jobs.submit(conn, kind, payload)
    finally:
        conn.close()


mcp.tool(submit_job)


def get_job_status(job_id: int) -> dict[str, Any]:
    """Return the current status and metadata for a job queue entry.

    Parameters
    ----------
    job_id:
        The integer primary key of the job_queue row.

    Returns
    -------
    Dict with keys ``id, kind, status, created_at, started_at,
    completed_at, error, attempts, payload`` (payload JSON-decoded).

    Raises
    ------
    ValueError
        If no job with the given id exists.
    """
    db_path = os.environ.get("COMMONPLACE_DB_PATH", commonplace_db.DB_PATH)
    conn = commonplace_db.connect(db_path)
    try:
        commonplace_db.migrate(conn)
        return jobs.status(conn, job_id)
    finally:
        conn.close()


mcp.tool(get_job_status)


def cancel_job(job_id: int) -> dict[str, Any]:
    """Attempt to cancel a queued or running job.

    Parameters
    ----------
    job_id:
        The integer primary key of the job_queue row.

    Returns
    -------
    ``{"id": job_id, "cancelled": <bool>, "previous_status": <str>}``
    ``cancelled`` is ``True`` only when the status was actually changed.

    Raises
    ------
    ValueError
        If no job with the given id exists.
    """
    db_path = os.environ.get("COMMONPLACE_DB_PATH", commonplace_db.DB_PATH)
    conn = commonplace_db.connect(db_path)
    try:
        commonplace_db.migrate(conn)
        return jobs.cancel(conn, job_id)
    finally:
        conn.close()


mcp.tool(cancel_job)


# ---------------------------------------------------------------------------
# Semantic search MCP tool
# ---------------------------------------------------------------------------


def search_commonplace(
    query: str,
    content_type: str | None = None,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    calendar_year: int | None = None,
    category: str | None = None,
    genre: str | None = None,
    tradition: str | None = None,
    feast_name: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search across books, highlights, captures, Bluesky posts, and liturgical units.

    Use when the user asks to find, recall, or look up past content by topic or keyword.

    Parameters
    ----------
    query:
        Natural-language search query (required, non-empty).
    content_type:
        Filter to a single content type (e.g. ``"book"``, ``"capture"``,
        ``"bluesky"``, ``"kindle"``, ``"article"``, ``"youtube"``,
        ``"podcast"``, ``"image"``, ``"video"``, ``"liturgical_unit"``).
    source:
        Free-text substring match against the document's source URI.
    date_from:
        ISO 8601 date (``YYYY-MM-DD``).  For non-liturgical content: lower
        bound on ``documents.created_at``.  For ``content_type="liturgical_unit"``:
        lower bound on the feast's resolved calendar date (see note below).
    date_to:
        ISO 8601 date (``YYYY-MM-DD``).  Same dual semantics as *date_from*.

        **Calendar-range overload (Option A):** when ``content_type`` is set to
        ``"liturgical_unit"``, ``date_from``/``date_to`` are reinterpreted as
        liturgical calendar bounds — each feast's ``date_rule`` is resolved for
        *calendar_year* and only units whose feast falls within the range are
        returned.  For all other content types the original ``created_at``
        semantics apply unchanged.
    calendar_year:
        Year used when resolving movable feasts under the liturgical calendar
        overload (default: current year).  Ignored for non-liturgical queries.
    category:
        Equality filter on ``liturgical_unit_meta.category``
        (e.g. ``"liturgical_proper"``, ``"devotional_manual"``, ``"psalter"``,
        ``"hagiography"``).  Only matches ``liturgical_unit`` documents.
    genre:
        Equality filter on ``liturgical_unit_meta.genre``
        (e.g. ``"collect"``, ``"canticle"``, ``"prayer"``, ``"psalm_verse"``).
        Only matches ``liturgical_unit`` documents.
    tradition:
        Equality filter on ``liturgical_unit_meta.tradition``
        (``"anglican"``, ``"byzantine"``, ``"roman"``, ``"shared"``).
        Only matches ``liturgical_unit`` documents.
    feast_name:
        Case-insensitive substring match against ``feast.primary_name`` via
        ``liturgical_unit_meta.calendar_anchor_id → feast.id``.  Only matches
        ``liturgical_unit`` documents that have a feast calendar anchor.
    limit:
        Maximum results to return (default 10, max 50).

    Returns
    -------
    ``{"results": [...], "count": <int>}`` where each result contains
    ``score``, ``document_id``, ``content_type``, ``source_id``,
    ``source_uri``, ``title``, ``chunk_text``, and ``created_at``.
    """
    if not query or not query.strip():
        return {"results": [], "count": 0, "error": "query must be non-empty"}

    from commonplace_server.embedding import embed, pack_vector

    db_path = os.environ.get("COMMONPLACE_DB_PATH", commonplace_db.DB_PATH)
    conn = commonplace_db.connect(db_path)
    try:
        commonplace_db.migrate(conn)

        # Embed the query
        try:
            vectors = embed([query.strip()])
        except Exception as exc:
            logger.error("Embedding failed for search query: %s", exc)
            return {"results": [], "count": 0, "error": f"embedding failed: {exc}"}

        query_blob = pack_vector(vectors[0])

        results = search_commonplace_impl(
            conn,
            query_blob,
            content_type=content_type,
            source=source,
            date_from=date_from,
            date_to=date_to,
            calendar_year=calendar_year,
            category=category,
            genre=genre,
            tradition=tradition,
            feast_name=feast_name,
            limit=limit,
        )

        result_dicts = results_to_dicts(results)
        return {"results": result_dicts, "count": len(result_dicts)}
    finally:
        conn.close()


mcp.tool(search_commonplace)


# ---------------------------------------------------------------------------
# Correction MCP tool
# ---------------------------------------------------------------------------


def correct(
    target_type: str,
    correction: str,
    target_id: str | None = None,
) -> dict[str, Any]:
    """Apply an on-the-fly correction to a profile, book, or the serendipity judge.

    Use when the user says something like "prefer blunt register" (profile),
    "this book is really a memoir, not an argument" (book), or
    "stop surfacing politics during work hours" (judge_serendipity).

    Parameters
    ----------
    target_type:
        ``"profile"``, ``"book"``, or ``"judge_serendipity"``.
    correction:
        Free-text correction to record.
    target_id:
        For ``"book"``: the book slug (required).
        For ``"profile"`` and ``"judge_serendipity"``: ignored / pass ``None``.

    Returns
    -------
    On success: ``{"status": "applied", "target_type": ..., ...}``
    On error:   ``{"status": "error", "error": ..., ...}``
    """
    if target_type == "profile":
        return correct_profile(correction)
    elif target_type == "book":
        if not target_id:
            return {
                "status": "error",
                "error": "target_id (book slug) is required for target_type='book'",
            }
        return correct_book(target_id, correction)
    elif target_type == "judge_serendipity":
        return correct_judge(correction)
    else:
        return {
            "status": "error",
            "error": (
                f"unknown target_type {target_type!r}; "
                "expected 'profile', 'book', or 'judge_serendipity'"
            ),
        }


mcp.tool(correct)


# ---------------------------------------------------------------------------
# Serendipity surface MCP tool
# ---------------------------------------------------------------------------


def surface(
    seed: str,
    mode: str = "ambient",
    types: list[str] | None = None,
    limit: int = 10,
    similarity_floor: float = 0.55,
    recency_bias: bool = True,
) -> dict[str, Any]:
    """Surface passages from the corpus that bear on the current conversation topic.

    Invoke when the user's current message is substantive (~20+ words on a topic
    with intellectual traction). Ambient mode returns silently when nothing
    genuinely fits; on_demand mode is more permissive.

    Parameters
    ----------
    seed:
        Current conversation topic or excerpt (1-3 sentences).
    mode:
        ``"ambient"`` (unsolicited, stingy) or ``"on_demand"`` (user asked, permissive).
    types:
        Optional list of content types to search (e.g. ``["book", "highlight"]``).
        If omitted, searches all types.
    limit:
        Max candidates pulled before judge filtering (default 10).
    similarity_floor:
        Candidates below this similarity score are dropped pre-judge (default 0.55).
    recency_bias:
        If True, pass ``last_engaged_days_ago`` to the judge as a ranking signal.
    """
    return run_surface(
        seed=seed,
        mode=mode,
        types=types,
        limit=limit,
        similarity_floor=similarity_floor,
        recency_bias=recency_bias,
    )


mcp.tool(surface)


# ---------------------------------------------------------------------------
# Embedding pipeline progress MCP tool
# ---------------------------------------------------------------------------


def embedding_progress(
    content_type: str | None = None,
    recent_limit: int = 5,
) -> dict[str, Any]:
    """Report embedding-pipeline progress across the corpus.

    Use when the user asks about ingestion / embedding status — for
    example "how far into the drain are we", "what's embedding right
    now", or "what was the last thing embedded".

    Parameters
    ----------
    content_type:
        Restrict counts and throughput to a single content type (e.g.
        ``"book"``, ``"article"``, ``"capture"``). Omit for the full corpus.
    recent_limit:
        Number of recently-finished ingest jobs to include
        (default 5, clamped to [0, 20]).

    Returns
    -------
    Dict with keys:

    - ``total``: documents counted (after any ``content_type`` filter).
    - ``by_status``: counts keyed by document status (``pending`` /
      ``embedded`` / ``failed`` / …).
    - ``by_content_type``: nested counts ``{content_type: {status: n}}``.
    - ``oldest_pending``: oldest still-pending document, or ``None`` when
      nothing is pending. Contains ``id``, ``title``, ``content_type``,
      ``created_at``, ``age_minutes``.
    - ``in_flight``: list of ``ingest_*`` jobs currently ``running``,
      each with ``job_id``, ``kind``, ``summary`` (best-effort label from
      payload), ``started_at``, ``running_for_seconds``.
    - ``recently_completed``: up to ``recent_limit`` most recent
      finished ingest jobs (``complete`` / ``failed`` / ``cancelled``).
    - ``recent_throughput``: counts over the last hour —
      ``ingest_jobs_completed`` and ``documents_embedded``.
    """
    db_path = os.environ.get("COMMONPLACE_DB_PATH", commonplace_db.DB_PATH)
    conn = commonplace_db.connect(db_path)
    try:
        commonplace_db.migrate(conn)
        return progress.report(
            conn,
            content_type=content_type,
            recent_limit=recent_limit,
        )
    finally:
        conn.close()


mcp.tool(embedding_progress)


# ---------------------------------------------------------------------------
# Subject frequency MCP tool
# ---------------------------------------------------------------------------


def subject_frequency(
    include_controlled: bool = True,
    include_other: bool = True,
    min_count: int = 1,
) -> dict[str, Any]:
    """Report theological subject frequency across the feast table.

    Use when asked to audit liturgical subject tags, identify promotion
    candidates from ``_other:`` prefixed tags, or survey how theological
    themes are distributed across feasts.

    Parameters
    ----------
    include_controlled:
        Include subjects without the ``_other:`` prefix (default True).
    include_other:
        Include subjects with the ``_other:`` prefix (default True).
    min_count:
        Exclude subjects that appear on fewer than this many feasts
        (default 1 — return everything).

    Returns
    -------
    ``{"controlled": [...], "other": [...]}`` where each item is
    ``{"subject": str, "count": int, "feasts": [str, ...]}``.
    Both lists are sorted by count descending, ties broken by subject name
    ascending. Feast names within each item are sorted alphabetically.
    """
    db_path = os.environ.get("COMMONPLACE_DB_PATH", commonplace_db.DB_PATH)
    conn = commonplace_db.connect(db_path)
    try:
        commonplace_db.migrate(conn)
        return _subject_frequency_report(
            conn,
            include_controlled=include_controlled,
            include_other=include_other,
            min_count=min_count,
        )
    finally:
        conn.close()


mcp.tool(subject_frequency)


# ---------------------------------------------------------------------------
# HTTP route
# ---------------------------------------------------------------------------


@mcp.custom_route("/healthcheck", methods=["GET"])
async def http_healthcheck(request: Request) -> Response:
    """HTTP GET /healthcheck — returns the same payload as the MCP tool."""
    db_path = os.environ.get(
        "COMMONPLACE_DB_PATH",
        commonplace_db.DB_PATH,
    )
    conn = commonplace_db.connect(db_path)
    try:
        payload = _build_health_payload(conn)
    finally:
        conn.close()
    return JSONResponse(payload)


@mcp.custom_route("/capture", methods=["POST"])
async def http_capture(request: Request) -> Response:
    """HTTP POST /capture — bearer-authed capture intake endpoint.

    Writes payload to the vault inbox and enqueues a worker job.
    Reachable at https://plex-server.tailb9faa9.ts.net:8443/capture via
    Tailscale serve (ADR-0004).
    """
    authorization: str | None = request.headers.get("Authorization")

    # Parse JSON body; return 400 on parse failure
    try:
        body: dict[str, Any] = await request.json()
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
    except Exception:
        return JSONResponse({"error": "request body must be valid JSON object"}, status_code=400)

    db_path = os.environ.get("COMMONPLACE_DB_PATH", commonplace_db.DB_PATH)
    inbox_dir_raw = os.environ.get("COMMONPLACE_INBOX_DIR", "~/commonplace-vault/inbox/")
    inbox_dir = Path(inbox_dir_raw).expanduser()

    conn = commonplace_db.connect(db_path)
    try:
        commonplace_db.migrate(conn)
        status_code, payload = handle_capture(
            body,
            authorization,
            conn=conn,
            inbox_dir=inbox_dir,
            expected_bearer=_CAPTURE_BEARER,
        )
    finally:
        conn.close()

    return JSONResponse(payload, status_code=status_code)


# ---------------------------------------------------------------------------
# Server startup (called by __main__)
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the Commonplace MCP server.

    Reads the MCP URL-path token from keychain (or COMMONPLACE_MCP_TOKEN env var)
    and mounts FastMCP at /mcp/<token>.  Exits non-zero if the token is missing
    (run ``make mcp-token-init`` to seed it) or if DB migrations fail.
    """
    host = os.environ.get("COMMONPLACE_HOST", "127.0.0.1")
    port = int(os.environ.get("COMMONPLACE_PORT", "8765"))
    db_path = os.environ.get("COMMONPLACE_DB_PATH", commonplace_db.DB_PATH)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
    )

    # Resolve the URL-path secret token.  Server refuses to start without it.
    token = resolve_mcp_token()
    if not token:
        logger.error(
            "MCP URL-path token not found in keychain (service=commonplace-mcp-token, "
            "account=mcp) and COMMONPLACE_MCP_TOKEN env var is not set. "
            "Run `make mcp-token-init` to generate and store the token, then restart."
        )
        sys.exit(1)

    mcp_path = f"/mcp/{token}"

    # Run migrations on startup so a fresh install initialises itself.
    try:
        conn = commonplace_db.connect(db_path)
        schema_ver = commonplace_db.migrate(conn)
        conn.close()
        logger.info(
            "commonplace startup: db=%s schema_version=%d", db_path, schema_ver
        )
    except Exception as exc:
        logger.error("DB migration failed — cannot start: %s", exc, exc_info=True)
        sys.exit(1)

    logger.info(
        "Starting Commonplace MCP server on http://%s:%d%s  (version %s)",
        host,
        port,
        mcp_path,
        _get_version(),
    )
    logger.info(
        "MCP endpoint: http://%s:%d%s/",
        host,
        port,
        mcp_path,
    )

    mcp.run(
        transport="http",
        host=host,
        port=port,
        path=mcp_path,
        middleware=[Middleware(AcceptHeaderMiddleware)],
    )
