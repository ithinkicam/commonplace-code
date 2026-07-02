"""Therapy session Notion ingest handler.

One Notion child page under the Therapy parent becomes one
``documents(content_type='therapy_session')`` row. The Notion page is the
canonical source; raw Google Drive transcripts are deliberately not read.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from commonplace_server.chunking import Chunk
from commonplace_worker.checkpoints import for_payload
from commonplace_worker.frontmatter import render_embed_header, slugify, yaml_escape
from commonplace_worker.notion import (
    NotionClient,
    blocks_to_markdown,
    extract_property_text,
    page_summary,
)
from commonplace_worker.vault_io import atomic_write_text, vault_root

logger = logging.getLogger(__name__)

_TITLE_RE = re.compile(
    r"^\s*(?P<date>[A-Za-z]+ \d{1,2}, \d{4})(?:\s*(?:--|-|—|–)\s*(?P<suffix>.+))?\s*$"
)
_HIGHLIGHT_RE = re.compile(r"(?m)^###\s+\d+\.\s+.+$")


@dataclass(frozen=True)
class SessionTitle:
    title: str
    session_date: date
    suffix: str | None
    session_type: str


@dataclass(frozen=True)
class SessionFields:
    therapist: str
    session_type: str


def parse_session_title(title: str) -> SessionTitle:
    """Parse a Notion therapy session title like ``May 18, 2026 — couples``."""
    match = _TITLE_RE.match(title)
    if not match:
        raise ValueError(
            "therapy session title must look like 'May 18, 2026' or "
            "'May 18, 2026 — couples'"
        )
    try:
        session_date = datetime.strptime(match.group("date"), "%B %d, %Y").date()
    except ValueError as exc:
        raise ValueError(f"therapy session title has invalid date: {title!r}") from exc

    suffix = match.group("suffix")
    session_type = "couples" if suffix and "couples" in suffix.lower() else "individual"
    return SessionTitle(
        title=title.strip(),
        session_date=session_date,
        suffix=suffix.strip() if suffix else None,
        session_type=session_type,
    )


def parse_session_fields(page: dict[str, Any], title_info: SessionTitle) -> SessionFields:
    """Extract therapist/session_type from Notion properties with safe defaults."""
    therapist = extract_property_text(page, ("Therapist", "therapist")) or "Christina"
    prop_type = extract_property_text(
        page,
        ("Session Type", "session_type", "Type", "type"),
    )
    session_type = title_info.session_type
    if prop_type and prop_type.strip().lower() in {"individual", "couples"}:
        session_type = prop_type.strip().lower()
    return SessionFields(therapist=therapist, session_type=session_type)


def chunk_therapy_markdown(markdown: str) -> list[Chunk]:
    """Chunk therapy markdown as summary + one chunk per numbered highlight."""
    text = markdown.strip()
    if not text:
        return []

    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    matches = list(_HIGHLIGHT_RE.finditer(text))
    sections: list[str] = []
    if not matches:
        sections = [text]
    else:
        summary = text[: matches[0].start()].strip()
        if summary:
            sections.append(summary)
        for idx, match in enumerate(matches):
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            section = text[match.start():end].strip()
            if section:
                sections.append(section)
    return [Chunk(text=section, token_count=len(enc.encode(section))) for section in sections]


def handle_therapy_session_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _client: Any = None,
    _embedder: Any = None,
) -> dict[str, Any]:
    """Worker handler for ``ingest_therapy_session`` jobs.

    Payload: ``{"notion_page_id": "<uuid>"}``.
    """
    t0 = time.monotonic()
    page_id = payload.get("notion_page_id")
    if not isinstance(page_id, str) or not page_id.strip():
        raise ValueError(f"ingest_therapy_session payload missing notion_page_id: {payload!r}")
    page_id = page_id.strip()

    ckpt = for_payload(conn, payload, int(payload.get("_attempt", 0) or 0))
    client = _client if _client is not None else NotionClient()

    metadata = ckpt.get_output("fetch_metadata")
    if metadata is None:
        ckpt.start("fetch_metadata")
        page = client.get_page(page_id)
        summary = page_summary(page)
        if not summary.title:
            raise ValueError(f"Notion page {page_id} has no title property")
        metadata = {
            "page": page,
            "summary": asdict(summary),
        }
        ckpt.complete("fetch_metadata", metadata)
    page = metadata["page"]
    summary_data = metadata["summary"]

    content = ckpt.get_output("fetch_content")
    if content is None:
        ckpt.start("fetch_content")
        blocks = client.fetch_block_tree(page_id)
        markdown = blocks_to_markdown(blocks)
        content = {"markdown": markdown}
        ckpt.complete("fetch_content", content)
    markdown = str(content["markdown"])

    parsed = ckpt.get_output("parse_session_fields")
    if parsed is None:
        ckpt.start("parse_session_fields")
        title_info = parse_session_title(str(summary_data["title"]))
        fields = parse_session_fields(page, title_info)
        parsed = {
            "title": title_info.title,
            "session_date": title_info.session_date.isoformat(),
            "therapist": fields.therapist,
            "session_type": fields.session_type,
        }
        ckpt.complete("parse_session_fields", parsed)

    upserted = ckpt.get_output("upsert_document")
    if upserted is None:
        ckpt.start("upsert_document")
        vault_path = _write_vault_file(
            title=str(parsed["title"]),
            session_date=str(parsed["session_date"]),
            therapist=str(parsed["therapist"]),
            session_type=str(parsed["session_type"]),
            notion_page_id=page_id,
            notion_url=summary_data.get("url"),
            notion_last_edited_at=str(summary_data["last_edited_time"]),
            markdown=markdown,
        )
        document_id = _upsert_document(
            conn,
            notion_page_id=page_id,
            notion_url=summary_data.get("url"),
            notion_last_edited_at=str(summary_data["last_edited_time"]),
            title=str(parsed["title"]),
            session_date=str(parsed["session_date"]),
            therapist=str(parsed["therapist"]),
            session_type=str(parsed["session_type"]),
            markdown=markdown,
            raw_path=vault_path,
        )
        upserted = {"document_id": document_id, "vault_path": str(vault_path)}
        ckpt.complete("upsert_document", upserted)

    chunked = ckpt.get_output("chunk")
    if chunked is None:
        ckpt.start("chunk")
        chunks = chunk_therapy_markdown(markdown)
        chunked = {
            "chunks": [{"text": c.text, "token_count": c.token_count} for c in chunks],
        }
        ckpt.complete("chunk", {"chunk_count": len(chunks)})
    chunk_payload = chunked.get("chunks") if isinstance(chunked, dict) else None
    chunks = (
        [Chunk(text=str(c["text"]), token_count=int(c["token_count"])) for c in chunk_payload]
        if isinstance(chunk_payload, list)
        else chunk_therapy_markdown(markdown)
    )

    document_id = int(upserted["document_id"])
    embedded = ckpt.get_output("embed")
    if embedded is None:
        ckpt.start("embed")
        from commonplace_server.pipeline import embed_document

        header = render_embed_header(
            [
                ("Title", str(parsed["title"])),
                ("Content type", "therapy session"),
                ("Session date", str(parsed["session_date"])),
                ("Therapist", str(parsed["therapist"])),
                ("Session type", str(parsed["session_type"])),
                ("Notion URL", summary_data.get("url")),
            ]
        )

        embed_kwargs: dict[str, Any] = {"chunks_override": chunks}
        if _embedder is not None:
            embed_kwargs["_embedder"] = _embedder
        result = embed_document(document_id, header + markdown, conn, **embed_kwargs)
        embedded = {"chunk_count": result.chunk_count, "total_tokens": result.total_tokens}
        ckpt.complete("embed", embedded)

    if not ckpt.is_complete("index"):
        ckpt.start("index")
        ckpt.complete("index", {"document_id": document_id})

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "ingested therapy_session document_id=%d chunks=%d notion_page_id=%s elapsed_ms=%.0f",
        document_id,
        int(embedded["chunk_count"]),
        page_id,
        elapsed_ms,
    )
    return {
        "document_id": document_id,
        "chunk_count": int(embedded["chunk_count"]),
        "elapsed_ms": elapsed_ms,
        "notion_page_id": page_id,
        "title": parsed["title"],
    }


def _upsert_document(
    conn: sqlite3.Connection,
    *,
    notion_page_id: str,
    notion_url: str | None,
    notion_last_edited_at: str,
    title: str,
    session_date: str,
    therapist: str,
    session_type: str,
    markdown: str,
    raw_path: Path,
) -> int:
    content_hash = hashlib.sha256(
        f"{notion_page_id}|{notion_last_edited_at}|{markdown}".encode()
    ).hexdigest()
    source_uri = notion_url or f"notion://page/{notion_page_id}"
    existing = conn.execute(
        """
        SELECT d.id
          FROM documents d
          JOIN therapy_session_meta tsm ON tsm.document_id = d.id
         WHERE tsm.notion_page_id = ?
        """,
        (notion_page_id,),
    ).fetchone()

    with conn:
        if existing is None:
            cur = conn.execute(
                """
                INSERT INTO documents
                    (content_type, source_uri, source_id, title, author,
                     content_hash, raw_path, status)
                VALUES ('therapy_session', ?, ?, ?, ?, ?, ?, 'ingesting')
                """,
                (
                    source_uri,
                    notion_page_id,
                    title,
                    therapist,
                    content_hash,
                    str(raw_path),
                ),
            )
            if cur.lastrowid is None:
                raise sqlite3.DatabaseError("therapy_session document insert returned no id")
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
                (
                    source_uri,
                    notion_page_id,
                    title,
                    therapist,
                    content_hash,
                    str(raw_path),
                    document_id,
                ),
            )

        conn.execute(
            """
            INSERT INTO therapy_session_meta
                (document_id, session_date, therapist, session_type,
                 notion_page_id, notion_url, notion_last_edited_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                session_date = excluded.session_date,
                therapist = excluded.therapist,
                session_type = excluded.session_type,
                notion_page_id = excluded.notion_page_id,
                notion_url = excluded.notion_url,
                notion_last_edited_at = excluded.notion_last_edited_at
            """,
            (
                document_id,
                session_date,
                therapist,
                session_type,
                notion_page_id,
                notion_url,
                notion_last_edited_at,
            ),
        )
    return document_id


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


def _write_vault_file(
    *,
    title: str,
    session_date: str,
    therapist: str,
    session_type: str,
    notion_page_id: str,
    notion_url: str | None,
    notion_last_edited_at: str,
    markdown: str,
) -> Path:
    out_dir = vault_root() / "therapy" / session_date[:4]
    slug = slugify(title, fallback="therapy-session")
    final_path = out_dir / f"{session_date}-{slug}.md"
    lines = [
        "---",
        "source: therapy_session",
        f"title: {yaml_escape(title)}",
        f"session_date: {yaml_escape(session_date)}",
        f"therapist: {yaml_escape(therapist)}",
        f"session_type: {yaml_escape(session_type)}",
        f"notion_page_id: {yaml_escape(notion_page_id)}",
    ]
    if notion_url:
        lines.append(f"notion_url: {yaml_escape(notion_url)}")
    lines.append(f"notion_last_edited_at: {yaml_escape(notion_last_edited_at)}")
    lines.extend(["---", "", markdown.rstrip(), ""])
    return atomic_write_text(final_path, "\n".join(lines))
