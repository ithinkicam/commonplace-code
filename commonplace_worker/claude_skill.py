"""Shared wrapper around ``claude -p <skill>`` subprocess invocations.

Centralises:

* Binary resolution (``$COMMONPLACE_CLAUDE_BIN`` → ``shutil.which`` →
  ``~/.local/bin/claude``), which otherwise every caller had to
  re-implement and some got wrong (``profile.py`` hardcoded the
  operator's home directory).
* Timeout + error classification. Callers see :class:`SkillTimeout` for
  timeouts / missing binary and :class:`SkillFailure` for non-zero exits
  instead of the raw ``subprocess.TimeoutExpired`` / ``FileNotFoundError``
  / ``CalledProcessError`` triplet each call site had to handle itself.
* Optional parse-retry. ``run_skill_with_parse_retry`` runs the skill,
  runs the caller-supplied parser, and — on parse failure only — invokes
  the skill one more time. This is the pattern ``surface.py`` already
  follows for judge invocations; other callers can opt in when they
  want symmetric semantics.

Deliberately *not* in scope:

* yt-dlp / ffmpeg / whisper subprocess calls — they share a shape with
  ``claude -p`` but have different retry semantics and different error
  types. Forcing them into a generic ``run_tool`` helper would be false
  economy.
* The skill parsers themselves — each skill owns its parser module; this
  wrapper only runs the subprocess and hands stdout back.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SkillTimeout(RuntimeError):
    """Claude subprocess exceeded its timeout or the binary was not found.

    Grouped with missing-binary because both manifest as "the skill did
    not run at all" from a caller's perspective — they want a retry
    signal, not a distinction.
    """


class SkillFailure(RuntimeError):
    """Claude subprocess exited non-zero."""


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def resolve_claude_binary() -> str:
    """Locate the claude CLI binary.

    Resolution order:
      1. ``$COMMONPLACE_CLAUDE_BIN`` (operator override).
      2. ``shutil.which('claude')`` (PATH lookup).
      3. ``~/.local/bin/claude`` (the Mac mini install location).

    Chosen order mirrors the existing ``profile._resolve_claude_binary``
    (now inlined here) so production behaviour is unchanged while tests
    and dev machines pick up whatever's on PATH first.
    """
    explicit = os.environ.get("COMMONPLACE_CLAUDE_BIN")
    if explicit:
        return explicit
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    return str(Path.home() / ".local" / "bin" / "claude")


# ---------------------------------------------------------------------------
# Core run helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillResult:
    """Successful-exit output of a skill invocation."""

    stdout: str
    stderr: str
    returncode: int


def run_skill(
    *,
    skill_md: Path,
    payload: str,
    model: str = "haiku",
    timeout_s: int = 120,
    claude_bin: str | None = None,
) -> SkillResult:
    """Invoke ``claude -p --system-prompt-file <skill> --model <m> <payload>``.

    Raises :class:`SkillTimeout` on timeout or missing binary,
    :class:`SkillFailure` on non-zero exit. Callers handle parse errors
    themselves (or use :func:`run_skill_with_parse_retry`).
    """
    if not skill_md.exists():
        raise FileNotFoundError(f"skill file not found: {skill_md}")

    cmd = [
        claude_bin or resolve_claude_binary(),
        "-p",
        "--system-prompt-file",
        str(skill_md),
        "--model",
        model,
        payload,
    ]

    try:
        completed = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise SkillTimeout(
            f"claude skill {skill_md.name} exceeded {timeout_s}s timeout"
        ) from exc
    except FileNotFoundError as exc:
        raise SkillTimeout(f"claude binary not found: {cmd[0]}") from exc

    if completed.returncode != 0:
        raise SkillFailure(
            f"claude skill {skill_md.name} exit {completed.returncode}; "
            f"stderr: {completed.stderr[:500]}"
        )

    if not completed.stdout or not completed.stdout.strip():
        raise SkillFailure(f"claude skill {skill_md.name} returned empty stdout")

    return SkillResult(
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
    )


def run_skill_with_parse_retry(
    *,
    parse: Callable[[str], T],
    skill_md: Path,
    payload: str,
    model: str = "haiku",
    timeout_s: int = 120,
) -> tuple[SkillResult, T]:
    """Run the skill, parse stdout, retry the skill once if parse fails.

    Mirrors the existing ``surface.py`` judge-retry pattern. Returns the
    final :class:`SkillResult` together with the parsed value. Raises
    whatever ``parse`` raised on the second attempt; the first-attempt
    parse exception is logged at ``info`` so intermittent flakes leave a
    trail without being alarming.
    """
    first = run_skill(
        skill_md=skill_md, payload=payload, model=model, timeout_s=timeout_s
    )
    try:
        return first, parse(first.stdout)
    except Exception as exc:
        logger.info(
            "skill %s output failed to parse (%s); retrying once",
            skill_md.name, exc,
        )

    second = run_skill(
        skill_md=skill_md, payload=payload, model=model, timeout_s=timeout_s
    )
    parsed = parse(second.stdout)  # propagates if retry also fails
    logger.info("skill %s recovered after parse retry", skill_md.name)
    return second, parsed
