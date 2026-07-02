"""Frontmatter string helpers — YAML escaping and URL-safe slug generation.

Pure text transforms; no I/O. Extracted from handlers that reimplemented
the same two helpers five times each. Kept intentionally narrow: per-
handler frontmatter *assembly* (field ordering, conditional inclusion,
section separators) remains in the handlers because the field sets and
templates genuinely differ between article / podcast / youtube /
video / image outputs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def yaml_escape(value: str) -> str:
    """Return a double-quoted, escaped YAML scalar for ``value``.

    Sufficient for the single-line string values the handlers produce
    (titles, URLs, authors). Does not handle multi-line scalars; those
    shouldn't appear in our frontmatter in the first place.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_embed_header(fields: Iterable[tuple[str, str | None]]) -> str:
    """Render a short ``Label: value`` header for prepending to embed text.

    Given an ordered iterable of ``(label, value)`` pairs, returns a
    human-readable header followed by a blank line, skipping pairs whose
    value is ``None`` or empty/whitespace-only. Returns an empty string
    when every value is skipped so callers can unconditionally prepend
    it without introducing spurious blank lines.

    The handlers (youtube, podcast, video, article) prepend this header
    to the text they pass to ``pipeline.embed_document`` so title,
    channel, author, and URL land in chunk 0 and become discoverable via
    semantic search. Without this step, a query like "John Behr video"
    can only match if "Behr" appears verbatim in the transcript.
    """
    lines: list[str] = []
    for label, value in fields:
        if not value or not str(value).strip():
            continue
        lines.append(f"{label}: {str(value).strip()}")
    if not lines:
        return ""
    return "\n".join(lines) + "\n\n"


def slugify(text: str, *, max_len: int = 60, fallback: str = "capture") -> str:
    """Return a URL-safe slug: lowercase, hyphen-separated, trimmed.

    ``fallback`` is returned if the input produces an empty slug (e.g.
    an all-punctuation title). Each handler passes a handler-appropriate
    default (``"article"``, ``"podcast"``, ``"episode"``, ``"video"``)
    so the resulting filename hints at capture kind even when upstream
    metadata is missing.
    """
    lowered = text.lower().strip()
    slug = _SLUG_RE.sub("-", lowered).strip("-")
    slug = slug[:max_len].rstrip("-")
    return slug or fallback
