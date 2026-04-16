"""YouTube URL ingest handler.

``handle_youtube_ingest(payload, conn)`` is the worker handler for
``ingest_youtube`` jobs.

Behaviour
---------
1. Validate and normalize the YouTube URL to canonical form.
2. Download captions via yt-dlp (prefer manual ``en``, fall back to auto).
3. Assess auto-caption quality; if poor → Whisper fallback via shared
   transcription module.
4. Write vault file atomically (YAML frontmatter + markdown body).
5. Optionally invoke ``summarize_capture`` skill for long content (>2000 words).
6. Embed the summary (or full text if short) via ``pipeline.embed_document``.

Typed exceptions
----------------
- :class:`YouTubeError` — base.
- :class:`YouTubeFetchError` — yt-dlp failure or invalid URL.
- :class:`YouTubeTranscriptionError` — both captions and Whisper failed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class YouTubeError(Exception):
    """Base class for YouTube handler errors."""


class YouTubeFetchError(YouTubeError):
    """yt-dlp failed or URL is invalid."""


class YouTubeTranscriptionError(YouTubeError):
    """Neither captions nor Whisper produced a usable transcript."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class VideoMetadata:
    """Metadata extracted from yt-dlp --dump-json."""

    video_id: str
    title: str
    channel: str
    upload_date: str | None  # YYYYMMDD or None
    duration_s: float


@dataclass
class CaptionResult:
    """Result from caption/transcription extraction."""

    text: str
    source: str  # "manual" | "auto" | "whisper"


# ---------------------------------------------------------------------------
# Default callables (production implementations)
# ---------------------------------------------------------------------------


