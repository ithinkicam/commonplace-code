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

Stage-level checkpointing
-------------------------
Each expensive stage (hash, audio extract, transcribe, keyframes, OCR,
doc write) records completion via a shared :class:`Checkpointer` so a
re-queued job resumes from the last complete stage rather than
re-running ffmpeg + Whisper + Tesseract from scratch. When ``_job_id``
is absent (direct-call tests) the checkpointer is a no-op and the
handler runs every stage, which is the pre-feature behaviour the test
suite still relies on.

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

from commonplace_worker.checkpoints import for_payload, stage_cache_dir
from commonplace_worker.claude_skill import SkillFailure, SkillTimeout, run_skill
from commonplace_worker.errors import RetryableHandlerError
from commonplace_worker.frontmatter import render_embed_header, slugify, yaml_escape
from commonplace_worker.handlers._alarm_timeout import alarm_timeout
from commonplace_worker.vault_io import atomic_write_text, vault_root

OCR_TIMEOUT_SECONDS = int(os.environ.get("COMMONPLACE_OCR_TIMEOUT", "120"))

logger = logging.getLogger(__name__)

# Supported video extensions
_SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mkv", ".mov", ".avi", ".webm"})

# Files larger than 2 GB skip keyframe OCR
_MAX_SIZE_FOR_KEYFRAMES: int = 2 * 1024 * 1024 * 1024

# Substrings in ffmpeg stderr that suggest a transient failure worth retrying.
_FFMPEG_TRANSIENT_MARKERS: tuple[str, ...] = (
    "connection reset",
    "connection refused",
    "temporary failure",
    "resource temporarily unavailable",
    "out of memory",
    "cannot allocate memory",
    "network is unreachable",
    "timed out",
    "timeout",
)


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


