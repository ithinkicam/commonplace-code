"""Vault filesystem primitives — atomic writes + vault root resolution.

Lives alongside the worker handlers because the vault is a worker-side
concern. Intentionally small: two write helpers and a root resolver.
Frontmatter rendering lives in ``frontmatter.py``; per-handler path
layout (``captures/YYYY/MM/...``) stays in the handlers because the
filename conventions legitimately differ across capture kinds.

Atomicity
---------
Both write helpers use the same ``<path>.tmp`` + ``fsync`` + ``rename``
pattern the handlers previously reimplemented 5-6 times. On success,
the temp file is renamed atomically on the same filesystem; on any
exception during the write, the temp file is removed so callers never
see half-written artifacts leaking into the vault.

The ``.tmp`` suffix is appended to the filename — not substituted for
the existing suffix — so a path like ``image.png`` becomes
``image.png.tmp`` rather than ``image.tmp``. This also fixes a latent
bug in the old ``profile.py`` implementation which used
``with_suffix(".md.tmp")`` and silently stripped non-``.md`` suffixes.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path


def vault_root() -> Path:
    """Return the vault root directory.

    ``$COMMONPLACE_VAULT_DIR`` (env override) wins; otherwise
    ``~/commonplace``. Expands ``~`` / env vars for operator convenience.
    """
    env = os.environ.get("COMMONPLACE_VAULT_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / "commonplace"


def _tmp_path(target: Path) -> Path:
    """Return the sibling ``.tmp`` path used during atomic writes."""
    return target.with_name(target.name + ".tmp")


def atomic_write_bytes(target: Path, data: bytes) -> Path:
    """Atomically write ``data`` to ``target``; return ``target``.

    Parents are created as needed. On any failure during the write the
    ``.tmp`` sibling is removed so a retry starts from a clean slate.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(target)
    try:
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.rename(target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return target


def atomic_write_text(
    target: Path, text: str, *, encoding: str = "utf-8"
) -> Path:
    """Atomically write ``text`` to ``target``; return ``target``.

    Thin wrapper over :func:`atomic_write_bytes` that encodes the string.
    Separate helper so call sites read naturally and so a future change
    (e.g. different newline handling) has one place to land.
    """
    return atomic_write_bytes(target, text.encode(encoding))
