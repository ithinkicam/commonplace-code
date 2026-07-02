"""Wave 4.15 regression tests for ``surface._invoke_judge`` parse-retry-once.

Across Wave 4.14's three 33-min replays, 1–3 judge subprocess invocations per
30-seed run returned unparseable stdout, silently producing empty accepts.
Wave 4.15 adds a retry-once wrapper inside ``_invoke_judge``: on parse failure,
rerun the subprocess once with the same input; log the stdout tail on both
failures; gracefully degrade to the ``'judge output unparseable'`` sentinel
only after two consecutive failures.

These tests exercise the wrapper through ``subprocess.run`` monkeypatching
(via the shared ``claude_cli_recorder`` fixture in ``conftest.py``) so they
stay hermetic and fast.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from commonplace_server import surface as surface_mod

VALID_JUDGE_STDOUT = (
    '{"accepted": [{"id": "c1", "reason": "ok"}], '
    '"rejected": [], "triangulation_groups": []}'
)
UNPARSEABLE_STDOUT_A = "not json — claude preamble drift"
UNPARSEABLE_STDOUT_B = "still not json — second malformed response"


class TestInvokeJudgeParseRetry:
    def test_valid_first_attempt_does_not_retry(
        self, claude_cli_recorder: object
    ) -> None:
        """Happy path: parse succeeds on attempt 1, subprocess called once."""
        recorder = claude_cli_recorder
        recorder.set_response(VALID_JUDGE_STDOUT)  # type: ignore[attr-defined]

        raw, judgment, err = surface_mod._invoke_judge('{"seed":"x"}')

        assert err is None
        assert raw == VALID_JUDGE_STDOUT
        assert judgment is not None
        assert [a.id for a in judgment.accepted] == ["c1"]
        assert len(recorder.calls) == 1  # type: ignore[attr-defined]

    def test_unparseable_then_valid_recovers_on_retry(
        self, claude_cli_recorder: object
    ) -> None:
        """Attempt 1 unparseable, attempt 2 parses — retry saves the call."""
        recorder = claude_cli_recorder
        recorder.set_responses([UNPARSEABLE_STDOUT_A, VALID_JUDGE_STDOUT])  # type: ignore[attr-defined]

        raw, judgment, err = surface_mod._invoke_judge('{"seed":"x"}')

        assert err is None, "retry should have recovered"
        assert raw == VALID_JUDGE_STDOUT
        assert judgment is not None
        assert [a.id for a in judgment.accepted] == ["c1"]
        assert len(recorder.calls) == 2  # type: ignore[attr-defined]

    def test_two_unparseable_degrades_with_last_raw_preserved(
        self, claude_cli_recorder: object
    ) -> None:
        """Both attempts unparseable — return the sentinel note and the last raw."""
        recorder = claude_cli_recorder
        recorder.set_responses([UNPARSEABLE_STDOUT_A, UNPARSEABLE_STDOUT_B])  # type: ignore[attr-defined]

        raw, judgment, err = surface_mod._invoke_judge('{"seed":"x"}')

        assert err == "judge output unparseable"
        assert judgment is None
        assert raw == UNPARSEABLE_STDOUT_B
        assert len(recorder.calls) == 2  # type: ignore[attr-defined]

    def test_subprocess_timeout_short_circuits_without_retry(
        self, claude_cli_recorder: object
    ) -> None:
        """Subprocess-layer failures (timeout) return immediately without retrying.

        Rationale: parse retry is for malformed-stdout noise; a timeout likely
        reflects model or infra latency and retrying would just burn another
        JUDGE_TIMEOUT budget without improving the outcome.
        """
        recorder = claude_cli_recorder
        recorder.set_timeout()  # type: ignore[attr-defined]

        raw, judgment, err = surface_mod._invoke_judge('{"seed":"x"}')

        assert isinstance(err, surface_mod.JudgeFailure)
        assert err.kind == "timeout"
        assert err.message == "judge timed out after 60s"
        assert raw is None
        assert judgment is None
        assert len(recorder.calls) == 1  # type: ignore[attr-defined]

    def test_parse_failure_log_includes_stdout_tail(
        self,
        claude_cli_recorder: object,
        caplog: object,
    ) -> None:
        """Log tail must surface on every parse failure so operators can
        post-hoc diagnose what Haiku emitted. Check that the malformed
        stdout string appears somewhere in the warning logs."""
        import logging

        recorder = claude_cli_recorder
        recorder.set_responses([UNPARSEABLE_STDOUT_A, UNPARSEABLE_STDOUT_B])  # type: ignore[attr-defined]

        caplog.set_level(logging.WARNING, logger="commonplace_server.surface")  # type: ignore[attr-defined]

        surface_mod._invoke_judge('{"seed":"x"}')

        joined = "\n".join(r.getMessage() for r in caplog.records)  # type: ignore[attr-defined]
        assert UNPARSEABLE_STDOUT_A in joined
        assert UNPARSEABLE_STDOUT_B in joined
        assert "attempt 1/2" in joined
        assert "attempt 2/2" in joined

    def test_nonzero_exit_preserves_stdout_and_stderr_diagnostics(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["claude"],
            returncode=1,
            stdout="authentication failed on stdout",
            stderr="disk quota exceeded on stderr",
        )
        with patch("subprocess.run", return_value=completed):
            raw, judgment, err = surface_mod._invoke_judge('{"seed":"x"}')

        assert raw is None
        assert judgment is None
        assert isinstance(err, surface_mod.JudgeFailure)
        assert err.kind == "exit_nonzero"
        assert "exit_code=1" in (err.detail or "")
        assert "authentication failed" in (err.detail or "")
        assert "disk quota exceeded" in (err.detail or "")

    def test_pre_cancelled_judge_is_distinct_from_timeout(self) -> None:
        import threading

        cancel_event = threading.Event()
        cancel_event.set()

        raw, judgment, err = surface_mod._invoke_judge(
            '{"seed":"x"}',
            cancel_event,
        )

        assert raw is None
        assert judgment is None
        assert isinstance(err, surface_mod.JudgeFailure)
        assert err.kind == "cancelled"