def _looks_transient(stderr: str) -> bool:
    """Return True if ffmpeg stderr hints at a re-tryable failure."""
    lowered = stderr.lower()
    return any(marker in lowered for marker in _FFMPEG_TRANSIENT_MARKERS)


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
    """Run Tesseract OCR on a PIL Image with --psm 11 (sparse text).

    Bounded by SIGALRM at OCR_TIMEOUT_SECONDS; pytesseract itself has no
    timeout parameter and can hang indefinitely on pathological inputs.
    Video ingest calls this once per keyframe, so a hang here would stall
    the entire job.
    """
    import pytesseract  # type: ignore[import-untyped]

    with alarm_timeout(OCR_TIMEOUT_SECONDS, message="pytesseract.image_to_string"):
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
    """Invoke the summarize_capture skill via the shared claude wrapper.

    Returns parsed summary dict or None if summarization is not needed
    or fails. ``SkillTimeout`` / ``SkillFailure`` are swallowed because
    summarization is strictly optional — callers fall back to embedding
    the raw combined text.
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
        result = run_skill(
            skill_md=skill_path,
            payload=input_json,
            model="haiku",
            timeout_s=120,
        )
    except (SkillTimeout, SkillFailure) as exc:
        logger.warning("summarize_capture skill invocation failed: %s", exc)
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
    root = vault_root()
    year = captured_at.strftime("%Y")
    month = captured_at.strftime("%m")
    out_dir = root / "captures" / year / month
    out_dir.mkdir(parents=True, exist_ok=True)

    hash8 = content_hash[:8]
    ts = captured_at.strftime("%Y-%m-%dT%H%M%SZ")
    # slugify() is defensive for unusual filenames but we intentionally
    # keep the legacy ``-<hash8>`` suffix: content-hash uniqueness is what
    # the idempotency check relies on, and the filename tells at a glance
    # which video the vault file came from.
    _ = slugify(filename, fallback="video")
    fname = f"{ts}-video-{hash8}.md"
    final_path = out_dir / fname

    lines: list[str] = ["---", "source: video"]
    lines.append(f"path: {yaml_escape(path)}")
    lines.append(f"filename: {yaml_escape(filename)}")
    lines.append(f"duration_s: {duration_s:.1f}")
    lines.append(f"transcript_words: {transcript_words}")
    lines.append(f"ocr_frames_processed: {ocr_frames_processed}")
    lines.append(f"ocr_text_found: {'true' if ocr_text_found else 'false'}")
    lines.append(f"content_hash: {yaml_escape(content_hash)}")
    lines.append(f"summarized: {'true' if summarized else 'false'}")
    lines.append(
        f"captured_at: {yaml_escape(captured_at.strftime('%Y-%m-%dT%H:%M:%SZ'))}"
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
    atomic_write_text(final_path, content)
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

    # Checkpointing — enabled when the worker injected _job_id, no-op
    # otherwise. ``for_payload`` handles both cases.
    raw_job_id = payload.get("_job_id")
    job_id: int | None = int(raw_job_id) if isinstance(raw_job_id, int) else None
    attempt = payload.get("_attempt", 0)
    ckpt = for_payload(conn, payload, int(attempt) if isinstance(attempt, int) else 0)

    # Fast-path: if the whole document was already written on a prior
    # attempt, rehydrate and return without touching ffmpeg/Whisper at
    # all.
    written = ckpt.get_output("doc_written")
    if written:
        existing_id = int(written["document_id"])
        existing_row = conn.execute(
            "SELECT id FROM documents WHERE id = ?", (existing_id,)
        ).fetchone()
        if existing_row is not None:
            chunk_count_row = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
                (existing_id,),
            ).fetchone()
            chunk_count = int(chunk_count_row[0]) if chunk_count_row else 0
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "video job resumed from doc_written checkpoint document_id=%d",
                existing_id,
            )
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
        # Stored row vanished (janitor, migration) — fall through and
        # recompute rather than trust the stale checkpoint.
        logger.warning(
            "doc_written checkpoint referenced missing document_id=%d; recomputing",
            existing_id,
        )

    # 3. Content hash for idempotency (checkpointed — hashing a 2 GB
    # video is non-trivial, and the hash drives the document-row lookup).
    hash_cached = ckpt.get_output("hash_computed")
    if hash_cached and isinstance(hash_cached.get("content_hash"), str):
        file_hash = str(hash_cached["content_hash"])
    else:
        ckpt.start("hash_computed")
        file_hash = hashlib.sha256(video_path.read_bytes()).hexdigest()
        ckpt.complete("hash_computed", {"content_hash": file_hash})

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

    # 6. Set up a durable scratch dir for audio + transcript when we have
    # a job_id, otherwise fall back to an ephemeral TemporaryDirectory so
    # direct-call tests don't leak state into the stage cache.
    transcript_text = ""
    transcript_words = 0
    ocr_texts: list[str] = []
    ocr_frames_processed = 0

    tmp_ctx: tempfile.TemporaryDirectory[str] | None = None
    if job_id is not None:
        durable_dir = stage_cache_dir(int(job_id))
    else:
        tmp_ctx = tempfile.TemporaryDirectory()
        durable_dir = Path(tmp_ctx.name)

    try:
        # 6a. Audio extraction (resume from checkpoint if wav still on disk).
        audio_out = ckpt.get_output("audio_extracted")
        wav_path: Path | None = None
        if audio_out and isinstance(audio_out.get("wav_path"), str):
            candidate = Path(audio_out["wav_path"])
            if candidate.exists():
                wav_path = candidate

        if wav_path is None:
            ckpt.start("audio_extracted")
            wav_path = durable_dir / "audio.wav"
            try:
                extract_audio(video_path, wav_path)
            except VideoProcessingError as exc:
                if _looks_transient(str(exc)):
                    raise RetryableHandlerError(
                        f"ffmpeg audio extraction looks transient for {video_path}: {exc}"
                    ) from exc
                # Non-transient extraction error — swallow; we may still
                # get usable OCR text from keyframes. Do NOT mark the
                # stage complete, so a retry will try again.
                logger.warning(
                    "audio extraction failed for %s: %s", video_path, exc
                )
                wav_path = None
            else:
                ckpt.complete(
                    "audio_extracted", {"wav_path": str(wav_path)}
                )

        # 6b. Transcription (resume from checkpoint if transcript file exists).
        if wav_path is not None:
            transcript_out = ckpt.get_output("transcribed")
            if (
                transcript_out
                and isinstance(transcript_out.get("text_path"), str)
                and Path(transcript_out["text_path"]).exists()
            ):
                transcript_text = Path(transcript_out["text_path"]).read_text(
                    encoding="utf-8"
                )
                transcript_words = len(transcript_text.split())
            else:
                ckpt.start("transcribed")
                try:
                    result = transcriber(wav_path)
                    transcript_text = result.text
                    transcript_words = len(transcript_text.split())
                    if result.duration_s and duration_s == 0.0:
                        duration_s = result.duration_s

                    transcript_path = durable_dir / "transcript.txt"
                    transcript_path.write_text(transcript_text, encoding="utf-8")
                    ckpt.complete(
                        "transcribed",
                        {
                            "text_path": str(transcript_path),
                            "source": "whisper",
                        },
                    )
                except VideoProcessingError as exc:
                    if _looks_transient(str(exc)):
                        raise RetryableHandlerError(
                            f"transcription looks transient for {video_path}: {exc}"
                        ) from exc
                    logger.warning(
                        "audio transcription failed for %s: %s", video_path, exc
                    )
                    transcript_text = ""
                    transcript_words = 0
                except Exception as exc:
                    # Whisper model-load / allocation errors from the
                    # faster-whisper / torch stack don't inherit from
                    # VideoProcessingError. Treat anything with a
                    # transient-looking message as retryable; otherwise
                    # continue with OCR-only.
                    if _looks_transient(str(exc)):
                        raise RetryableHandlerError(
                            f"transcription looks transient for {video_path}: {exc}"
                        ) from exc
                    logger.warning(
                        "audio transcription failed for %s: %s", video_path, exc
                    )
                    transcript_text = ""
                    transcript_words = 0

        # 6c. Keyframe extraction + OCR.
        #
        # Design choice: keyframes themselves are kept in-memory /
        # ephemeral; only the *deduplicated OCR text list* is persisted
        # as a checkpoint payload. The text list is tiny (a few KB),
        # whereas the keyframe directory can balloon into hundreds of
        # MB on long videos, and skipping ffmpeg on resume is the only
        # thing actually worth persisting.
        file_size = video_path.stat().st_size
        ocr_done = ckpt.get_output("ocr_done")
        if ocr_done and isinstance(ocr_done.get("texts"), list):
            ocr_texts = [str(t) for t in ocr_done["texts"]]
            ocr_frames_processed = int(ocr_done.get("frames_processed", 0))
        elif file_size > _MAX_SIZE_FOR_KEYFRAMES:
            logger.info(
                "video file %s is %.1f GB, skipping keyframe OCR",
                video_path,
                file_size / (1024**3),
            )
            ckpt.complete(
                "ocr_done",
                {"texts": [], "frames_processed": 0, "skipped": True},
            )
        else:
            ckpt.start("keyframes_extracted")
            with tempfile.TemporaryDirectory() as kf_tmp:
                keyframe_dir = Path(kf_tmp)
                try:
                    frames = extract_keyframes(video_path, keyframe_dir)
                    ckpt.complete(
                        "keyframes_extracted",
                        {"frame_count": len(frames)},
                    )
                    ckpt.start("ocr_done")
                    ocr_texts, ocr_frames_processed = _ocr_keyframes(
                        frames, ocr_fn
                    )
                    ckpt.complete(
                        "ocr_done",
                        {
                            "texts": ocr_texts,
                            "frames_processed": ocr_frames_processed,
                        },
                    )
                except VideoProcessingError as exc:
                    if _looks_transient(str(exc)):
                        raise RetryableHandlerError(
                            f"keyframe extraction looks transient for {video_path}: {exc}"
                        ) from exc
                    logger.warning(
                        "keyframe OCR failed for %s: %s", video_path, exc
                    )
                except Exception as exc:
                    logger.warning(
                        "keyframe OCR failed for %s: %s", video_path, exc
                    )
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()

    ocr_text_found = len(ocr_texts) > 0

    # 7. Build combined text for embedding
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

    # 8. Optional summarization
    filename = video_path.name
    summary_result = summarizer(combined_text, filename)
    summarized = summary_result is not None

    # 9. Write vault file
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

    # 10. Insert documents row
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

    # 11. Embed. Prepend filename header so filename-based search hits
    # chunk 0 even when the video has no spoken title announcement.
    from commonplace_server.pipeline import embed_document

    body_text = combined_text
    if summarized and summary_result:
        parts = [summary_result.get("description", "")]
        for kp in summary_result.get("key_points", []):
            parts.append(kp)
        for q in summary_result.get("quotes", []):
            parts.append(q)
        body_text = "\n\n".join(parts)

    header = render_embed_header(
        [
            ("Filename", filename),
            ("Captured", captured_at.strftime("%Y-%m-%d")),
        ]
    )
    embed_text = header + body_text

    embed_kwargs: dict[str, Any] = {}
    if _embedder is not None:
        embed_kwargs["_embedder"] = _embedder
    embed_result = embed_document(document_id, embed_text, conn, **embed_kwargs)

    ckpt.complete(
        "doc_written",
        {
            "document_id": document_id,
            "vault_path": str(vault_path),
            "content_hash": file_hash,
        },
    )

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