class _DefaultDownloader:
    """Wraps yt-dlp calls.  Replaced by _downloader in tests."""

    def get_metadata(self, url: str) -> dict[str, Any]:
        """Return yt-dlp --dump-json output as a dict."""
        try:
            result = subprocess.run(  # noqa: S603
                [
                    "yt-dlp",
                    "--dump-json",
                    "--no-download",
                    "--no-playlist",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            raise YouTubeFetchError(f"yt-dlp metadata failed for {url!r}: {exc}") from exc

        if result.returncode != 0:
            raise YouTubeFetchError(
                f"yt-dlp metadata exited {result.returncode} for {url!r}: "
                f"{result.stderr[:500]}"
            )

        try:
            return json.loads(result.stdout)  # type: ignore[no-any-return]
        except json.JSONDecodeError as exc:
            raise YouTubeFetchError(
                f"yt-dlp returned non-JSON for {url!r}: {exc}"
            ) from exc

    def get_captions(self, url: str, lang: str = "en") -> tuple[str | None, str | None]:
        """Return (manual_caption_text, auto_caption_text).

        Either or both may be None if unavailable.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            base = os.path.join(tmpdir, "subs")
            try:
                subprocess.run(  # noqa: S603
                    [
                        "yt-dlp",
                        "--write-sub",
                        "--write-auto-sub",
                        "--sub-lang", lang,
                        "--sub-format", "vtt",
                        "--skip-download",
                        "--no-playlist",
                        "-o", base,
                        url,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                raise YouTubeFetchError(
                    f"yt-dlp captions failed for {url!r}: {exc}"
                ) from exc

            # yt-dlp names auto subs differently — look for any vtt file
            vtt_files = sorted(Path(tmpdir).glob("*.vtt"))

            manual_text = None
            auto_text = None

            for vf in vtt_files:
                content = vf.read_text(encoding="utf-8", errors="replace")
                cleaned = _clean_vtt(content)
                if not cleaned.strip():
                    continue
                # Manual captions don't have "auto" in filename from yt-dlp
                # yt-dlp names auto subs: <base>.<lang>.vtt  (same as manual sometimes)
                # With both flags, manual is <base>.<lang>.vtt, auto is <base>.<lang>.vtt
                # Actually yt-dlp differentiates: manual → .en.vtt, auto → .en.vtt
                # but when both exist, manual has no extra suffix
                # Best heuristic: if only one file, need quality check to decide
                if manual_text is None:
                    manual_text = cleaned
                else:
                    auto_text = cleaned

            # If we only got one file, we need to check metadata to know if manual
            return manual_text, auto_text

    def download_audio(self, url: str, output_path: Path) -> Path:
        """Download audio-only WAV (16kHz mono) for Whisper."""
        try:
            subprocess.run(  # noqa: S603
                [
                    "yt-dlp",
                    "-f", "bestaudio",
                    "-x",
                    "--audio-format", "wav",
                    "--postprocessor-args", "ffmpeg:-ar 16000 -ac 1",
                    "--no-playlist",
                    "-o", str(output_path.with_suffix(".%(ext)s")),
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            raise YouTubeFetchError(
                f"yt-dlp audio download failed for {url!r}: {exc}"
            ) from exc

        wav_path = output_path.with_suffix(".wav")
        if not wav_path.exists():
            raise YouTubeFetchError(
                f"yt-dlp did not produce audio file at {wav_path}"
            )
        return wav_path


def _default_transcriber(audio_path: Path) -> CaptionResult:
    """Transcribe via the shared transcription module."""
    from commonplace_worker.transcription import transcribe

    result = transcribe(audio_path, model_size="medium", language="en")
    return CaptionResult(text=result.text, source="whisper")


def _default_summarizer(text: str, title: str, url: str) -> dict[str, Any] | None:
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
        "source_kind": "youtube",
        "title": title,
        "url": url,
        "text": text,
    })

    skill_path = Path(__file__).resolve().parent.parent.parent / "skills" / "summarize_capture" / "SKILL.md"

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
        logger.warning("summarize_capture exited %d: %s", result.returncode, result.stderr[:200])
        return None

    try:
        summary: CaptureSummary = parse(result.stdout)
    except Exception:
        logger.warning("summarize_capture output parse failed", exc_info=True)
        return None

    # Check for fabricated quotes
    bad_quotes = verify_quotes(summary, text)
    if bad_quotes:
        logger.warning("summarize_capture fabricated %d quotes, dropping them", len(bad_quotes))
        summary.quotes = [q for q in summary.quotes if q not in bad_quotes]

    return {
        "description": summary.description,
        "key_points": summary.key_points,
        "quotes": summary.quotes,
    }


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------

# Regex for extracting video ID from various YouTube URL forms
_YT_PATTERNS = [
    # Standard watch URL
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/watch\?.*v=([a-zA-Z0-9_-]{11})"),
    # Short URL
    re.compile(r"(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})"),
    # Shorts URL
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})"),
    # Embed URL
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})"),
]


def _extract_video_id(url: str) -> str:
    """Extract the 11-character video ID from a YouTube URL.

    Raises YouTubeFetchError if the URL is not a recognized YouTube format.
    """
    for pat in _YT_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)

    # Try parsing query string for 'v' parameter
    parsed = urlparse(url)
    if parsed.hostname and "youtube" in parsed.hostname:
        qs = parse_qs(parsed.query)
        v = qs.get("v", [None])
        if v and v[0] and len(v[0]) == 11:
            return v[0]

    raise YouTubeFetchError(f"not a recognized YouTube URL: {url!r}")


def _canonical_url(video_id: str) -> str:
    """Return the canonical YouTube URL for a video ID."""
    return f"https://www.youtube.com/watch?v={video_id}"


# ---------------------------------------------------------------------------
# Caption quality heuristic
# ---------------------------------------------------------------------------


def _clean_vtt(vtt_text: str) -> str:
    """Strip VTT headers, timestamps, and tags from caption text."""
    lines = vtt_text.splitlines()
    text_lines: list[str] = []
    for line in lines:
        line = line.strip()
        # Skip WEBVTT header, blank lines, timestamp lines
        if not line:
            continue
        if line.startswith("WEBVTT"):
            continue
        if line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if line.startswith("NOTE"):
            continue
        # Skip lines that look like cue IDs (just a number)
        if re.match(r"^\d+$", line):
            continue
        # Skip timestamp lines: 00:00:01.000 --> 00:00:04.000
        if re.match(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->", line):
            continue
        # Strip VTT formatting tags
        cleaned = re.sub(r"<[^>]+>", "", line)
        if cleaned.strip():
            text_lines.append(cleaned.strip())

    # Deduplicate consecutive identical lines (VTT often repeats)
    deduped: list[str] = []
    for line in text_lines:
        if not deduped or line != deduped[-1]:
            deduped.append(line)

    return " ".join(deduped)


def _caption_quality_ok(text: str, duration_s: float = 0.0) -> bool:
    """Assess whether auto-generated captions are usable.

    Heuristics:
    - Has some punctuation (manual captions have periods, commas, etc.)
    - Not excessively repetitive
    - Reasonable word count relative to duration (if known)
    """
    if not text or len(text.strip()) < 50:
        return False

    words = text.split()
    word_count = len(words)

    # Check for minimal punctuation (at least 1 period or question mark per 200 words)
    punct_count = sum(1 for c in text if c in ".?!,;:")
    if word_count > 0 and punct_count / word_count < 0.01:
        return False

    # Check for excessive repetition: if any 3-gram appears more than 10% of the time
    if word_count >= 30:
        trigrams: dict[str, int] = {}
        for i in range(len(words) - 2):
            tri = " ".join(words[i:i + 3]).lower()
            trigrams[tri] = trigrams.get(tri, 0) + 1
        max_freq = max(trigrams.values()) if trigrams else 0
        total_trigrams = len(words) - 2
        if total_trigrams > 0 and max_freq / total_trigrams > 0.1:
            return False

    # If duration known, check word density (speech is ~120-180 wpm)
    if duration_s > 30:
        wpm = word_count / (duration_s / 60)
        if wpm < 30 or wpm > 400:
            return False

    return True


def _has_manual_captions(metadata: dict[str, Any], lang: str = "en") -> bool:
    """Check yt-dlp metadata to determine if manual captions exist."""
    subtitles = metadata.get("subtitles") or {}
    return lang in subtitles


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
    video_id: str,
    canonical_url: str,
    title: str,
    channel: str,
    upload_date: str | None,
    duration_s: float,
    caption_source: str,
    transcript_text: str,
    transcript_words: int,
    summarized: bool,
    summary: dict[str, Any] | None,
    fetched_at: datetime,
) -> Path:
    """Atomically write the transcript as a markdown file and return its path.

    Layout: ``<vault>/captures/YYYY/MM/<UTC-timestamp>-youtube-<video-id>.md``.
    """
    vault_root = _vault_root()
    year = fetched_at.strftime("%Y")
    month = fetched_at.strftime("%m")
    out_dir = vault_root / "captures" / year / month
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = fetched_at.strftime("%Y-%m-%dT%H%M%SZ")
    filename = f"{ts}-youtube-{video_id}.md"
    final_path = out_dir / filename
    tmp_path = out_dir / f"{filename}.tmp"

    content = _render_markdown(
        canonical_url=canonical_url,
        title=title,
        channel=channel,
        upload_date=upload_date,
        duration_s=duration_s,
        caption_source=caption_source,
        transcript_text=transcript_text,
        transcript_words=transcript_words,
        summarized=summarized,
        summary=summary,
        fetched_at=fetched_at,
    )

    with tmp_path.open("w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    tmp_path.rename(final_path)
    return final_path


def _render_markdown(
    *,
    canonical_url: str,
    title: str,
    channel: str,
    upload_date: str | None,
    duration_s: float,
    caption_source: str,
    transcript_text: str,
    transcript_words: int,
    summarized: bool,
    summary: dict[str, Any] | None,
    fetched_at: datetime,
) -> str:
    """Return the full frontmatter + body string."""
    lines: list[str] = ["---", "source: youtube"]
    lines.append(f"url: {_yaml_escape(canonical_url)}")
    lines.append(f"title: {_yaml_escape(title)}")
    lines.append(f"channel: {_yaml_escape(channel)}")
    if upload_date:
        # Convert YYYYMMDD to YYYY-MM-DD
        if len(upload_date) == 8 and upload_date.isdigit():
            formatted = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
            lines.append(f"uploaded_at: {_yaml_escape(formatted)}")
        else:
            lines.append(f"uploaded_at: {_yaml_escape(upload_date)}")
    lines.append(f"duration_s: {duration_s:.0f}")
    lines.append(f"caption_source: {caption_source}")
    lines.append(f"transcript_words: {transcript_words}")
    lines.append(f"summarized: {'true' if summarized else 'false'}")
    lines.append(f"fetched_at: {_yaml_escape(fetched_at.strftime('%Y-%m-%dT%H:%M:%SZ'))}")
    lines.append("---")
    lines.append("")

    if summarized and summary:
        lines.append("## Summary")
        lines.append("")
        lines.append(summary.get("description", ""))
        lines.append("")
        if summary.get("key_points"):
            lines.append("## Key points")
            lines.append("")
            for point in summary["key_points"]:
                lines.append(f"- {point}")
            lines.append("")
        if summary.get("quotes"):
            lines.append("## Quotes")
            lines.append("")
            for quote in summary["quotes"]:
                lines.append(f"> {quote}")
            lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Full transcript")
        lines.append("")

    lines.append(transcript_text.rstrip())
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_youtube_ingest(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _downloader: Any | None = None,
    _transcriber: Any | None = None,
    _summarizer: Any | None = None,
    _embedder: Any | None = None,
) -> dict[str, Any]:
    """Worker handler for ``ingest_youtube`` jobs.

    Parameters
    ----------
    payload:
        ``{"url": str, "inbox_file": str | None}``
    conn:
        Open SQLite connection with migrations applied.
    _downloader:
        Override for yt-dlp calls (testing).  Must have ``get_metadata``,
        ``get_captions``, ``download_audio`` methods.
    _transcriber:
        Override for Whisper transcription (testing).  Callable taking
        ``audio_path: Path`` and returning ``CaptionResult``.
    _summarizer:
        Override for summarize_capture skill (testing).  Callable taking
        ``(text, title, url)`` and returning ``dict | None``.
    _embedder:
        Optional embedder override forwarded to ``pipeline.embed_document``.

    Returns
    -------
    dict with keys: ``document_id``, ``chunk_count``, ``elapsed_ms``,
    ``url``, ``title``, ``caption_source``, ``transcript_words``,
    ``summarized``.
    """
    t0 = time.monotonic()

    url_raw = payload.get("url")
    if not isinstance(url_raw, str) or not url_raw.strip():
        raise ValueError(f"ingest_youtube payload missing 'url': {payload!r}")

    # Normalize URL
    video_id = _extract_video_id(url_raw.strip())
    canonical = _canonical_url(video_id)

    # Idempotency check by (content_type, source_id)
    existing = conn.execute(
        "SELECT id FROM documents WHERE content_type = 'youtube' AND source_id = ?",
        (canonical,),
    ).fetchone()

    if existing is not None:
        existing_id = int(existing["id"])
        chunk_count_row = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (existing_id,)
        ).fetchone()
        chunk_count = int(chunk_count_row[0]) if chunk_count_row else 0
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "youtube already ingested document_id=%d url=%s", existing_id, canonical
        )
        # Fetch stored metadata for the return value
        doc_row = conn.execute(
            "SELECT title FROM documents WHERE id = ?", (existing_id,)
        ).fetchone()
        return {
            "document_id": existing_id,
            "chunk_count": chunk_count,
            "elapsed_ms": elapsed_ms,
            "url": canonical,
            "title": doc_row["title"] if doc_row else "",
            "caption_source": "unknown",
            "transcript_words": 0,
            "summarized": False,
        }

    # Set up callables
    downloader = _downloader if _downloader is not None else _DefaultDownloader()
    transcriber = _transcriber if _transcriber is not None else _default_transcriber
    summarizer = _summarizer if _summarizer is not None else _default_summarizer

    # 1. Get video metadata
    meta_raw = downloader.get_metadata(canonical)
    meta = VideoMetadata(
        video_id=video_id,
        title=meta_raw.get("title", "Untitled"),
        channel=meta_raw.get("channel", meta_raw.get("uploader", "Unknown")),
        upload_date=meta_raw.get("upload_date"),
        duration_s=float(meta_raw.get("duration", 0)),
    )

    # 2. Try to get captions
    caption_result: CaptionResult | None = None
    has_manual = _has_manual_captions(meta_raw)

    manual_text, auto_text = downloader.get_captions(canonical)

    if has_manual and manual_text and manual_text.strip():
        caption_result = CaptionResult(text=manual_text.strip(), source="manual")
    elif (
        manual_text
        and manual_text.strip()
        and _caption_quality_ok(manual_text, meta.duration_s)
    ):
        # Got a caption but metadata says no manual — treat as auto
        caption_result = CaptionResult(text=manual_text.strip(), source="auto")
    if (
        caption_result is None
        and auto_text
        and auto_text.strip()
        and _caption_quality_ok(auto_text, meta.duration_s)
    ):
        caption_result = CaptionResult(text=auto_text.strip(), source="auto")

    # 3. Whisper fallback if no usable captions
    if caption_result is None:
        logger.info("no usable captions for %s, falling back to Whisper", canonical)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_base = Path(tmpdir) / "audio"
                audio_path = downloader.download_audio(canonical, audio_base)
                try:
                    caption_result = transcriber(audio_path)
                finally:
                    # Clean up audio file
                    if audio_path.exists():
                        audio_path.unlink()
        except Exception as exc:
            raise YouTubeTranscriptionError(
                f"Whisper fallback failed for {canonical}: {exc}"
            ) from exc

    if caption_result is None or not caption_result.text.strip():
        raise YouTubeTranscriptionError(
            f"could not obtain any transcript for {canonical}"
        )

    transcript_text = caption_result.text
    caption_source = caption_result.source
    transcript_words = len(transcript_text.split())

    # 4. Summarization (for long transcripts)
    summary_result = summarizer(transcript_text, meta.title, canonical)
    summarized = summary_result is not None

    # 5. Compute content hash
    content_hash = hashlib.sha256(transcript_text.encode("utf-8")).hexdigest()

    # Also check by content_hash for idempotency
    existing_hash = conn.execute(
        "SELECT id FROM documents WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    if existing_hash is not None:
        existing_id = int(existing_hash["id"])
        chunk_count_row = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (existing_id,)
        ).fetchone()
        chunk_count = int(chunk_count_row[0]) if chunk_count_row else 0
        elapsed_ms = (time.monotonic() - t0) * 1000
        return {
            "document_id": existing_id,
            "chunk_count": chunk_count,
            "elapsed_ms": elapsed_ms,
            "url": canonical,
            "title": meta.title,
            "caption_source": caption_source,
            "transcript_words": transcript_words,
            "summarized": summarized,
        }

    # 6. Write vault file
    fetched_at = datetime.now(UTC)
    vault_path = _write_vault_file(
        video_id=video_id,
        canonical_url=canonical,
        title=meta.title,
        channel=meta.channel,
        upload_date=meta.upload_date,
        duration_s=meta.duration_s,
        caption_source=caption_source,
        transcript_text=transcript_text,
        transcript_words=transcript_words,
        summarized=summarized,
        summary=summary_result,
        fetched_at=fetched_at,
    )

    # 7. Insert documents row
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO documents
                (content_type, source_uri, title, author, content_hash,
                 raw_path, source_id, status)
            VALUES ('youtube', ?, ?, ?, ?, ?, ?, 'ingesting')
            """,
            (
                canonical,
                meta.title,
                meta.channel,
                content_hash,
                str(vault_path),
                canonical,
            ),
        )
    document_id: int = cursor.lastrowid  # type: ignore[assignment]

    # 8. Embed — use summary text if summarized, otherwise full transcript
    from commonplace_server.pipeline import embed_document

    embed_text = transcript_text
    if summarized and summary_result:
        # Build a search-friendly text from the summary
        parts = [summary_result.get("description", "")]
        for kp in summary_result.get("key_points", []):
            parts.append(kp)
        for q in summary_result.get("quotes", []):
            parts.append(q)
        embed_text = "\n\n".join(parts)

    embed_kwargs: dict[str, Any] = {}
    if _embedder is not None:
        embed_kwargs["_embedder"] = _embedder
    result = embed_document(document_id, embed_text, conn, **embed_kwargs)

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "ingested youtube document_id=%d chunks=%d url=%s caption_source=%s elapsed_ms=%.0f",
        document_id,
        result.chunk_count,
        canonical,
        caption_source,
        elapsed_ms,
    )
    return {
        "document_id": document_id,
        "chunk_count": result.chunk_count,
        "elapsed_ms": elapsed_ms,
        "url": canonical,
        "title": meta.title,
        "caption_source": caption_source,
        "transcript_words": transcript_words,
        "summarized": summarized,
    }
