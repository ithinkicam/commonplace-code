"""Tests for commonplace_worker/transcription.py."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from commonplace_worker.transcription import (
    Segment,
    TranscriptionResult,
    _get_model,
    _model_cache,
    transcribe,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_model(segments: list[dict] | None = None, duration: float = 120.0):
    """Create a mock WhisperModel that returns controlled output."""
    if segments is None:
        segments = [
            {"start": 0.0, "end": 5.0, "text": "Hello world."},
            {"start": 5.0, "end": 10.0, "text": "This is a test."},
        ]

    mock_segments = []
    for s in segments:
        seg = SimpleNamespace(start=s["start"], end=s["end"], text=s["text"])
        mock_segments.append(seg)

    info = SimpleNamespace(duration=duration, language="en")
    model = MagicMock()
    model.transcribe.return_value = (iter(mock_segments), info)
    return model


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the model cache before each test."""
    _model_cache.clear()
    yield
    _model_cache.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTranscriptionResult:
    def test_shape(self):
        """TranscriptionResult has the expected fields."""
        result = TranscriptionResult(
            text="hello",
            segments=[Segment(start=0.0, end=1.0, text="hello")],
            language="en",
            duration_s=1.0,
        )
        assert result.text == "hello"
        assert len(result.segments) == 1
        assert result.segments[0].start == 0.0
        assert result.segments[0].end == 1.0
        assert result.language == "en"
        assert result.duration_s == 1.0

    def test_defaults(self):
        """TranscriptionResult defaults are sensible."""
        result = TranscriptionResult(text="hi")
        assert result.segments == []
        assert result.language == "en"
        assert result.duration_s == 0.0


class TestSegment:
    def test_segment_fields(self):
        seg = Segment(start=1.5, end=3.0, text="some words")
        assert seg.start == 1.5
        assert seg.end == 3.0
        assert seg.text == "some words"


class TestTranscribe:
    def test_mock_model_output(self):
        """Transcribe with a mock model produces expected output."""
        import commonplace_worker.transcription as mod

        mock_model = _make_mock_model()
        original = mod._model
        try:
            mod._model = mock_model
            result = transcribe(Path("/fake/audio.wav"))
            assert result.text == "Hello world. This is a test."
            assert len(result.segments) == 2
            assert result.segments[0].text == "Hello world."
            assert result.segments[1].text == "This is a test."
            assert result.language == "en"
            assert result.duration_s == 120.0
        finally:
            mod._model = original

    def test_empty_segments(self):
        """Transcribe handles empty segment list."""
        import commonplace_worker.transcription as mod

        mock_model = _make_mock_model(segments=[], duration=0.0)
        original = mod._model
        try:
            mod._model = mock_model
            result = transcribe(Path("/fake/audio.wav"))
            assert result.text == ""
            assert result.segments == []
            assert result.duration_s == 0.0
        finally:
            mod._model = original


class TestModelCaching:
    def test_same_size_returns_same_instance(self):
        """_get_model returns the same instance for the same model_size."""
        mock = MagicMock()
        _model_cache["medium"] = mock
        assert _get_model("medium") is mock

    def test_different_sizes_cached_separately(self):
        """Different model sizes get separate cache entries."""
        mock_medium = MagicMock()
        mock_small = MagicMock()
        _model_cache["medium"] = mock_medium
        _model_cache["small"] = mock_small
        assert _get_model("medium") is mock_medium
        assert _get_model("small") is mock_small
        assert _get_model("medium") is not _get_model("small")

    def test_override_model_takes_precedence(self):
        """When _model is set, it takes precedence over cache."""
        import commonplace_worker.transcription as mod

        mock_override = MagicMock()
        mock_cached = MagicMock()
        _model_cache["medium"] = mock_cached

        original = mod._model
        try:
            mod._model = mock_override
            assert _get_model("medium") is mock_override
        finally:
            mod._model = original
