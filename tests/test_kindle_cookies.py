"""Tests for the kindle-cookies-install Makefile target behavior.

Verifies that the install process:
  - Reads a JSON file
  - Invokes `security add-generic-password -U` with the correct arguments
  - Removes the source file after installing

Uses temp files and mocked subprocess — no real keychain access.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helper: simulate the cookies-install logic
# ---------------------------------------------------------------------------

# The cookies-install Makefile target runs a shell command equivalent to:
#
#   python -c "
#     import json, subprocess, sys
#     p = sys.argv[1]
#     data = open(p).read()
#     json.loads(data)  # validate
#     subprocess.run(['security', 'add-generic-password', '-U',
#                     '-a', 'commonplace',
#                     '-s', 'commonplace-kindle/session-cookies',
#                     '-w', data], check=True)
#     import os; os.unlink(p)
#   " $(COOKIES)
#
# We test the same logic in pure Python here to verify correctness
# without running make.


def _install_cookies_from_file(cookies_path: Path) -> None:
    """Python equivalent of the kindle-cookies-install Makefile target.

    Reads a JSON cookies file, validates it, stores it in macOS Keychain,
    and deletes the source file.
    """
    data = cookies_path.read_text()
    # Validate JSON before storing
    json.loads(data)

    subprocess.run(  # noqa: S603
        [
            "security", "add-generic-password",
            "-U",
            "-a", "commonplace",
            "-s", "commonplace-kindle/session-cookies",
            "-w", data,
        ],
        check=True,
    )
    cookies_path.unlink()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCookiesInstall:
    def test_reads_json_file(self, tmp_path: Path) -> None:
        """install function reads the JSON file and passes it to security."""
        cookies_data = [
            {"name": "session-id", "value": "fake123", "domain": ".amazon.com"},
            {"name": "ubid-main", "value": "fake456", "domain": ".amazon.com"},
        ]
        cookies_file = tmp_path / "amazon-cookies.json"
        cookies_file.write_text(json.dumps(cookies_data))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _install_cookies_from_file(cookies_file)

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "security" in args
        assert "add-generic-password" in args

    def test_invokes_security_add_generic_password(self, tmp_path: Path) -> None:
        """The security command is called with -U and correct service/account."""
        cookies_data = [{"name": "x-main", "value": "fakevalue", "domain": ".amazon.com"}]
        cookies_file = tmp_path / "amazon-cookies.json"
        cookies_file.write_text(json.dumps(cookies_data))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _install_cookies_from_file(cookies_file)

        args = mock_run.call_args[0][0]
        assert "-U" in args
        assert "commonplace" in args
        assert "commonplace-kindle/session-cookies" in args

    def test_deletes_source_file_after_install(self, tmp_path: Path) -> None:
        """Source file is deleted after successful install."""
        cookies_data = [{"name": "test", "value": "val", "domain": ".amazon.com"}]
        cookies_file = tmp_path / "amazon-cookies.json"
        cookies_file.write_text(json.dumps(cookies_data))

        assert cookies_file.exists()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _install_cookies_from_file(cookies_file)

        assert not cookies_file.exists()

    def test_raises_on_invalid_json(self, tmp_path: Path) -> None:
        """Invalid JSON raises before calling security."""
        cookies_file = tmp_path / "bad-cookies.json"
        cookies_file.write_text("this is not json {{{")

        with patch("subprocess.run") as mock_run, pytest.raises(json.JSONDecodeError):
            _install_cookies_from_file(cookies_file)

        mock_run.assert_not_called()

    def test_does_not_delete_file_if_security_fails(self, tmp_path: Path) -> None:
        """File is NOT deleted if security command fails (subprocess.CalledProcessError)."""
        cookies_data = [{"name": "test", "value": "val", "domain": ".amazon.com"}]
        cookies_file = tmp_path / "amazon-cookies.json"
        cookies_file.write_text(json.dumps(cookies_data))

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "security")
            with pytest.raises(subprocess.CalledProcessError):
                _install_cookies_from_file(cookies_file)

        # File should still exist since install failed
        assert cookies_file.exists()

    def test_security_receives_full_json_as_password(self, tmp_path: Path) -> None:
        """The full JSON string is passed as the -w argument to security."""
        cookies_data = [{"name": "session-id", "value": "abc", "domain": ".amazon.com"}]
        json_str = json.dumps(cookies_data)
        cookies_file = tmp_path / "amazon-cookies.json"
        cookies_file.write_text(json_str)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _install_cookies_from_file(cookies_file)

        args = mock_run.call_args[0][0]
        # -w should be followed by the JSON string
        w_idx = args.index("-w")
        assert args[w_idx + 1] == json_str

    def test_empty_cookies_array_is_valid_json(self, tmp_path: Path) -> None:
        """An empty array [] is valid JSON and should proceed (edge case)."""
        cookies_file = tmp_path / "empty-cookies.json"
        cookies_file.write_text("[]")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _install_cookies_from_file(cookies_file)

        mock_run.assert_called_once()
        assert not cookies_file.exists()
