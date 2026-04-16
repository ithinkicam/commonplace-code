"""Video file ingest handler.

``handle_video_ingest(payload, conn)`` is the worker handler for
``ingest_video`` jobs.  When a capture arrives with ``kind="video"`` and
content containing a local file path:

1. Validate the file exists and has a supported extension.
2. Compute a content hash of the file bytes for idempotency.
3. Extract audio via ffmpeg as 16 kHz mono WAV for Whisper transcription.
4. Extract I-frame keyframes via ffmpeg; run Tesseract OCR on each with
   ``--psm 11`` (sparse text); deduplicate near-identical OCR text.
5. Combine transcript + OCR text into a vault markdown file.
6. Optionally summarize long combined text via ``summarize_capture`` skill.
7. Embed the combined text via ``pipeline.embed_document``.

Typed exceptions
----------------
- :class:`VideoError` -- base.
- :class:`VideoInputError` -- missing/invalid input or file not found.
- :class:`UnsupportedVideoFormat` -- extension not in supported set.
- :class:`VideoProcessingError` -- ffmpeg or transcription failure.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

# Supported video extensions
_SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mkv", ".mov", ".avi", ".webm"})

# Files larger than 2 GB skip keyframe OCR
_MAX_SIZE_FOR_KEYFRAMES: int = 2 * 1024 * 1024 * 1024


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class VideoError(Exception):
    """Base class for video handler errors."""


class VideoInputError(VideoError):
    """Payload is missing required fields or the file cannot be found."""


class UnsupportedVideoFormat(VideoError):
    """Video extension is not in the supported set."""


class VideoProcessingError(VideoError):
    """ffmpeg extraction or transcription failed."""


# ---------------------------------------------------------------------------
# Type aliases for injectable seams
# ---------------------------------------------------------------------------

OcrFn = Callable[[Image.Image], str]
TranscriberFn = Callable[[Path], Any]
SummarizerFn = Callable[[str, str], dict[str, Any] | None]


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------


def _get_video_duration(path: Path) -> float:
    """Use ffprobe to get video duration in seconds."""
    try:
        result = subprocess.run(  # noqa: S603
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0.0))
    except Exception:
        logger.warning("ffprobe failed for %s, duration unknown", path)
    return 0.0


def _extract_audio(video_path: Path, output_path: Path) -> Path:
    """Extract audio from video as 16 kHz mono WAV."""
    try:
        result = subprocess.run(  # noqa: S603
            [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise VideoProcessingError(
            f"ffmpeg audio extraction failed for {video_path}: {exc}"
        ) from exc

    if result.returncode != 0:
        raise VideoProcessingError(
            f"ffmpeg audio extraction exited {result.returncode}: "
            f"{result.stderr[:500]}"
        )
    if not output_path.exists():
        raise VideoProcessingError(
            f"ffmpeg did not produce audio file at {output_path}"
        )
    return output_path


def _extract_keyframes(video_path: Path, output_dir: Path) -> list[Path]:
    """Extract I-frame keyframes from video as JPEG images.

    Returns sorted list of keyframe image paths.
    """
    pattern = str(output_dir / "%04d.jpg")
    try:
        result = subprocess.run(  # noqa: S603
            [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-vf", "select=eq(pict_type\\,I)",
                "-vsync", "vfr",
                "-q:v", "2",
                pattern,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise VideoProcessingError(
            f"ffmpeg keyframe extraction failed for {video_path}: {exc}"
        ) from exc

    if result.returncode != 0:
        raise VideoProcessingError(
            f"ffmpeg keyframe extraction exited {result.returncode}: "
            f"{result.stderr[:500]}"
        )

    frames = sorted(output_dir.glob("*.jpg"))
    return frames


# ---------------------------------------------------------------------------
# OCR helpers
# ---------------------------------------------------------------------------


def _default_ocr(img: Image.Image) -> str:
    """Run Tesseract OCR on a PIL Image with --psm 11 (sparse text)."""
    import pytesseract  # type: ignore[import-untyped]

    return pytesseract.image_to_string(img, config="--psm 11")  # type: ignore[no-any-return]


def _text_similarity(a: str, b: str) -> float:
    """Simple Jaccard similarity on word sets for deduplication."""
    if not a.strip() or not b.strip():
        return 0.0
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def _deduplicate_ocr_texts(texts: list[str], threshold: float = 0.85) -> list[str]:
    """Remove near-duplicate OCR text entries.

    Keeps the first occurrence when subsequent entries have Jaccard
    similarity >= threshold with any already-kept entry.
    """
    kept: list[str] = []
    for text in texts:
        if not text.strip():
            continue
        is_dup = False
        for existing in kept:
            if _text_similarity(text, existing) >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(text)
    return kept


def _ocr_keyframes(
    frames: list[Path],
    ocr_fn: OcrFn,
) -> tuple[list[str], int]:
    """Run OCR on keyframe images and return (deduplicated_texts, frames_processed)."""
    raw_texts: list[str] = []
    for frame_path in frames:
        try:
            img = Image.open(frame_path)
            text = ocr_fn(img).strip()
            if text:
                raw_texts.append(text)
        except Exception:
            logger.warning("OCR failed on keyframe %s", frame_path, exc_info=True)

    deduped = _deduplicate_ocr_texts(raw_texts)
    return deduped, len(frames)


# ---------------------------------------------------------------------------
# Default transcriber
# ---------------------------------------------------------------------------


def _default_transcriber(audio_path: Path) -> Any:
    """Transcribe via the shared transcription module."""
    from commonplace_worker.transcription import transcribe

    return transcribe(audio_path, model_size="medium", language="en")


# ---------------------------------------------------------------------------
# Default summarizer
# ---------------------------------------------------------------------------


def _default_summarizer(text: str, filename: str) -> dict[str, Any] | None:
    """Invoke the summarize_capture skill via claude CLI.

    Returns parsed summary dict or None if summarization is not needed
    or fails.
    """
    from skills.summarize_capture.parser import (
        CaptureSummary,
        parse,
        should_summarize,
        verify_quotes,
    )

    if not should_summarize(text):
        return None

    input_json = json.dumps({
        "source_kind": "other",
        "title": filename,
        "text": text,
    })

    skill_path = (
        Path(__file__).resolve().parent.parent.parent
        / "skills" / "summarize_capture" / "SKILL.md"
    )

    try:
        result = subprocess.run(  # noqa: S603
            [
                "claude", "-p",
                "--system-prompt-file", str(skill_path),
                "--model", "haiku",
                input_json,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("summarize_capture skill invocation failed")
        return None

    if result.returncode != 0:
        logger.warning(
            "summarize_capture exited %d: %s",
            result.returncode,
            result.stderr[:200],
        )
        return None

    try:
        summary: CaptureSummary = parse(result.stdout)
    except Exception:
        logger.warning("summarize_capture output parse failed", exc_info=True)
        return None

    bad_quotes = verify_quotes(summary, text)
    if bad_quotes:
        logger.warning(
            "summarize_capture fabricated %d quotes, dropping them",
            len(bad_quotes),
        )
        summary.quotes = [q for q in summary.quotes if q not in bad_quotes]

    return {
        "description": summary.description,
        "key_points": summary.key_points,
        "quotes": summary.quotes,
    }


# ---------------------------------------------------------------------------
# Vault writing
# ---------------------------------------------------------------------------


def _vault_root() -> Path:
    root = os.environ.get("COMMONPLACE_VAULT_DIR")
    if root:
        return Path(root).expanduser()
    return Path.home() / "commonplace"


def _yaml_escape(value: str) -> str:
    """Minimal YAML-safe escaping for a single-line scalar."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _write_vault_file(
    *,
    content_hash: str,
    path: str,
    filename: str,
    duration_s: float,
    transcript_text: str,
    transcript_words: int,
    ocr_texts: list[str],
    ocr_frames_processed: int,
    ocr_text_found: bool,
    summarized: bool,
    captured_at: datetime,
) -> Path:
    """Atomically write the video capture markdown file and return its path."""
    vault_root = _vault_root()
    year = captured_at.strftime("%Y")
    month = captured_at.strftime("%m")
    out_dir = vault_root / "captures" / year / month
    out_dir.mkdir(parents=True, exist_ok=True)

    hash8 = content_hash[:8]
    ts = captured_at.strftime("%Y-%m-%dT%H%M%SZ")
    fname = f"{ts}-video-{hash8}.md"
    final_path = out_dir / fname
    tmp_path = out_dir / f"{fname}.tmp"

    lines: list[str] = ["---", "source: video"]
    lines.append(f"path: {_yaml_escape(path)}")
    lines.append(f"filename: {_yaml_escape(filename)}")
    lines.append(f"duration_s: {duration_s:.1f}")
    lines.append(f"transcript_words: {transcript_words}")
    lines.append(f"ocr_frames_processed: {ocr_frames_processed}")
    lines.append(f"ocr_text_found: {'true' if ocr_text_found else 'false'}")
    lines.append(f"content_hash: {_yaml_escape(content_hash)}")
    lines.append(f"summarized: {'true' if summarized else 'false'}")
    lines.append(
        f"captured_at: {_yaml_escape(captured_at.strftime('%Y-%m-%dT%H:%M:%SZ'))}"
    )
    lines.append("---")
    lines.append("")

    # Transcript section
    lines.append("## Transcript")
    lines.append("")
    if transcript_text.strip():
        lines.append(transcript_text.strip())
    else:
        lines.append("*No audio transcript available.*")
    lines.append("")

    # Text overlays section
    lines.append("## Text overlays")
    lines.append("")
    if ocr_texts:
        for ocr_text in ocr_texts:
            lines.append(ocr_text.strip())
            lines.append("")
    else:
        lines.append("*No text overlays detected.*")
        lines.append("")

    content = "\n".join(lines)

    with tmp_path.open("w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    tmp_path.rename(final_path)
    return final_path


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_video_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _transcriber: TranscriberFn | None = None,
    _ocr: OcrFn | None = None,
    _summarizer: SummarizerFn | None = None,
    _embedder: Any | None = None,
    _ffmpeg_extract_audio: Any | None = None,
    _ffmpeg_extract_keyframes: Any | None = None,
    _ffmpeg_get_duration: Any | None = None,
) -> dict[str, Any]:
    """Worker handler for ``ingest_video`` jobs.

    Parameters
    ----------
    payload:
        ``{"path": str, "inbox_file": str | None}``
    conn:
        Open SQLite connection with migrations applied.
    _transcriber:
        Override for Whisper transcription (testing).  Callable taking
        ``audio_path: Path`` and returning ``TranscriptionResult``.
    _ocr:
        Override for Tesseract OCR (testing).  Callable taking
        ``PIL.Image`` and returning ``str``.
    _summarizer:
        Override for summarize_capture skill (testing).  Callable taking
        ``(text, filename)`` and returning ``dict | None``.
    _embedder:
        Optional embedder override forwarded to ``pipeline.embed_document``.
    _ffmpeg_extract_audio:
        Override for audio extraction (testing).
    _ffmpeg_extract_keyframes:
        Override for keyframe extraction (testing).
    _ffmpeg_get_duration:
        Override for duration probe (testing).

    Returns
    -------
    dict with keys: ``document_id``, ``chunk_count``, ``elapsed_ms``,
    ``path``, ``duration_s``, ``transcript_words``,
    ``ocr_frames_processed``, ``ocr_text_found``, ``summarized``.
    """
    t0 = time.monotonic()

    # 1. Validate path
    path_str = payload.get("path")
    if not isinstance(path_str, str) or not path_str.strip():
        raise VideoInputError(f"ingest_video payload missing 'path': {payload!r}")

    video_path = Path(path_str)
    if not video_path.exists():
        raise VideoInputError(f"video file not found: {path_str}")

    # 2. Check supported format
    ext = video_path.suffix.lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        raise UnsupportedVideoFormat(
            f"unsupported video format: {ext!r}. "
            f"Supported: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}"
        )

    # 3. Content hash for idempotency
    file_hash = hashlib.sha256(video_path.read_bytes()).hexdigest()

    existing = conn.execute(
        "SELECT id FROM documents WHERE content_hash = ?",
        (file_hash,),
    ).fetchone()
    if existing is not None:
        existing_id = int(existing["id"])
        chunk_count_row = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (existing_id,)
        ).fetchone()
        chunk_count = int(chunk_count_row[0]) if chunk_count_row else 0
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info("video already ingested document_id=%d", existing_id)
        return {
            "document_id": existing_id,
            "chunk_count": chunk_count,
            "elapsed_ms": elapsed_ms,
            "path": path_str,
            "duration_s": 0.0,
            "transcript_words": 0,
            "ocr_frames_processed": 0,
            "ocr_text_found": False,
            "summarized": False,
        }

    # 4. Set up callables
    transcriber = _transcriber if _transcriber is not None else _default_transcriber
    ocr_fn: OcrFn = _ocr if _ocr is not None else _default_ocr
    summarizer = _summarizer if _summarizer is not None else _default_summarizer
    extract_audio = (
        _ffmpeg_extract_audio
        if _ffmpeg_extract_audio is not None
        else _extract_audio
    )
    extract_keyframes = (
        _ffmpeg_extract_keyframes
        if _ffmpeg_extract_keyframes is not None
        else _extract_keyframes
    )
    get_duration = (
        _ffmpeg_get_duration
        if _ffmpeg_get_duration is not None
        else _get_video_duration
    )

    # 5. Get duration
    duration_s: float = get_duration(video_path)

    # 6. Extract audio and transcribe
    transcript_text = ""
    transcript_words = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Audio extraction + transcription
        audio_path = tmpdir_path / "audio.wav"
        try:
            extract_audio(video_path, audio_path)
            result = transcriber(audio_path)
            transcript_text = result.text
            transcript_words = len(transcript_text.split())
            if result.duration_s and duration_s == 0.0:
                duration_s = result.duration_s
        except Exception as exc:
            logger.warning(
                "audio transcription failed for %s: %s", video_path, exc
            )
            # Continue — we may still get OCR text

        # 7. Keyframe extraction + OCR
        ocr_texts: list[str] = []
        ocr_frames_processed = 0
        file_size = video_path.stat().st_size

        if file_size > _MAX_SIZE_FOR_KEYFRAMES:
            logger.info(
                "video file %s is %.1f GB, skipping keyframe OCR",
                video_path,
                file_size / (1024**3),
            )
        else:
            keyframe_dir = tmpdir_path / "keyframes"
            keyframe_dir.mkdir()
            try:
                frames = extract_keyframes(video_path, keyframe_dir)
                ocr_texts, ocr_frames_processed = _ocr_keyframes(frames, ocr_fn)
            except Exception as exc:
                logger.warning(
                    "keyframe OCR failed for %s: %s", video_path, exc
                )

    # Temp files are cleaned up by exiting the TemporaryDirectory context

    ocr_text_found = len(ocr_texts) > 0

    # 8. Build combined text for embedding
    combined_parts: list[str] = []
    if transcript_text.strip():
        combined_parts.append(transcript_text.strip())
    if ocr_texts:
        combined_parts.append("\n\n".join(ocr_texts))
    combined_text = "\n\n".join(combined_parts)

    if not combined_text.strip():
        raise VideoProcessingError(
            f"no transcript or OCR text could be extracted from {video_path}"
        )

    # 9. Optional summarization
    filename = video_path.name
    summary_result = summarizer(combined_text, filename)
    summarized = summary_result is not None

    # 10. Write vault file
    captured_at = datetime.now(UTC)
    vault_path = _write_vault_file(
        content_hash=file_hash,
        path=path_str,
        filename=filename,
        duration_s=duration_s,
        transcript_text=transcript_text,
        transcript_words=transcript_words,
        ocr_texts=ocr_texts,
        ocr_frames_processed=ocr_frames_processed,
        ocr_text_found=ocr_text_found,
        summarized=summarized,
        captured_at=captured_at,
    )

    # 11. Insert documents row
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO documents
                (content_type, source_uri, title, content_hash,
                 raw_path, source_id, status)
            VALUES ('video', ?, ?, ?, ?, ?, 'ingesting')
            """,
            (
                path_str,
                filename,
                file_hash,
                str(vault_path),
                file_hash,
            ),
        )
    document_id: int = cursor.lastrowid  # type: ignore[assignment]

    # 12. Embed
    from commonplace_server.pipeline import embed_document

    embed_text = combined_text
    if summarized and summary_result:
        parts = [summary_result.get("description", "")]
        for kp in summary_result.get("key_points", []):
            parts.append(kp)
        for q in summary_result.get("quotes", []):
            parts.append(q)
        embed_text = "\n\n".join(parts)

    embed_kwargs: dict[str, Any] = {}
    if _embedder is not None:
        embed_kwargs["_embedder"] = _embedder
    embed_result = embed_document(document_id, embed_text, conn, **embed_kwargs)

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "ingested video document_id=%d chunks=%d path=%s elapsed_ms=%.0f",
        document_id,
        embed_result.chunk_count,
        path_str,
        elapsed_ms,
    )
    return {
        "document_id": document_id,
        "chunk_count": embed_result.chunk_count,
        "elapsed_ms": elapsed_ms,
        "path": path_str,
        "duration_s": duration_s,
        "transcript_words": transcript_words,
        "ocr_frames_processed": ocr_frames_processed,
        "ocr_text_found": ocr_text_found,
        "summarized": summarized,
    }
