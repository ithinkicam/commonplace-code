"""Canonical slug generation for feast rows.

Single source of truth shared by scripts/feast_import.py and
commonplace_worker/handlers/liturgy_lff.py — the two previously carried
diverged copies (the importer skipped NFKD normalization, so accented
names produced different slugs than the LFF handler).
"""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def make_slug(primary_name: str, tradition: str = "anglican") -> str:
    """Return a stable slug for a feast: ``{name_snake}_{tradition}``.

    Accented characters are NFKD-normalized and ASCII-stripped so that
    e.g. ``"Óscar Romero"`` → ``"oscar_romero_anglican"``.

    Example: ``"Saint Mary the Virgin"`` + ``"anglican"``
    → ``"saint_mary_the_virgin_anglican"``
    """
    name = unicodedata.normalize("NFKD", primary_name).encode("ascii", "ignore").decode()
    name = _NON_ALNUM_RE.sub("_", name.lower()).strip("_")
    return f"{name}_{tradition}"
