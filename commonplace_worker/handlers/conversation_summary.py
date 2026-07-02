"""Curated AI conversation summary ingest handler.

These are not raw chat transcripts. They are intentionally compact summaries
of a conversation where the user's thinking moved: a question sharpened, a
framework shifted, a self-understanding became clearer, or a connection became
worth carrying forward.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from commonplace_worker.frontmatter import render_embed_header, slugify, yaml_escape
from commonplace_worker.vault_io import atomic_write_text, vault_root

logger = logging.getLogger(__name__)

_VALID_PLATFORMS = {"claude", "chatgpt", "other"}


def handle_conversation_summary_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _embedder: Any = None,
) -> dict[str, Any]:
    """Worker handler for ``ingest_conversation_summary`` jobs.

    Payload keys:
    - ``summary``: curated markdown summary, required
    - ``title``: optional title
    - ``platform``: ``claude`` / ``chatgpt`` / ``other``
    - ``conversation_date``: ISO date, defaults to today UTC
    - ``source_url``: optional conversation share URL
    - ``model``: optional model name
    - ``topics``: optional list of topic strings
    """
    t0 = time.monotonic()
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("ingest_conversation_summary payload requires non-empty 'summary'")
    summary = summary.strip()

    platform = _normalise_platform(payload.get("platform"))
    conversation_date = _normalise_date(payload.get("conversation_date"))
    source_url = _optional_str(payload.get("source_url"))
    model = _optional_str(payload.get("model"))
    topics = _normalise_topics(payload.get("topics"))
    title = _optional_str(payload.get("title")) or _derive_title(summary, conversation_date)

    captured_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    content_hash = _content_hash(
        summary=summary,
        platform=platform,
        conversation_date=conversation_date,
        source_url=source_url,
        model=model,
        topics=topics,
    )
    source_id = source_url or f"conversation-summary:{content_hash}"

    existing = conn.execute(
        "SELECT id, content_hash FROM documents "
        "WHERE content_type = 'conversation_summary' AND source_id = ?",
        (source_id,),
    ).fetchone()
    if existing is None and source_url is None:
        existing = conn.execute(
            "SELECT id, content_hash FROM documents "
            "WHERE content_type = 'conversation_summary' AND content_hash = ?",
            (content_hash,),
        ).fetchone()

    if existing is not None and existing["content_hash"] == content_hash:
        document_id = int(existing["id"])
        chunk_count = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
            (document_id,),
        ).fetchone()[0]
        return {
            "document_id": document_id,
            "chunk_count": int(chunk_count),
            "elapsed_ms": (time.monotonic() - t0) * 1000,
            "title": title,
            "status": "skipped",
        }

    raw_path = _write_vault_file(
        title=title,
        summary=summary,
        platform=platform,
        conversation_date=conversation_date,
        source_url=source_url,
        model=model,
        topics=topics,
        captured_at=captured_at,
    )

    with conn:
        if existing is None:
            cur = conn.execute(
                """
                INSERT INTO documents
                    (content_type, source_uri, source_id, title, author,
                     content_hash, raw_path, status)
                VALUES ('conversation_summary', ?, ?, ?, ?, ?, ?, 'ingesting')
                """,
                (source_url, source_id, title, platform, content_hash, str(raw_path)),
            )
            if cur.lastrowid is None:
                raise sqlite3.DatabaseError("conversation_summary insert returned no id")
            document_id = int(cur.lastrowid)
        else:
            document_id = int(existing["id"])
            _delete_document_chunks(conn, document_id)
            conn.execute(
                """
                UPDATE documents
                   SET source_uri = ?,
                       source_id = ?,
                       title = ?,
                       author = ?,
                       content_hash = ?,
                       raw_path = ?,
                       status = 'ingesting',
                       updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                 WHERE id = ?
                """,
                (source_url, source_id, title, platform, content_hash, str(raw_path), document_id),
            )

        conn.execute(
            """
            INSERT INTO conversation_summary_meta
                (document_id, conversation_date, platform, source_url,
                 model, topics, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                conversation_date = excluded.conversation_date,
                platform = excluded.platform,
                source_url = excluded.source_url,
                model = excluded.model,
                topics = excluded.topics,
                captured_at = excluded.captured_at
            """,
            (
                document_id,
                conversation_date,
                platform,
                source_url,
                model,
                json.dumps(topics, ensure_ascii=False),
                captured_at,
            ),
        )

    from commonplace_server.pipeline import embed_document

    header = render_embed_header(
        [
            ("Title", title),
            ("Content type", "conversation summary"),
            ("Platform", platform),
            ("Conversation date", conversation_date),
            ("Topics", ", ".join(topics) if topics else None),
            ("Model", model),
            ("Source URL", source_url),
        ]
    )
    embed_kwargs: dict[str, Any] = {}
    if _embedder is not None:
        embed_kwargs["_embedder"] = _embedder
    result = embed_document(document_id, header + summary, conn, **embed_kwargs)

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "ingested conversation_summary document_id=%d chunks=%d platform=%s elapsed_ms=%.0f",
        document_id,
        result.chunk_count,
        platform,
        elapsed_ms,
    )
    return {
        "document_id": document_id,
        "chunk_count": result.chunk_count,
        "elapsed_ms": elapsed_ms,
        "title": title,
        "status": "updated" if existing is not None else "inserted",
    }


def _normalise_platform(value: Any) -> str:
    if value is None:
        return "claude"
    if not isinstance(value, str):
        raise ValueError("platform must be a string")
    platform = value.strip().lower()
    if platform not in _VALID_PLATFORMS:
        raise ValueError("platform must be one of: claude, chatgpt, other")
    return platform


def _normalise_date(value: Any) -> str:
    if value is None or value == "":
        return date.today().isoformat()
    if not isinstance(value, str):
        raise ValueError("conversation_date must be an ISO date string")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise ValueError(f"conversation_date must be YYYY-MM-DD, got {value!r}") from exc


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional string fields must be strings when provided")
    stripped = value.strip()
    return stripped or None


def _normalise_topics(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("topics must be a list of strings")
    topics: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("topics must be a list of strings")
        topic = item.strip()
        if topic and topic not in topics:
            topics.append(topic)
    return topics


def _derive_title(summary: str, conversation_date: str) -> str:
    for line in summary.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.sub(r"^#+\s*", "", stripped)
        title = heading[:80].strip()
        if title:
            return title
    return f"Conversation summary {conversation_date}"


def _content_hash(
    *,
    summary: str,
    platform: str,
    conversation_date: str,
    source_url: str | None,
    model: str | None,
    topics: list[str],
) -> str:
    blob = json.dumps(
        {
            "summary": summary,
            "platform": platform,
            "conversation_date": conversation_date,
            "source_url": source_url,
            "model": model,
            "topics": topics,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def _write_vault_file(
    *,
    title: str,
    summary: str,
    platform: str,
    conversation_date: str,
    source_url: str | None,
    model: str | None,
    topics: list[str],
    captured_at: str,
) -> Path:
    year, month = conversation_date[:4], conversation_date[5:7]
    out_dir = vault_root() / "conversations" / year / month
    final_path = out_dir / f"{conversation_date}-{slugify(title, fallback='conversation')}.md"

    lines = [
        "---",
        "source: conversation_summary",
        f"title: {yaml_escape(title)}",
        f"conversation_date: {yaml_escape(conversation_date)}",
        f"platform: {yaml_escape(platform)}",
    ]
    if source_url:
        lines.append(f"source_url: {yaml_escape(source_url)}")
    if model:
        lines.append(f"model: {yaml_escape(model)}")
    if topics:
        lines.append("topics:")
        lines.extend(f"  - {yaml_escape(topic)}" for topic in topics)
    lines.append(f"captured_at: {yaml_escape(captured_at)}")
    lines.extend(["---", "", summary.rstrip(), ""])
    return atomic_write_text(final_path, "\n".join(lines))


def _delete_document_chunks(conn: sqlite3.Connection, document_id: int) -> None:
    rows = conn.execute(
        "SELECT id FROM chunks WHERE document_id = ?",
        (document_id,),
    ).fetchall()
    chunk_ids = [int(row["id"]) for row in rows]
    if chunk_ids:
        placeholders = ",".join("?" * len(chunk_ids))
        conn.execute(f"DELETE FROM chunk_vectors WHERE chunk_id IN ({placeholders})", chunk_ids)
    conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
