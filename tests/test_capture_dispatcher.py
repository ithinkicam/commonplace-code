"""Tests for the capture dispatcher in commonplace_worker.worker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from commonplace_worker.worker import (
    HANDLERS,
    _capture_handler,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def inbox_dir(tmp_path: Path) -> Path:
    """Create and return a temporary inbox directory."""
    d = tmp_path / "inbox"
    d.mkdir()
    return d


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    """Create and return a temporary vault/captured directory."""
    d = tmp_path / "captured"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _env_dirs(inbox_dir: Path, vault_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point COMMONPLACE_INBOX_DIR and COMMONPLACE_VAULT_DIR at tmp dirs."""
    monkeypatch.setenv("COMMONPLACE_INBOX_DIR", str(inbox_dir))
    monkeypatch.setenv("COMMONPLACE_VAULT_DIR", str(vault_dir))


def _write_inbox(inbox_dir: Path, record: dict[str, Any], filename: str = "test.json") -> str:
    """Write a capture record to the inbox and return the filename."""
    (inbox_dir / filename).write_text(json.dumps(record), encoding="utf-8")
    return filename


# ---------------------------------------------------------------------------
# Dispatch to article handler
# ---------------------------------------------------------------------------


def test_dispatch_article(inbox_dir: Path) -> None:
    filename = _write_inbox(inbox_dir, {"kind": "article", "content": "https://example.com/post", "source": "shortcut"})
    mock_handler = MagicMock()
    with patch.dict(HANDLERS, {"ingest_article": mock_handler}):
        _capture_handler({"inbox_file": filename})
    mock_handler.assert_called_once()
    call_payload = mock_handler.call_args[0][0]
    assert call_payload["url"] == "https://example.com/post"
    assert call_payload["inbox_file"] == filename
    # File should be moved to processed/
    assert (inbox_dir / "processed" / filename).exists()
    assert not (inbox_dir / filename).exists()


# ---------------------------------------------------------------------------
# Dispatch to youtube handler
# ---------------------------------------------------------------------------


def test_dispatch_youtube(inbox_dir: Path) -> None:
    filename = _write_inbox(inbox_dir, {"kind": "youtube", "content": "https://youtube.com/watch?v=abc", "source": "shortcut"})
    mock_handler = MagicMock()
    with patch.dict(HANDLERS, {"ingest_youtube": mock_handler}):
        _capture_handler({"inbox_file": filename})
    mock_handler.assert_called_once()
    call_payload = mock_handler.call_args[0][0]
    assert call_payload["url"] == "https://youtube.com/watch?v=abc"
    assert (inbox_dir / "processed" / filename).exists()


# ---------------------------------------------------------------------------
# Dispatch to podcast handler
# ---------------------------------------------------------------------------


def test_dispatch_podcast(inbox_dir: Path) -> None:
    filename = _write_inbox(inbox_dir, {"kind": "podcast", "content": "https://feeds.example.com/rss", "source": "shortcut"})
    mock_handler = MagicMock()
    with patch.dict(HANDLERS, {"ingest_podcast": mock_handler}):
        _capture_handler({"inbox_file": filename})
    mock_handler.assert_called_once()
    call_payload = mock_handler.call_args[0][0]
    assert call_payload["url"] == "https://feeds.example.com/rss"


# ---------------------------------------------------------------------------
# Dispatch to bluesky_url handler
# ---------------------------------------------------------------------------


def test_dispatch_bluesky_url(inbox_dir: Path) -> None:
    filename = _write_inbox(inbox_dir, {"kind": "bluesky_url", "content": "https://bsky.app/profile/x/post/y", "source": "shortcut"})
    mock_handler = MagicMock()
    with patch.dict(HANDLERS, {"bluesky_url": mock_handler}):
        _capture_handler({"inbox_file": filename})
    mock_handler.assert_called_once()
    call_payload = mock_handler.call_args[0][0]
    assert call_payload["url"] == "https://bsky.app/profile/x/post/y"


# ---------------------------------------------------------------------------
# Dispatch to image handler
# ---------------------------------------------------------------------------


