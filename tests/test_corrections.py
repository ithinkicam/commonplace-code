"""Unit tests for commonplace_server.corrections.

Covers:
- correct_profile: first-run (file creation)
- correct_profile: append to existing file
- correct_profile: appends into existing ## Corrections section
- correct_book: happy path (creates corrections.md, updates notes.md)
- correct_book: slug not found
- atomic write (tmp file cleaned up, content present)
- directive format (YYYY-MM-DD date)
- error cases (empty correction, empty slug)
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from commonplace_server.corrections import correct_book, correct_judge, correct_profile

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DATE_RE = re.compile(r"\[directive, \d{4}-\d{2}-\d{2}\]")


def _today() -> str:
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# correct_profile — first run (creates file)
# ---------------------------------------------------------------------------


def test_profile_first_run_creates_file(tmp_path: Path) -> None:
    """When current.md is absent, correct_profile should create it."""
    profile_dir = tmp_path / "profile"
    result = correct_profile("prefer blunt register over hedged", profile_dir=profile_dir)

    assert result["status"] == "applied"
    assert result["target_type"] == "profile"

    current_md = profile_dir / "current.md"
    assert current_md.exists(), "current.md should have been created"


def test_profile_first_run_contains_directive(tmp_path: Path) -> None:
    """Created file should contain the directive with today's date."""
    profile_dir = tmp_path / "profile"
    correction = "prefer blunt register over hedged"
    result = correct_profile(correction, profile_dir=profile_dir)

    current_md = profile_dir / "current.md"
    content = current_md.read_text()

    assert correction in content
    assert _today() in content
    assert DATE_RE.search(content), "directive tag format should be [directive, YYYY-MM-DD]"
    assert result["appended_directive"] == f"[directive, {_today()}] {correction}"


def test_profile_first_run_has_minimal_header(tmp_path: Path) -> None:
    """Created file should include the standard section headings."""
    profile_dir = tmp_path / "profile"
    correct_profile("some correction", profile_dir=profile_dir)

    content = (profile_dir / "current.md").read_text()
    assert "# Profile" in content
    assert "## How to talk to me" in content


# ---------------------------------------------------------------------------
# correct_profile — append to existing file
# ---------------------------------------------------------------------------


def test_profile_append_existing_file(tmp_path: Path) -> None:
    """Appending to an existing file should not overwrite existing content."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True)
    current_md = profile_dir / "current.md"
    current_md.write_text("# Profile\n\nexisting directive\n")

    correct_profile("new directive", profile_dir=profile_dir)

    content = current_md.read_text()
    assert "existing directive" in content
    assert "new directive" in content


def test_profile_append_adds_corrections_section(tmp_path: Path) -> None:
    """First correction on an existing file should add ## Corrections section."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True)
    (profile_dir / "current.md").write_text("# Profile\n\n## How to talk to me\n\n")

    correct_profile("some correction", profile_dir=profile_dir)

    content = (profile_dir / "current.md").read_text()
    assert "## Corrections" in content


def test_profile_append_into_corrections_section(tmp_path: Path) -> None:
    """Second correction appends into the existing ## Corrections section."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True)
    existing = (
        "# Profile\n\n"
        "## Corrections\n\n"
        "[directive, 2025-01-01] first correction\n"
    )
    (profile_dir / "current.md").write_text(existing)

    correct_profile("second correction", profile_dir=profile_dir)

    content = (profile_dir / "current.md").read_text()
    assert "first correction" in content
    assert "second correction" in content
    assert content.count("## Corrections") == 1, "Should not duplicate section heading"


def test_profile_returns_path(tmp_path: Path) -> None:
    """Return dict should include the resolved path to current.md."""
    profile_dir = tmp_path / "profile"
    result = correct_profile("test", profile_dir=profile_dir)
    assert result["path"] == str(profile_dir / "current.md")


# ---------------------------------------------------------------------------
# correct_profile — error cases
# ---------------------------------------------------------------------------


def test_profile_empty_correction_returns_error(tmp_path: Path) -> None:
    profile_dir = tmp_path / "profile"
    result = correct_profile("", profile_dir=profile_dir)
    assert result["status"] == "error"
    assert "correction" in result["error"]


def test_profile_whitespace_correction_returns_error(tmp_path: Path) -> None:
    profile_dir = tmp_path / "profile"
    result = correct_profile("   ", profile_dir=profile_dir)
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# correct_book — happy path
# ---------------------------------------------------------------------------


def test_book_creates_corrections_md(tmp_path: Path) -> None:
    """correct_book should create corrections.md in the book directory."""
    books_dir = tmp_path / "books"
    slug = "thinking-fast-and-slow"
    book_dir = books_dir / slug
    book_dir.mkdir(parents=True)

    result = correct_book(slug, "this is narrative, not argument", books_dir=books_dir)

    assert result["status"] == "applied"
    assert result["target_type"] == "book"
    assert result["target_id"] == slug

    corrections_md = book_dir / "corrections.md"
    assert corrections_md.exists()
    content = corrections_md.read_text()
    assert "this is narrative, not argument" in content
    assert _today() in content


def test_book_corrections_md_directive_format(tmp_path: Path) -> None:
    """Directive in corrections.md should match [directive, YYYY-MM-DD] format."""
    books_dir = tmp_path / "books"
    slug = "some-book"
    (books_dir / slug).mkdir(parents=True)

    correct_book(slug, "template correction", books_dir=books_dir)

    content = (books_dir / slug / "corrections.md").read_text()
    assert DATE_RE.search(content), "Should contain [directive, YYYY-MM-DD] tag"


def test_book_appends_to_notes_md_corrections_section(tmp_path: Path) -> None:
    """If notes.md has a ## Corrections section, the directive is appended there."""
    books_dir = tmp_path / "books"
    slug = "a-book"
    book_dir = books_dir / slug
    book_dir.mkdir(parents=True)

    notes_md = book_dir / "notes.md"
    notes_md.write_text(
        "# Notes\n\n## Summary\n\nsome summary\n\n## Corrections\n\n"
    )

    correct_book(slug, "not an argument, it is a memoir", books_dir=books_dir)

    notes_content = notes_md.read_text()
    assert "not an argument, it is a memoir" in notes_content
    assert notes_content.count("## Corrections") == 1


