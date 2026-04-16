"""Profile regeneration handler.

handle_profile_regen(payload, conn) is the worker handler for 'regenerate_profile' jobs.

Steps
-----
1. Read ~/commonplace/profile/perennials.md (required).
2. Read ~/commonplace/profile/current.md if it exists (empty string on cold start).
3. Read ~/commonplace/profile/inbox/*.md inbox additions.
4. Sample corpus signal from the DB (recent highlights, captures, Bluesky, books).
5. Build the JSON payload for skills/regenerate_profile/SKILL.md.
6. Invoke claude -p with the skill via subprocess (10-minute timeout).
7. Validate output via skills/regenerate_profile/parser.py.
8. Snapshot the old current.md to profile/history/ (if it existed).
9. Atomically write new current.md.
10. Archive processed inbox files to inbox/processed/.

Profile directory is controlled by COMMONPLACE_PROFILE_DIR env var
(default ~/commonplace/profile/).

Repo root is controlled by COMMONPLACE_REPO_ROOT env var
(default ~/code/commonplace-code/).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

SNIPPET_MAX_CHARS = 300
CLAUDE_BINARY = os.environ.get("COMMONPLACE_CLAUDE_BIN", "/Users/cameronlewis/.local/bin/claude")
CLAUDE_TIMEOUT_SECONDS = 600  # 10 minutes


def _profile_dir() -> Path:
    return Path(
        os.environ.get("COMMONPLACE_PROFILE_DIR", "~/commonplace/profile/")
    ).expanduser()


def _repo_root() -> Path:
    return Path(
        os.environ.get("COMMONPLACE_REPO_ROOT", "~/code/commonplace-code/")
    ).expanduser()


# ---------------------------------------------------------------------------
# Parser loader (avoids name collision with other skills' parser.py files)
# ---------------------------------------------------------------------------


def _load_parser() -> types.ModuleType:
    """Load skills/regenerate_profile/parser.py without importing it as 'parser'."""
    parser_path = _repo_root() / "skills" / "regenerate_profile" / "parser.py"
    spec = importlib.util.spec_from_file_location(
        "regenerate_profile_parser", str(parser_path)
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load parser from {parser_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Profile-dir I/O helpers (isolated for testability)
# ---------------------------------------------------------------------------


def read_perennials(profile_dir: Path) -> str:
    """Read perennials.md. Raises FileNotFoundError if missing (required)."""
    path = profile_dir / "perennials.md"
    if not path.exists():
        raise FileNotFoundError(
            f"perennials.md not found at {path} — this file is required for profile regen. "
            "Create it at ~/commonplace/profile/perennials.md before running."
        )
    return path.read_text(encoding="utf-8")


def read_current_profile(profile_dir: Path) -> str:
    """Read current.md if it exists; return empty string on cold start."""
    path = profile_dir / "current.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def read_inbox_additions(profile_dir: Path) -> list[dict[str, str]]:
    """Read *.md files from profile/inbox/ and return list of {timestamp, content} dicts.

    Each inbox file is expected to have a YAML frontmatter line::

        timestamp: ISO8601

    followed by the addition text. If the frontmatter is missing, the full
    file content is used as the addition body and the file's mtime is used
    as a fallback timestamp.
    """
    inbox_dir = profile_dir / "inbox"
    if not inbox_dir.exists():
        return []

    additions: list[dict[str, str]] = []
    _ts_re = re.compile(r"^timestamp:\s*(.+?)\s*$", re.MULTILINE)
    _fm_re = re.compile(r"^---\s*\n(.*?\n)?---\s*\n", re.DOTALL)

    for md_file in sorted(inbox_dir.glob("*.md")):
        raw = md_file.read_text(encoding="utf-8")
        ts_match = _ts_re.search(raw)
        if ts_match:
            timestamp = ts_match.group(1)
        else:
            # Fall back to file mtime
            mtime = md_file.stat().st_mtime
            timestamp = datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Strip YAML frontmatter from content
        body = _fm_re.sub("", raw, count=1).strip()
        if not body:
            body = raw.strip()

        additions.append({"timestamp": timestamp, "content": body})

    return additions


# ---------------------------------------------------------------------------
# Corpus sampling helpers (isolated for testability)
# ---------------------------------------------------------------------------


def _snippet(text: str) -> str:
    """Return the first SNIPPET_MAX_CHARS characters of text."""
    return text[:SNIPPET_MAX_CHARS]


def sample_recent_highlights(conn: sqlite3.Connection, limit: int = 20) -> list[str]:
    """Sample recent Kindle highlights (content_type='kindle').

    Uses the first chunk (chunk_index=0) as the representative snippet.
    Falls back to kindle_highlight if kindle not present.
    """
    rows = conn.execute(
        """
        SELECT c.text
        FROM documents d
        JOIN chunks c ON c.document_id = d.id AND c.chunk_index = 0
        WHERE d.content_type IN ('kindle', 'kindle_highlight')
        ORDER BY d.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_snippet(row["text"]) for row in rows]


