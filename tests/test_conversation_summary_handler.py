from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from commonplace_db import connect, migrate
from commonplace_worker.handlers.conversation_summary import (
    handle_conversation_summary_ingest,
)

_DIM = 768


def _fake_embedder(texts: list[str], model: str) -> list[list[float]]:
    return [[float(i)] * _DIM for i, _ in enumerate(texts)]


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


def test_conversation_summary_ingest_inserts_doc_meta_chunks_and_vault_file(
    db: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMMONPLACE_VAULT_DIR", str(tmp_path))
    result = handle_conversation_summary_ingest(
        {
            "summary": "# A clearer question\n\nI stopped trying to solve grief and started naming it.",
            "title": "Grief as something named",
            "platform": "chatgpt",
            "conversation_date": "2026-05-28",
            "source_url": "https://chatgpt.com/share/example",
            "model": "GPT-5",
            "topics": ["grief", "attention", "grief"],
        },
        db,
        _embedder=_fake_embedder,
    )

    assert result["status"] == "inserted"
    row = db.execute(
        "SELECT id, content_type, source_id, source_uri, title, author, raw_path, status "
        "FROM documents WHERE content_type='conversation_summary'"
    ).fetchone()
    assert row is not None
    assert row["source_id"] == "https://chatgpt.com/share/example"
    assert row["source_uri"] == "https://chatgpt.com/share/example"
    assert row["title"] == "Grief as something named"
    assert row["author"] == "chatgpt"
    assert row["status"] == "embedded"
    assert Path(row["raw_path"]).exists()

    meta = db.execute(
        "SELECT conversation_date, platform, source_url, model, topics "
        "FROM conversation_summary_meta WHERE document_id = ?",
        (row["id"],),
    ).fetchone()
    assert meta["conversation_date"] == "2026-05-28"
    assert meta["platform"] == "chatgpt"
    assert meta["model"] == "GPT-5"
    assert json.loads(meta["topics"]) == ["grief", "attention"]

    assert db.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
        (row["id"],),
    ).fetchone()[0] >= 1
    vault_text = Path(row["raw_path"]).read_text()
    assert "source: conversation_summary" in vault_text
    assert "I stopped trying to solve grief" in vault_text


def test_conversation_summary_idempotent_without_source_url(
    db: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMMONPLACE_VAULT_DIR", str(tmp_path))
    payload = {
        "summary": "The central shift was from winning the argument to noticing the desire.",
        "platform": "claude",
        "conversation_date": "2026-05-28",
        "topics": ["desire"],
    }
    first = handle_conversation_summary_ingest(payload, db, _embedder=_fake_embedder)
    second = handle_conversation_summary_ingest(payload, db, _embedder=_fake_embedder)

    assert first["document_id"] == second["document_id"]
    assert second["status"] == "skipped"
    assert db.execute(
        "SELECT COUNT(*) FROM documents WHERE content_type='conversation_summary'"
    ).fetchone()[0] == 1


def test_conversation_summary_updates_existing_source_url(
    db: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMMONPLACE_VAULT_DIR", str(tmp_path))
    base = {
        "summary": "Initial summary.",
        "platform": "claude",
        "conversation_date": "2026-05-28",
        "source_url": "https://claude.ai/share/example",
    }
    first = handle_conversation_summary_ingest(base, db, _embedder=_fake_embedder)
    updated = {
        **base,
        "summary": "Updated summary with the actual shift.",
        "topics": ["theology"],
    }
    second = handle_conversation_summary_ingest(updated, db, _embedder=_fake_embedder)

    assert second["document_id"] == first["document_id"]
    assert second["status"] == "updated"
    assert db.execute(
        "SELECT COUNT(*) FROM documents WHERE content_type='conversation_summary'"
    ).fetchone()[0] == 1
    chunk_text = "\n".join(
        r["text"]
        for r in db.execute(
            "SELECT text FROM chunks WHERE document_id = ?",
            (first["document_id"],),
        ).fetchall()
    )
    assert "Updated summary with the actual shift." in chunk_text
    assert "Initial summary." not in chunk_text


def test_conversation_summary_validates_payload(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="summary"):
        handle_conversation_summary_ingest({"summary": ""}, db, _embedder=_fake_embedder)
    with pytest.raises(ValueError, match="platform"):
        handle_conversation_summary_ingest(
            {"summary": "x", "platform": "slack"},
            db,
            _embedder=_fake_embedder,
        )
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        handle_conversation_summary_ingest(
            {"summary": "x", "conversation_date": "May 28"},
            db,
            _embedder=_fake_embedder,
        )