def test_book_adds_corrections_section_to_notes_md(tmp_path: Path) -> None:
    """If notes.md has no ## Corrections section, one should be added."""
    books_dir = tmp_path / "books"
    slug = "another-book"
    book_dir = books_dir / slug
    book_dir.mkdir(parents=True)

    notes_md = book_dir / "notes.md"
    notes_md.write_text("# Notes\n\n## Summary\n\nsome summary\n")

    correct_book(slug, "register correction", books_dir=books_dir)

    notes_content = notes_md.read_text()
    assert "## Corrections" in notes_content
    assert "register correction" in notes_content


def test_book_no_notes_md_skips_silently(tmp_path: Path) -> None:
    """If notes.md is absent, correct_book should still succeed."""
    books_dir = tmp_path / "books"
    slug = "no-notes-book"
    book_dir = books_dir / slug
    book_dir.mkdir(parents=True)

    result = correct_book(slug, "some correction", books_dir=books_dir)

    assert result["status"] == "applied"
    assert not (book_dir / "notes.md").exists()
    assert (book_dir / "corrections.md").exists()


def test_book_corrections_md_append(tmp_path: Path) -> None:
    """Appending a second correction preserves the first one."""
    books_dir = tmp_path / "books"
    slug = "multi-correction"
    book_dir = books_dir / slug
    book_dir.mkdir(parents=True)

    correct_book(slug, "first correction", books_dir=books_dir)
    correct_book(slug, "second correction", books_dir=books_dir)

    content = (book_dir / "corrections.md").read_text()
    assert "first correction" in content
    assert "second correction" in content


def test_book_returns_corrections_md_path(tmp_path: Path) -> None:
    """Return dict should include path to corrections.md."""
    books_dir = tmp_path / "books"
    slug = "path-check-book"
    (books_dir / slug).mkdir(parents=True)

    result = correct_book(slug, "test", books_dir=books_dir)
    assert result["path"] == str(books_dir / slug / "corrections.md")


# ---------------------------------------------------------------------------
# correct_book — slug not found
# ---------------------------------------------------------------------------


def test_book_slug_not_found(tmp_path: Path) -> None:
    """When the book directory does not exist, return an error dict."""
    books_dir = tmp_path / "books"
    books_dir.mkdir(parents=True)

    result = correct_book("nonexistent-slug", "some correction", books_dir=books_dir)

    assert result["status"] == "error"
    assert result["error"] == "book slug not found"
    assert result["target_id"] == "nonexistent-slug"


# ---------------------------------------------------------------------------
# correct_book — error cases
# ---------------------------------------------------------------------------


def test_book_empty_slug_returns_error(tmp_path: Path) -> None:
    books_dir = tmp_path / "books"
    result = correct_book("", "some correction", books_dir=books_dir)
    assert result["status"] == "error"


def test_book_empty_correction_returns_error(tmp_path: Path) -> None:
    books_dir = tmp_path / "books"
    slug = "real-slug"
    (books_dir / slug).mkdir(parents=True)
    result = correct_book(slug, "", books_dir=books_dir)
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Atomic write verification
# ---------------------------------------------------------------------------