def test_dispatch_image(inbox_dir: Path) -> None:
    filename = _write_inbox(inbox_dir, {"kind": "image", "content": "/tmp/photo.jpg", "source": "shortcut"})
    mock_handler = MagicMock()
    with patch.dict(HANDLERS, {"ingest_image": mock_handler}):
        _capture_handler({"inbox_file": filename})
    mock_handler.assert_called_once()
    call_payload = mock_handler.call_args[0][0]
    assert call_payload["url"] == "/tmp/photo.jpg"
    assert call_payload["inbox_file"] == filename


# ---------------------------------------------------------------------------
# Dispatch to video handler
# ---------------------------------------------------------------------------


def test_dispatch_video(inbox_dir: Path) -> None:
    filename = _write_inbox(inbox_dir, {"kind": "video", "content": "/tmp/clip.mp4", "source": "shortcut"})
    mock_handler = MagicMock()
    with patch.dict(HANDLERS, {"ingest_video": mock_handler}):
        _capture_handler({"inbox_file": filename})
    mock_handler.assert_called_once()
    call_payload = mock_handler.call_args[0][0]
    assert call_payload["path"] == "/tmp/clip.mp4"


# ---------------------------------------------------------------------------
# Plain text capture — embedded directly
# ---------------------------------------------------------------------------


def test_dispatch_text(inbox_dir: Path) -> None:
    filename = _write_inbox(inbox_dir, {"kind": "text", "content": "Some interesting thought.", "source": "shortcut"})
    mock_connect = MagicMock()
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn
    mock_migrate = MagicMock()
    mock_embed = MagicMock()

    # Simulate INSERT returning a lastrowid
    mock_cursor = MagicMock()
    mock_cursor.lastrowid = 42
    mock_conn.execute.return_value = mock_cursor

    with (
        patch("commonplace_worker.worker.connect", mock_connect, create=True),
        patch("commonplace_db.db.connect", mock_connect),
        patch("commonplace_db.db.migrate", mock_migrate),
        patch("commonplace_server.pipeline.embed_document", mock_embed),
    ):
        _capture_handler({"inbox_file": filename})

    mock_embed.assert_called_once_with(42, "Some interesting thought.", mock_conn)
    assert (inbox_dir / "processed" / filename).exists()


# ---------------------------------------------------------------------------
# Note capture — moved to vault
# ---------------------------------------------------------------------------


def test_dispatch_note(inbox_dir: Path, vault_dir: Path) -> None:
    filename = _write_inbox(inbox_dir, {"kind": "note", "content": "My note content", "source": "save_note"})
    _capture_handler({"inbox_file": filename})
    assert (vault_dir / filename).exists()
    assert not (inbox_dir / filename).exists()


# ---------------------------------------------------------------------------
# Unknown kind — fallback to vault, no crash
# ---------------------------------------------------------------------------


def test_dispatch_unknown_kind(inbox_dir: Path, vault_dir: Path) -> None:
    filename = _write_inbox(inbox_dir, {"kind": "alien_format", "content": "???", "source": "test"})
    # Should not raise
    _capture_handler({"inbox_file": filename})
    assert (vault_dir / filename).exists()
    assert not (inbox_dir / filename).exists()


# ---------------------------------------------------------------------------
# Inbox file not found — error surfaced
# ---------------------------------------------------------------------------


def test_inbox_file_not_found() -> None:
    with pytest.raises(FileNotFoundError, match="inbox file not found"):
        _capture_handler({"inbox_file": "nonexistent.json"})


# ---------------------------------------------------------------------------
# Missing inbox_file in payload — ValueError
# ---------------------------------------------------------------------------


def test_missing_inbox_file_field() -> None:
    with pytest.raises(ValueError, match="capture payload missing inbox_file"):
        _capture_handler({})


# ---------------------------------------------------------------------------
# HANDLERS dict has all expected keys
# ---------------------------------------------------------------------------


def test_handlers_registry_keys() -> None:
    expected = {
        "noop",
        "capture",
        "ingest_library",
        "ingest_bluesky",
        "ingest_kindle",
        "ingest_article",
        "ingest_youtube",
        "ingest_podcast",
        "ingest_image",
        "ingest_video",
        "bluesky_url",
        "ingest_audiobook",
        "regenerate_profile",
        "ingest_movie",
        "ingest_tv",
        "ingest_book_enrichment",
        "ingest_liturgy_bcp",
        "ingest_liturgy_lff",
    }
    assert set(HANDLERS.keys()) == expected
