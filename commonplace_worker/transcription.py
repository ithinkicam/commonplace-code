"""Shared transcription module wrapping faster-whisper.

Used by YouTube (3.4), podcast (3.5), and video (3.7) handlers.

Provides lazy model loading with a module-level cache so the model is
loaded once and reused across calls.  Tests can override ``_model`` to
avoid downloading the real model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Segment:
    """A single transcription segment with timestamps."""

    start: float
    end: float
    text: str


@dataclass
class TranscriptionResult:
    """Result of a transcription run."""

    text: str
    segments: list[Segment] = field(default_factory=list)
    language: str = "en"
    duration_s: float = 0.0


# ---------------------------------------------------------------------------
# Module-level model cache
# ---------------------------------------------------------------------------

# Keyed by model_size string → WhisperModel instance.
_model_cache: dict[str, Any] = {}

# Override point for tests.  When set to a non-None value, ``transcribe``
# uses this object instead of loading a real faster-whisper model.
# The mock should implement ``transcribe(str, language=..., beam_size=...)``
# returning ``(segments_iterable, info_object)``.
_model: Any = None


def _get_model(model_size: str = "medium") -> Any:
    """Return a cached WhisperModel, loading on first call."""
    if _model is not None:
        return _model

    if model_size in _model_cache:
        return _model_cache[model_size]

    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    logger.info("loading faster-whisper model '%s' (first use)", model_size)
    model = WhisperModel(model_size, device="auto", compute_type="int8")
    _model_cache[model_size] = model
    return model


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def transcribe(
    audio_path: Path,
    *,
    model_size: str = "medium",
    language: str = "en",
) -> TranscriptionResult:
    """Transcribe an audio file using faster-whisper.

    Parameters
    ----------
    audio_path:
        Path to the audio file (WAV, MP3, etc.).
    model_size:
        Whisper model size (``"medium"`` per v5 plan).
    language:
        Language code hint for transcription.

    Returns
    -------
    TranscriptionResult with full text, segments, detected language,
    and total duration in seconds.
    """
    model = _get_model(model_size)

    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
    )

    segments: list[Segment] = []
    text_parts: list[str] = []

    for seg in segments_iter:
        segments.append(Segment(start=seg.start, end=seg.end, text=seg.text.strip()))
        text_parts.append(seg.text.strip())

    full_text = " ".join(text_parts)
    duration = getattr(info, "duration", 0.0) or 0.0
    detected_lang = getattr(info, "language", language) or language

    return TranscriptionResult(
        text=full_text,
        segments=segments,
        language=detected_lang,
        duration_s=duration,
    )