def test_atomic_write_no_tmp_files_left(tmp_path: Path) -> None:
    """After a successful write, no .tmp_ files should remain."""
    profile_dir = tmp_path / "profile"
    correct_profile("test directive", profile_dir=profile_dir)

    tmp_files = list(profile_dir.glob(".tmp_*"))
    assert tmp_files == [], f"Stray tmp files found: {tmp_files}"


def test_atomic_write_content_is_complete(tmp_path: Path) -> None:
    """Written file should be complete and readable after correction."""
    profile_dir = tmp_path / "profile"
    correct_profile("atomic write test", profile_dir=profile_dir)
    content = (profile_dir / "current.md").read_text()
    assert "atomic write test" in content
    assert len(content) > 0


# ---------------------------------------------------------------------------
# correct_judge — happy path and error cases
# ---------------------------------------------------------------------------


def test_judge_first_run_creates_file(tmp_path: Path) -> None:
    """When directives.md is absent, correct_judge should create it."""
    directives_path = tmp_path / "skills" / "judge_serendipity" / "directives.md"
    result = correct_judge(
        "stop surfacing politics during work hours",
        directives_path=directives_path,
    )

    assert result["status"] == "applied"
    assert result["target_type"] == "judge_serendipity"
    assert directives_path.exists()


def test_judge_first_run_contains_directive(tmp_path: Path) -> None:
    """Created file should contain the directive with today's date."""
    directives_path = tmp_path / "directives.md"
    correction = "prefer connections to applied math, deprioritize philosophy"
    result = correct_judge(correction, directives_path=directives_path)

    content = directives_path.read_text()
    assert correction in content
    assert _today() in content
    assert DATE_RE.search(content)
    assert result["appended_directive"] == f"[directive, {_today()}] {correction}"


def test_judge_append_existing_file(tmp_path: Path) -> None:
    """Appending should preserve existing directives."""
    directives_path = tmp_path / "directives.md"
    directives_path.write_text("[directive, 2025-01-01] earlier directive\n")

    correct_judge("newer directive", directives_path=directives_path)

    content = directives_path.read_text()
    assert "earlier directive" in content
    assert "newer directive" in content


def test_judge_append_handles_missing_trailing_newline(tmp_path: Path) -> None:
    """Existing file without trailing newline should still get a clean append."""
    directives_path = tmp_path / "directives.md"
    directives_path.write_text("[directive, 2025-01-01] no trailing newline")

    correct_judge("appended", directives_path=directives_path)

    content = directives_path.read_text()
    lines = [line for line in content.splitlines() if line.strip()]
    assert len(lines) == 2
    assert "no trailing newline" in lines[0]
    assert "appended" in lines[1]


def test_judge_returns_path(tmp_path: Path) -> None:
    """Return dict should include the resolved directives path."""
    directives_path = tmp_path / "directives.md"
    result = correct_judge("test", directives_path=directives_path)
    assert result["path"] == str(directives_path)


def test_judge_empty_correction_returns_error(tmp_path: Path) -> None:
    directives_path = tmp_path / "directives.md"
    result = correct_judge("", directives_path=directives_path)
    assert result["status"] == "error"
    assert "correction" in result["error"]


def test_judge_whitespace_correction_returns_error(tmp_path: Path) -> None:
    directives_path = tmp_path / "directives.md"
    result = correct_judge("   ", directives_path=directives_path)
    assert result["status"] == "error"


def test_judge_creates_parent_directories(tmp_path: Path) -> None:
    """Intermediate directories should be created if absent."""
    directives_path = tmp_path / "deep" / "nested" / "path" / "directives.md"
    result = correct_judge("nested test", directives_path=directives_path)
    assert result["status"] == "applied"
    assert directives_path.exists()


def test_judge_atomic_write_no_tmp_files(tmp_path: Path) -> None:
    """No .tmp_ files should remain after a successful write."""
    directives_path = tmp_path / "directives.md"
    correct_judge("atomic test", directives_path=directives_path)
    tmp_files = list(tmp_path.glob(".tmp_*"))
    assert tmp_files == []


def test_judge_env_var_override(tmp_path: Path, monkeypatch) -> None:
    """COMMONPLACE_JUDGE_DIRECTIVES_PATH env var should be honored."""
    target = tmp_path / "env_directives.md"
    monkeypatch.setenv("COMMONPLACE_JUDGE_DIRECTIVES_PATH", str(target))

    result = correct_judge("env override test")

    assert result["status"] == "applied"
    assert target.exists()
    assert "env override test" in target.read_text()
