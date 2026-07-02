"""Tests for commonplace_worker.claude_skill — subprocess wrapper for `claude -p`."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from commonplace_worker.claude_skill import (
    SkillFailure,
    SkillTimeout,
    resolve_claude_binary,
    run_skill,
    run_skill_with_parse_retry,
)

# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def test_resolve_binary_prefers_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONPLACE_CLAUDE_BIN", "/opt/custom/claude")
    assert resolve_claude_binary() == "/opt/custom/claude"


def test_resolve_binary_falls_back_to_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMMONPLACE_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(
        "commonplace_worker.claude_skill.shutil.which",
        lambda name: "/usr/local/bin/claude",
    )
    assert resolve_claude_binary() == "/usr/local/bin/claude"


def test_resolve_binary_fallback_to_local_bin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COMMONPLACE_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(
        "commonplace_worker.claude_skill.shutil.which", lambda name: None
    )
    expected = str(Path.home() / ".local" / "bin" / "claude")
    assert resolve_claude_binary() == expected


# ---------------------------------------------------------------------------
# run_skill — error classification
# ---------------------------------------------------------------------------


def test_run_skill_raises_filenotfound_when_skill_md_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="skill file not found"):
        run_skill(skill_md=tmp_path / "nope.md", payload="{}")


def test_run_skill_timeout_raises_skill_timeout(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("# skill")
    with patch(
        "commonplace_worker.claude_skill.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=1),
    ), pytest.raises(SkillTimeout, match="exceeded"):
        run_skill(skill_md=skill, payload="{}", timeout_s=1)


def test_run_skill_missing_binary_raises_skill_timeout(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("# skill")
    with patch(
        "commonplace_worker.claude_skill.subprocess.run",
        side_effect=FileNotFoundError("no such file"),
    ), pytest.raises(SkillTimeout, match="claude binary not found"):
        run_skill(skill_md=skill, payload="{}")


def test_run_skill_nonzero_exit_raises_skill_failure(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("# skill")
    result = MagicMock(returncode=2, stdout="", stderr="boom")
    with patch(
        "commonplace_worker.claude_skill.subprocess.run", return_value=result
    ), pytest.raises(SkillFailure, match="exit 2"):
        run_skill(skill_md=skill, payload="{}")


def test_run_skill_empty_stdout_raises_skill_failure(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("# skill")
    result = MagicMock(returncode=0, stdout="   \n", stderr="")
    with patch(
        "commonplace_worker.claude_skill.subprocess.run", return_value=result
    ), pytest.raises(SkillFailure, match="empty stdout"):
        run_skill(skill_md=skill, payload="{}")


def test_run_skill_happy_path_returns_skill_result(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("# skill")
    result = MagicMock(returncode=0, stdout="ok\n", stderr="log")
    with patch(
        "commonplace_worker.claude_skill.subprocess.run", return_value=result
    ) as run_mock:
        out = run_skill(skill_md=skill, payload="payload", model="haiku", timeout_s=60)
    assert out.stdout == "ok\n"
    assert out.stderr == "log"
    assert out.returncode == 0
    # Confirm the command constructed correctly.
    (cmd,), kwargs = run_mock.call_args
    assert "--system-prompt-file" in cmd
    assert str(skill) in cmd
    assert "--model" in cmd
    assert "haiku" in cmd
    assert "payload" in cmd
    assert kwargs["timeout"] == 60


# ---------------------------------------------------------------------------
# run_skill_with_parse_retry
# ---------------------------------------------------------------------------


def test_parse_retry_succeeds_on_first_attempt(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("# skill")
    result = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")
    with patch(
        "commonplace_worker.claude_skill.subprocess.run", return_value=result
    ) as run_mock:
        final, parsed = run_skill_with_parse_retry(
            parse=lambda s: {"value": s.strip()},
            skill_md=skill,
            payload="",
        )
    assert parsed == {"value": '{"ok": true}'}
    assert final.stdout == '{"ok": true}'
    assert run_mock.call_count == 1


def test_parse_retry_recovers_on_second_attempt(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("# skill")
    # First parse raises; second succeeds.
    call_count = {"n": 0}

    def parse(s: str) -> int:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ValueError("bad parse")
        return 42

    result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch(
        "commonplace_worker.claude_skill.subprocess.run", return_value=result
    ) as run_mock:
        final, parsed = run_skill_with_parse_retry(
            parse=parse, skill_md=skill, payload=""
        )
    assert parsed == 42
    assert run_mock.call_count == 2
    assert final.stdout == "ok"


def test_parse_retry_propagates_when_both_parses_fail(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("# skill")

    def parse(_s: str) -> int:
        raise ValueError("still bad")

    result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch(
        "commonplace_worker.claude_skill.subprocess.run", return_value=result
    ), pytest.raises(ValueError, match="still bad"):
        run_skill_with_parse_retry(parse=parse, skill_md=skill, payload="")
