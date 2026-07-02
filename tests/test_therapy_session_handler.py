from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from commonplace_db import connect, migrate
from commonplace_worker.handlers.therapy_session import (
    chunk_therapy_markdown,
    handle_therapy_session_ingest,
    parse_session_title,
)
from commonplace_worker.notion import blocks_to_markdown

_DIM = 768


def _rt(text: str, **annotations: bool) -> dict[str, Any]:
    base = {
        "bold": False,
        "italic": False,
        "strikethrough": False,
        "underline": False,
        "code": False,
        "color": "default",
    }
    base.update(annotations)
    return {"plain_text": text, "annotations": base}


def _fake_embedder(texts: list[str], model: str) -> list[list[float]]:
    return [[float(i)] * _DIM for i, _ in enumerate(texts)]


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


class FakeNotionClient:
    def __init__(self, *, page: dict[str, Any], blocks: list[dict[str, Any]]) -> None:
        self.page = page
        self.blocks = blocks

    def get_page(self, page_id: str) -> dict[str, Any]:
        assert page_id == self.page["id"]
        return self.page

    def fetch_block_tree(self, page_id: str) -> list[dict[str, Any]]:
        assert page_id == self.page["id"]
        return self.blocks


def _page(last_edited: str = "2026-05-18T20:00:00.000Z") -> dict[str, Any]:
    return {
        "id": "page-1",
        "url": "https://notion.so/page-1",
        "last_edited_time": last_edited,
        "properties": {
            "Name": {"type": "title", "title": [_rt("May 18, 2026 — couples")]},
            "Therapist": {"type": "rich_text", "rich_text": [_rt("Christina")]},
        },
    }


def _session_blocks(response: str = "I noticed the pattern clearly.") -> list[dict[str, Any]]:
    return [
        {
            "type": "heading_2",
            "heading_2": {"rich_text": [_rt("Summary")]},
        },
        {
            "type": "paragraph",
            "paragraph": {"rich_text": [_rt("We talked about trust and repair.")]},
        },
        {
            "type": "heading_3",
            "heading_3": {"rich_text": [_rt("1. The first turn")]},
        },
        {
            "type": "quote",
            "quote": {"rich_text": [_rt("What did you feel there?", italic=True)]},
        },
        {
            "type": "quote",
            "quote": {"rich_text": [_rt(response)]},
        },
        {
            "type": "heading_3",
            "heading_3": {"rich_text": [_rt("2. The second turn")]},
        },
        {
            "type": "quote",
            "quote": {"rich_text": [_rt("What changed afterward?", italic=True)]},
        },
        {
            "type": "quote",
            "quote": {"rich_text": [_rt("It gave me room to choose.")]},
        },
    ]


def test_notion_blocks_to_markdown_renders_expected_shapes() -> None:
    blocks = [
        {"type": "heading_3", "heading_3": {"rich_text": [_rt("1. A heading")]}},
        {
            "type": "paragraph",
            "paragraph": {"rich_text": [_rt("Bold", bold=True), _rt(" and "), _rt("soft", italic=True)]},
        },
        {"type": "quote", "quote": {"rich_text": [_rt("A quoted preamble", italic=True)]}},
        {
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [_rt("Parent item")]},
            "children": [
                {
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": [_rt("Nested item")]},
                }
            ],
        },
        {"type": "divider", "divider": {}},
    ]

    assert blocks_to_markdown(blocks) == (
        "### 1. A heading\n\n"
        "**Bold** and *soft*\n\n"
        "> *A quoted preamble*\n\n"
        "- Parent item\n"
        "  - Nested item\n\n"
        "---\n"
    )


@pytest.mark.parametrize(
    ("title", "date_value", "session_type"),
    [
        ("May 18, 2026", "2026-05-18", "individual"),
        ("May 18, 2026 — couples", "2026-05-18", "couples"),
        ("May 18, 2026 - Couples session", "2026-05-18", "couples"),
    ],
)
def test_parse_session_title(title: str, date_value: str, session_type: str) -> None:
    parsed = parse_session_title(title)
    assert parsed.session_date.isoformat() == date_value
    assert parsed.session_type == session_type


@pytest.mark.parametrize("title", ["2026-05-18", "May nope, 2026", "Therapy May 18"])
def test_parse_session_title_rejects_malformed_titles(title: str) -> None:
    with pytest.raises(ValueError):
        parse_session_title(title)


def test_chunk_therapy_markdown_respects_highlight_boundaries() -> None:
    markdown = blocks_to_markdown(_session_blocks())
    chunks = chunk_therapy_markdown(markdown)
    assert len(chunks) == 3
    assert chunks[0].text.startswith("## Summary")
    assert chunks[1].text.startswith("### 1. The first turn")
    assert "> *What did you feel there?*" in chunks[1].text
    assert chunks[2].text.startswith("### 2. The second turn")


def test_full_job_inserts_document_meta_chunks_and_replaces_on_rerun(
    db: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMMONPLACE_VAULT_DIR", str(tmp_path))
    client = FakeNotionClient(page=_page(), blocks=_session_blocks())

    result = handle_therapy_session_ingest(
        {"notion_page_id": "page-1"},
        db,
        _client=client,
        _embedder=_fake_embedder,
    )
    assert result["chunk_count"] == 3

    docs = db.execute(
        "SELECT id, content_type, source_id, title, raw_path, status FROM documents"
    ).fetchall()
    assert len(docs) == 1
    doc_id = docs[0]["id"]
    assert docs[0]["content_type"] == "therapy_session"
    assert docs[0]["source_id"] == "page-1"
    assert docs[0]["status"] == "embedded"
    assert Path(docs[0]["raw_path"]).exists()

    meta = db.execute(
        "SELECT session_date, therapist, session_type, notion_last_edited_at "
        "FROM therapy_session_meta WHERE document_id = ?",
        (doc_id,),
    ).fetchone()
    assert meta["session_date"] == "2026-05-18"
    assert meta["therapist"] == "Christina"
    assert meta["session_type"] == "couples"

    chunks_before = db.execute(
        "SELECT id, text FROM chunks WHERE document_id = ? ORDER BY chunk_index",
        (doc_id,),
    ).fetchall()
    assert len(chunks_before) == 3
    assert "I noticed the pattern clearly." in chunks_before[1]["text"]

    updated_client = FakeNotionClient(
        page=_page("2026-05-19T20:00:00.000Z"),
        blocks=_session_blocks("I named it differently after editing."),
    )
    second = handle_therapy_session_ingest(
        {"notion_page_id": "page-1"},
        db,
        _client=updated_client,
        _embedder=_fake_embedder,
    )
    assert second["document_id"] == doc_id
    assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM therapy_session_meta").fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
        (doc_id,),
    ).fetchone()[0] == 3
    assert db.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0] == 3
    replacement_text = "\n".join(
        row["text"]
        for row in db.execute(
            "SELECT text FROM chunks WHERE document_id = ? ORDER BY chunk_index",
            (doc_id,),
        ).fetchall()
    )
    assert "I named it differently after editing." in replacement_text
    assert "I noticed the pattern clearly." not in replacement_text
