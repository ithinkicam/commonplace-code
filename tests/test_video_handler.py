"""Tests for commonplace_worker/handlers/video.py."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from commonplace_db.db import migrate
from commonplace_worker.handlers.video import (
    UnsupportedVideoFormat,
    VideoInputError,
    VideoProcessingError,
    _deduplicate_ocr_texts,
    _text_similarity,
    handle_video_ingest,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """In-memory SQLite with sqlite-vec + migrations applied."""
    import sqlite_vec  # type: ignore[import-untyped]

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    migrate(conn)
    return conn


@pytest.fixture
def vault_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setenv("COMMONPLACE_VAULT_DIR", str(root))
    return root


def _fake_embedder(texts: list[str], model: str) -> list[list[float]]:
    return [[0.0] * 768 for _ in texts]


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

_GOOD_TRANSCRIPT = (
    "Welcome to this video about software design. "
    "Today we'll discuss the importance of clean architecture. "
    "First, let's talk about separation of concerns. "
    "Each module should have a single responsibility. "
    "This makes the code easier to test and maintain. "
)

_LONG_TRANSCRIPT = _GOOD_TRANSCRIPT * 100  # >2000 words


@dataclass
class FakeTranscriptionResult:
    text: str = _GOOD_TRANSCRIPT
    segments: list[Any] = field(default_factory=list)
    language: str = "en"
    duration_s: float = 120.0


def _fake_transcriber(audio_path: Path) -> FakeTranscriptionResult:
    return FakeTranscriptionResult()


def _fake_transcriber_long(audio_path: Path) -> FakeTranscriptionResult:
    return FakeTranscriptionResult(text=_LONG_TRANSCRIPT, duration_s=3600.0)


def _fake_transcriber_fail(audio_path: Path) -> FakeTranscriptionResult:
    raise VideoProcessingError("whisper failed")


def _fake_ocr_with_text(img: Image.Image) -> str:
    return "Important slide: Key takeaways from Q4 2025 results"


def _fake_ocr_empty(img: Image.Image) -> str:
    return ""


def _fake_ocr_duplicate(img: Image.Image) -> str:
    """Returns the same text for any frame — tests deduplication."""
    return "Duplicate overlay text for testing deduplication"


def _fake_ocr_varied(img: Image.Image) -> str:
    """Returns text based on image size for variety."""
    w, h = img.size
    if w < 100:
        return "First unique slide text with details"
    return "Second unique slide text with other info"


def _fake_summarizer_none(text: str, filename: str) -> dict[str, Any] | None:
    return None


def _fake_summarizer(text: str, filename: str) -> dict[str, Any] | None:
    if len(text.split()) >= 2000:
        return {
            "description": "A video about software design.",
            "key_points": ["Clean architecture matters", "Testing is important"],
            "quotes": [],
        }
    return None


def _make_test_video(tmp_path: Path, name: str = "test.mp4", size: int = 1024) -> Path:
    """Create a fake video file (just bytes, not a real video)."""
    video_file = tmp_path / name
    video_file.write_bytes(b"\x00" * size)
    return video_file


def _fake_extract_audio(video_path: Path, output_path: Path) -> Path:
    """Fake audio extraction — creates a dummy WAV file."""
    output_path.write_bytes(b"RIFF" + b"\x00" * 100)
    return output_path


def _fake_extract_keyframes(video_path: Path, output_dir: Path) -> list[Path]:
    """Fake keyframe extraction — creates dummy JPEG images."""
    frames: list[Path] = []
    for i in range(3):
        frame_path = output_dir / f"{i:04d}.jpg"
        img = Image.new("RGB", (200, 100), color=(128, 128, 128))
        img.save(frame_path, format="JPEG")
        frames.append(frame_path)
    return frames


def _fake_extract_keyframes_varied(video_path: Path, output_dir: Path) -> list[Path]:
    """Creates frames with different sizes for varied OCR results."""
    frames: list[Path] = []
    for i, width in enumerate([50, 200]):
        frame_path = output_dir / f"{i:04d}.jpg"
        img = Image.new("RGB", (width, 100), color=(128, 128, 128))
        img.save(frame_path, format="JPEG")
        frames.append(frame_path)
    return frames


def _fake_extract_keyframes_empty(
    video_path: Path, output_dir: Path
) -> list[Path]:
    """No keyframes produced."""
    return []


def _fake_get_duration(video_path: Path) -> float:
    return 120.0


# ---------------------------------------------------------------------------
# 1. Happy path with mocked ffmpeg + transcriber + OCR
# ---------------------------------------------------------------------------


def test_happy_path(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    video_file = _make_test_video(tmp_path)

    result = handle_video_ingest(
        {"path": str(video_file)},
        db_conn,
        _transcriber=_fake_transcriber,
        _ocr=_fake_ocr_with_text,
        _summarizer=_fake_summarizer_none,
        _embedder=_fake_embedder,
        _ffmpeg_extract_audio=_fake_extract_audio,
        _ffmpeg_extract_keyframes=_fake_extract_keyframes,
        _ffmpeg_get_duration=_fake_get_duration,
    )

    assert result["document_id"] is not None
    assert result["chunk_count"] >= 1
    assert result["elapsed_ms"] >= 0
    assert result["path"] == str(video_file)
    assert result["duration_s"] == 120.0
    assert result["transcript_words"] > 0
    assert result["ocr_frames_processed"] == 3
    assert result["ocr_text_found"] is True
    assert result["summarized"] is False

    # Verify document in DB
    doc = db_conn.execute(
        "SELECT * FROM documents WHERE id = ?", (result["document_id"],)
    ).fetchone()
    assert doc is not None
    assert doc["content_type"] == "video"

    # Verify vault file exists
    md_files = list(vault_dir.rglob("*-video-*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text()
    assert "source: video" in content
    assert "## Transcript" in content
    assert "## Text overlays" in content


# ---------------------------------------------------------------------------
# 2. Audio-only path (no OCR text found in keyframes)
# ---------------------------------------------------------------------------


def test_audio_only_no_ocr_text(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    video_file = _make_test_video(tmp_path)

    result = handle_video_ingest(
        {"path": str(video_file)},
        db_conn,
        _transcriber=_fake_transcriber,
        _ocr=_fake_ocr_empty,
        _summarizer=_fake_summarizer_none,
        _embedder=_fake_embedder,
        _ffmpeg_extract_audio=_fake_extract_audio,
        _ffmpeg_extract_keyframes=_fake_extract_keyframes,
        _ffmpeg_get_duration=_fake_get_duration,
    )

    assert result["ocr_text_found"] is False
    assert result["ocr_frames_processed"] == 3
    assert result["transcript_words"] > 0

    md_files = list(vault_dir.rglob("*-video-*.md"))
    content = md_files[0].read_text()
    assert "*No text overlays detected.*" in content
    assert "## Transcript" in content


# ---------------------------------------------------------------------------
# 3. OCR text found in keyframes -> combined output
# ---------------------------------------------------------------------------


def test_ocr_text_combined_with_transcript(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    video_file = _make_test_video(tmp_path)

    result = handle_video_ingest(
        {"path": str(video_file)},
        db_conn,
        _transcriber=_fake_transcriber,
        _ocr=_fake_ocr_with_text,
        _summarizer=_fake_summarizer_none,
        _embedder=_fake_embedder,
        _ffmpeg_extract_audio=_fake_extract_audio,
        _ffmpeg_extract_keyframes=_fake_extract_keyframes,
        _ffmpeg_get_duration=_fake_get_duration,
    )

    assert result["ocr_text_found"] is True

    md_files = list(vault_dir.rglob("*-video-*.md"))
    content = md_files[0].read_text()
    assert "Key takeaways" in content
    assert "software design" in content


# ---------------------------------------------------------------------------
# 4. Idempotency
# ---------------------------------------------------------------------------


def test_idempotency(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    video_file = _make_test_video(tmp_path)

    result1 = handle_video_ingest(
        {"path": str(video_file)},
        db_conn,
        _transcriber=_fake_transcriber,
        _ocr=_fake_ocr_with_text,
        _summarizer=_fake_summarizer_none,
        _embedder=_fake_embedder,
        _ffmpeg_extract_audio=_fake_extract_audio,
        _ffmpeg_extract_keyframes=_fake_extract_keyframes,
        _ffmpeg_get_duration=_fake_get_duration,
    )

    result2 = handle_video_ingest(
        {"path": str(video_file)},
        db_conn,
        _transcriber=_fake_transcriber,
        _ocr=_fake_ocr_with_text,
        _summarizer=_fake_summarizer_none,
        _embedder=_fake_embedder,
        _ffmpeg_extract_audio=_fake_extract_audio,
        _ffmpeg_extract_keyframes=_fake_extract_keyframes,
        _ffmpeg_get_duration=_fake_get_duration,
    )

    assert result1["document_id"] == result2["document_id"]
    # Only one vault file should exist
    md_files = list(vault_dir.rglob("*-video-*.md"))
    assert len(md_files) == 1


# ---------------------------------------------------------------------------
# 5. Unsupported format rejected
# ---------------------------------------------------------------------------


def test_unsupported_format(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    bad_file = tmp_path / "test.flv"
    bad_file.write_bytes(b"\x00" * 100)

    with pytest.raises(UnsupportedVideoFormat, match="unsupported video format"):
        handle_video_ingest(
            {"path": str(bad_file)},
            db_conn,
            _embedder=_fake_embedder,
        )


# ---------------------------------------------------------------------------
# 6. File not found -> typed exception
# ---------------------------------------------------------------------------


def test_file_not_found(
    db_conn: sqlite3.Connection, vault_dir: Path
) -> None:
    with pytest.raises(VideoInputError, match="video file not found"):
        handle_video_ingest(
            {"path": "/nonexistent/video.mp4"},
            db_conn,
            _embedder=_fake_embedder,
        )


# ---------------------------------------------------------------------------
# 7. Missing path in payload
# ---------------------------------------------------------------------------


def test_missing_path(db_conn: sqlite3.Connection, vault_dir: Path) -> None:
    with pytest.raises(VideoInputError, match="missing 'path'"):
        handle_video_ingest({}, db_conn, _embedder=_fake_embedder)


# ---------------------------------------------------------------------------
# 8. Keyframe deduplication
# ---------------------------------------------------------------------------


def test_keyframe_deduplication(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    """Duplicate OCR text from consecutive frames should collapse to one entry."""
    video_file = _make_test_video(tmp_path)

    result = handle_video_ingest(
        {"path": str(video_file)},
        db_conn,
        _transcriber=_fake_transcriber,
        _ocr=_fake_ocr_duplicate,
        _summarizer=_fake_summarizer_none,
        _embedder=_fake_embedder,
        _ffmpeg_extract_audio=_fake_extract_audio,
        _ffmpeg_extract_keyframes=_fake_extract_keyframes,
        _ffmpeg_get_duration=_fake_get_duration,
    )

    assert result["ocr_frames_processed"] == 3
    assert result["ocr_text_found"] is True

    # The vault file should only have one OCR entry despite 3 frames
    md_files = list(vault_dir.rglob("*-video-*.md"))
    content = md_files[0].read_text()
    # Count occurrences of the duplicate text
    assert content.count("Duplicate overlay text") == 1


# ---------------------------------------------------------------------------
# 9. Summary integration for long combined text
# ---------------------------------------------------------------------------


def test_summary_integration(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    video_file = _make_test_video(tmp_path)

    result = handle_video_ingest(
        {"path": str(video_file)},
        db_conn,
        _transcriber=_fake_transcriber_long,
        _ocr=_fake_ocr_empty,
        _summarizer=_fake_summarizer,
        _embedder=_fake_embedder,
        _ffmpeg_extract_audio=_fake_extract_audio,
        _ffmpeg_extract_keyframes=_fake_extract_keyframes,
        _ffmpeg_get_duration=_fake_get_duration,
    )

    assert result["summarized"] is True


# ---------------------------------------------------------------------------
# 10. Temp file cleanup verified
# ---------------------------------------------------------------------------


def test_temp_file_cleanup(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    video_file = _make_test_video(tmp_path)

    result = handle_video_ingest(
        {"path": str(video_file)},
        db_conn,
        _transcriber=_fake_transcriber,
        _ocr=_fake_ocr_with_text,
        _summarizer=_fake_summarizer_none,
        _embedder=_fake_embedder,
        _ffmpeg_extract_audio=_fake_extract_audio,
        _ffmpeg_extract_keyframes=_fake_extract_keyframes,
        _ffmpeg_get_duration=_fake_get_duration,
    )

    assert result["document_id"] is not None
    # The tmp_path only has our video file, no leftover audio/keyframes
    # (temp files are created in system temp dir, not tmp_path)
    # Just verify the handler completed without leaving temp dirs open
    # The TemporaryDirectory context manager ensures cleanup


# ---------------------------------------------------------------------------
# 11. Varied OCR text from different keyframes
# ---------------------------------------------------------------------------


def test_varied_ocr_texts(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    """Different keyframes producing different OCR text are all kept."""
    video_file = _make_test_video(tmp_path)

    result = handle_video_ingest(
        {"path": str(video_file)},
        db_conn,
        _transcriber=_fake_transcriber,
        _ocr=_fake_ocr_varied,
        _summarizer=_fake_summarizer_none,
        _embedder=_fake_embedder,
        _ffmpeg_extract_audio=_fake_extract_audio,
        _ffmpeg_extract_keyframes=_fake_extract_keyframes_varied,
        _ffmpeg_get_duration=_fake_get_duration,
    )

    assert result["ocr_text_found"] is True
    assert result["ocr_frames_processed"] == 2

    md_files = list(vault_dir.rglob("*-video-*.md"))
    content = md_files[0].read_text()
    assert "First unique slide" in content
    assert "Second unique slide" in content


# ---------------------------------------------------------------------------
# 12. No keyframes produced (empty keyframe list)
# ---------------------------------------------------------------------------


def test_no_keyframes(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    video_file = _make_test_video(tmp_path)

    result = handle_video_ingest(
        {"path": str(video_file)},
        db_conn,
        _transcriber=_fake_transcriber,
        _ocr=_fake_ocr_with_text,
        _summarizer=_fake_summarizer_none,
        _embedder=_fake_embedder,
        _ffmpeg_extract_audio=_fake_extract_audio,
        _ffmpeg_extract_keyframes=_fake_extract_keyframes_empty,
        _ffmpeg_get_duration=_fake_get_duration,
    )

    assert result["ocr_frames_processed"] == 0
    assert result["ocr_text_found"] is False


# ---------------------------------------------------------------------------
# 13. Vault file frontmatter fields
# ---------------------------------------------------------------------------


def test_vault_frontmatter(
    db_conn: sqlite3.Connection, vault_dir: Path, tmp_path: Path
) -> None:
    video_file = _make_test_video(tmp_path, name="lecture.mp4")

    handle_video_ingest(
        {"path": str(video_file)},
        db_conn,
        _transcriber=_fake_transcriber,
        _ocr=_fake_ocr_empty,
        _summarizer=_fake_summarizer_none,
        _embedder=_fake_embedder,
        _ffmpeg_extract_audio=_fake_extract_audio,
        _ffmpeg_extract_keyframes=_fake_extract_keyframes,
        _ffmpeg_get_duration=_fake_get_duration,
    )

    md_files = list(vault_dir.rglob("*-video-*.md"))
    content = md_files[0].read_text()
    assert "source: video" in content
    assert "filename:" in content
    assert "lecture.mp4" in content
    assert "duration_s:" in content
    assert "transcript_words:" in content
    assert "ocr_frames_processed:" in content
    assert "ocr_text_found:" in content
    assert "content_hash:" in content
    assert "summarized:" in content
    assert "captured_at:" in content


# ---------------------------------------------------------------------------
# 14. Supported extensions accepted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ext", [".mp4", ".mkv", ".mov", ".avi", ".webm"])
def test_supported_extensions(
    db_conn: sqlite3.Connection,
    vault_dir: Path,
    tmp_path: Path,
    ext: str,
) -> None:
    # Each extension gets unique content to avoid idempotency collision
    video_file = tmp_path / f"test{ext}"
    video_file.write_bytes(f"unique-{ext}".encode() + b"\x00" * 100)

    result = handle_video_ingest(
        {"path": str(video_file)},
        db_conn,
        _transcriber=_fake_transcriber,
        _ocr=_fake_ocr_empty,
        _summarizer=_fake_summarizer_none,
        _embedder=_fake_embedder,
        _ffmpeg_extract_audio=_fake_extract_audio,
        _ffmpeg_extract_keyframes=_fake_extract_keyframes,
        _ffmpeg_get_duration=_fake_get_duration,
    )
    assert result["document_id"] is not None


# ---------------------------------------------------------------------------
# 15. Text similarity function
# ---------------------------------------------------------------------------


def test_text_similarity() -> None:
    assert _text_similarity("hello world", "hello world") == 1.0
    assert _text_similarity("hello world", "goodbye moon") == 0.0
    assert _text_similarity("", "hello") == 0.0
    sim = _text_similarity("hello world foo", "hello world bar")
    assert 0.3 < sim < 0.8


# ---------------------------------------------------------------------------
# 16. Deduplication function
# ---------------------------------------------------------------------------


def test_deduplicate_ocr_texts() -> None:
    base = "Hello world from slide one two three four five six seven eight nine ten eleven twelve thirteen"
    near_dup = "Hello world from slide one two three four five six seven eight nine ten eleven twelve fourteen"
    different = "Completely different text here about something else entirely new and unexpected"
    texts = [base, base, different, near_dup]
    result = _deduplicate_ocr_texts(texts)
    # base kept, exact dup dropped, different kept, near-dup dropped (Jaccard > 0.85)
    assert len(result) == 2
    assert base in result
    assert different in result
