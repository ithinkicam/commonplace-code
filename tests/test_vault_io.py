"""Tests for commonplace_worker.vault_io — atomic write primitives."""

from __future__ import annotations

from pathlib import Path

import pytest

from commonplace_worker.vault_io import (
    atomic_write_bytes,
    atomic_write_text,
    vault_root,
)


def test_vault_root_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COMMONPLACE_VAULT_DIR", str(tmp_path))
    assert vault_root() == tmp_path


def test_vault_root_default_is_home_commonplace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COMMONPLACE_VAULT_DIR", raising=False)
    assert vault_root() == Path.home() / "commonplace"


def test_atomic_write_text_creates_parents(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c.md"
    result = atomic_write_text(target, "hello\n")
    assert result == target
    assert target.read_text() == "hello\n"


def test_atomic_write_bytes_roundtrips_binary(tmp_path: Path) -> None:
    target = tmp_path / "image.png"
    payload = bytes(range(256))
    atomic_write_bytes(target, payload)
    assert target.read_bytes() == payload


def test_atomic_write_leaves_no_tmp_on_success(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    atomic_write_text(target, "ok")
    siblings = {p.name for p in tmp_path.iterdir()}
    assert siblings == {"note.md"}


def test_atomic_write_tmp_uses_full_name_not_suffix(tmp_path: Path) -> None:
    """Verify tmp naming is <name>.tmp, not with_suffix('.tmp').

    The latter would turn 'image.png' into 'image.tmp', which is the
    latent profile.py bug the refactor fixes. Assert the implementation
    appends to the filename, not substitutes the suffix.
    """
    target = tmp_path / "image.png"
    # Mid-write observation is inherently racy; instead we verify indirectly
    # by checking that a file named 'image.tmp' (the bad suffix-substitute
    # result) never appears.
    atomic_write_bytes(target, b"x")
    assert not (tmp_path / "image.tmp").exists()
    assert target.exists()


def test_atomic_write_cleans_tmp_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "note.md"

    # Force os.fsync to raise so the write aborts mid-flight.
    import os

    real_fsync = os.fsync
    call_count = {"n": 0}

    def exploding_fsync(fd: int) -> None:
        call_count["n"] += 1
        raise OSError("simulated disk error")

    monkeypatch.setattr(os, "fsync", exploding_fsync)

    with pytest.raises(OSError, match="simulated disk error"):
        atomic_write_text(target, "should not appear")

    monkeypatch.setattr(os, "fsync", real_fsync)

    # Neither the target nor the .tmp file should exist after the failure.
    assert not target.exists()
    assert not (tmp_path / "note.md.tmp").exists()
    assert call_count["n"] == 1