def sample_recent_captures(conn: sqlite3.Connection, limit: int = 10) -> list[str]:
    """Sample recent article/youtube/podcast/image/video captures."""
    rows = conn.execute(
        """
        SELECT c.text
        FROM documents d
        JOIN chunks c ON c.document_id = d.id AND c.chunk_index = 0
        WHERE d.content_type IN ('article', 'youtube', 'podcast', 'image', 'video')
        ORDER BY d.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_snippet(row["text"]) for row in rows]


def sample_recent_bluesky(conn: sqlite3.Connection, limit: int = 15) -> list[str]:
    """Sample recent Bluesky posts."""
    rows = conn.execute(
        """
        SELECT c.text
        FROM documents d
        JOIN chunks c ON c.document_id = d.id AND c.chunk_index = 0
        WHERE d.content_type = 'bluesky'
        ORDER BY d.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_snippet(row["text"]) for row in rows]


def sample_books_engaged(conn: sqlite3.Connection, limit: int = 10) -> list[str]:
    """Sample titles the user has been engaged with in the last 90 days."""
    rows = conn.execute(
        """
        SELECT DISTINCT COALESCE(d.title, d.source_uri) AS label
        FROM documents d
        WHERE d.content_type IN ('book', 'audiobook')
          AND d.created_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-90 days')
          AND (d.title IS NOT NULL OR d.source_uri IS NOT NULL)
        ORDER BY d.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [str(row["label"]) for row in rows]


def build_corpus_sample(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Build the corpus_sample dict from the live DB."""
    return {
        "recent_highlights": sample_recent_highlights(conn),
        "recent_captures": sample_recent_captures(conn),
        "recent_bluesky": sample_recent_bluesky(conn),
        "books_engaged": sample_books_engaged(conn),
    }


# ---------------------------------------------------------------------------
# Claude invocation
# ---------------------------------------------------------------------------


def invoke_skill(json_payload: str, *, repo_root: Path) -> str:
    """Invoke the regenerate_profile skill via claude -p.

    Returns the skill's stdout as a string.
    Raises RuntimeError if the subprocess fails.
    """
    skill_md = repo_root / "skills" / "regenerate_profile" / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md not found at {skill_md}")

    result = subprocess.run(  # noqa: S603
        [
            CLAUDE_BINARY,
            "-p",
            "--system-prompt-file",
            str(skill_md),
            "--model",
            "opus",
            json_payload,
        ],
        capture_output=True,
        text=True,
        timeout=CLAUDE_TIMEOUT_SECONDS,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"claude -p returned exit code {result.returncode}. "
            f"stderr: {result.stderr[:500]}"
        )

    output = result.stdout
    if not output or not output.strip():
        raise RuntimeError("claude -p returned empty output")

    return output


# ---------------------------------------------------------------------------
# Snapshot + atomic write helpers
# ---------------------------------------------------------------------------


def snapshot_current_profile(profile_dir: Path, now: datetime) -> Path | None:
    """Copy current.md to history/current-YYYY-MM-DDTHH-MM-SSZ.md.

    Returns the snapshot path, or None if current.md doesn't exist.
    """
    current = profile_dir / "current.md"
    if not current.exists():
        return None

    history_dir = profile_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    ts = now.strftime("%Y-%m-%dT%H-%M-%SZ")
    snapshot_path = history_dir / f"current-{ts}.md"
    shutil.copy2(str(current), str(snapshot_path))
    logger.info("snapshotted profile to %s", snapshot_path)
    return snapshot_path


def atomic_write(path: Path, content: str) -> None:
    """Write content to path via tmp + fsync + rename (atomic)."""
    tmp_path = path.with_suffix(".md.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    tmp_path.rename(path)
    logger.info("wrote new profile to %s", path)


def archive_inbox_files(profile_dir: Path, inbox_dir: Path) -> int:
    """Move all *.md files from inbox_dir to inbox_dir/processed/. Returns count moved."""
    processed_dir = inbox_dir / "processed"
    count = 0
    for md_file in sorted(inbox_dir.glob("*.md")):
        processed_dir.mkdir(parents=True, exist_ok=True)
        dest = processed_dir / md_file.name
        md_file.rename(dest)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_profile_regen(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    *,
    _invoke_skill: Any = None,
    _parser_module: Any = None,
) -> dict[str, Any]:
    """Worker handler for 'regenerate_profile' jobs.

    Parameters
    ----------
    payload:
        ``{}`` — no parameters; everything is discovered from the vault.
    conn:
        Open SQLite connection with migrations applied.
    _invoke_skill:
        Test seam — replace with a callable(json_str) -> str mock.
    _parser_module:
        Test seam — replace with a mock module that has parse() and
        verify_directives_preserved().

    Returns
    -------
    dict with keys: status, snapshot_path, new_profile_path, inbox_processed.
    """
    now = datetime.now(UTC)
    profile_dir = _profile_dir()
    repo_root = _repo_root()

    # 1. Read perennials (required)
    perennials = read_perennials(profile_dir)

    # 2. Read current profile (empty on cold start)
    current_profile = read_current_profile(profile_dir)

    # 3. Read inbox additions
    inbox_dir = profile_dir / "inbox"
    inbox_additions = read_inbox_additions(profile_dir)
    logger.info(
        "profile regen: current_profile=%d chars, inbox_additions=%d",
        len(current_profile),
        len(inbox_additions),
    )

    # 4. Sample corpus signal
    corpus_sample = build_corpus_sample(conn)
    logger.info(
        "corpus_sample: highlights=%d captures=%d bluesky=%d books=%d",
        len(corpus_sample["recent_highlights"]),
        len(corpus_sample["recent_captures"]),
        len(corpus_sample["recent_bluesky"]),
        len(corpus_sample["books_engaged"]),
    )

    # 5. Build JSON payload for skill
    skill_input: dict[str, Any] = {
        "current_profile": current_profile,
        "perennials": perennials,
        "inbox_additions": inbox_additions,
        "corpus_sample": corpus_sample,
    }
    json_payload = json.dumps(skill_input, ensure_ascii=False)

    # 6. Invoke skill
    invoke_fn = _invoke_skill if _invoke_skill is not None else (
        lambda jp: invoke_skill(jp, repo_root=repo_root)
    )
    raw_output = invoke_fn(json_payload)

    # 7. Validate output via parser
    parser = _parser_module if _parser_module is not None else _load_parser()
    try:
        parser.parse(raw_output)
    except Exception as exc:
        raise RuntimeError(
            f"Profile regen output failed validation — NOT writing to disk. "
            f"Previous profile is intact. Parser error: {exc}"
        ) from exc

    # Directive preservation check
    missing = parser.verify_directives_preserved(current_profile, raw_output)
    if missing:
        raise RuntimeError(
            f"Profile regen dropped {len(missing)} directive(s) — NOT writing to disk. "
            f"Missing: {missing!r}"
        )

    # 8. Snapshot old profile
    snapshot_path = snapshot_current_profile(profile_dir, now)

    # 9. Atomic write new profile
    profile_dir.mkdir(parents=True, exist_ok=True)
    current_path = profile_dir / "current.md"
    atomic_write(current_path, raw_output)

    # 10. Archive inbox files
    inbox_processed = 0
    if inbox_dir.exists():
        inbox_processed = archive_inbox_files(profile_dir, inbox_dir)

    logger.info(
        "profile regen complete: snapshot=%s new_profile=%s inbox_processed=%d",
        snapshot_path,
        current_path,
        inbox_processed,
    )

    return {
        "status": "complete",
        "snapshot_path": str(snapshot_path) if snapshot_path else None,
        "new_profile_path": str(current_path),
        "inbox_processed": inbox_processed,
    }
