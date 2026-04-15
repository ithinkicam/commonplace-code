"""Offline tests for the three book note skills (argument, narrative, poetry).

Does NOT invoke claude -p — that is the smoke script's job.
Validates file structure, prompt content, and fixture integrity.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke_book_notes.sh"

FIXTURE_REQUIRED_KEYS = {"title", "author", "text", "tier"}
FIXTURE_OPTIONAL_KEYS = {"reader_context"}
FIXTURE_ALL_KEYS = FIXTURE_REQUIRED_KEYS | FIXTURE_OPTIONAL_KEYS
VALID_TIERS = {"HIGH", "MEDIUM", "LOW"}

# Per-skill configuration
SKILL_CONFIGS = {
    "book_note_argument": {
        "h1_suffix": "argument note",
        "required_headers": [
            "## Thesis",
            "## Core argument",
            "## Key moves",
            "## Objections and limits",
            "## Durable takeaways",
        ],
        "model": "sonnet",
        "fixture": "marcus_aurelius.json",
    },
    "book_note_narrative": {
        "h1_suffix": "narrative note",
        "required_headers": [
            "## Arc",
            "## Voice and texture",
            "## Characters or figures",
            "## Images and scenes",
            "## What it turns on",
            "## Durable takeaways",
        ],
        "model": "sonnet",
        "fixture": "austen_pride.json",
    },
    "book_note_poetry": {
        "h1_suffix": "poetry note",
        "required_headers": [
            "## Project",
            "## Form and prosody",
            "## Recurring images",
            "## Quiet center",
            "## Durable takeaways",
        ],
        "model": "sonnet",
        "fixture": "dickinson.json",
    },
}


class TestSkillMdExists:
    def test_argument_skill_md_exists(self) -> None:
        path = REPO_ROOT / "skills" / "book_note_argument" / "SKILL.md"
        assert path.exists(), f"SKILL.md not found at {path}"

    def test_narrative_skill_md_exists(self) -> None:
        path = REPO_ROOT / "skills" / "book_note_narrative" / "SKILL.md"
        assert path.exists(), f"SKILL.md not found at {path}"

    def test_poetry_skill_md_exists(self) -> None:
        path = REPO_ROOT / "skills" / "book_note_poetry" / "SKILL.md"
        assert path.exists(), f"SKILL.md not found at {path}"


class TestSkillMdContent:
    """Each SKILL.md must be non-empty, have frontmatter, pin sonnet, and mention required headers."""

    def _skill_md(self, skill_name: str) -> tuple[Path, str]:
        path = REPO_ROOT / "skills" / skill_name / "SKILL.md"
        return path, path.read_text()

    def _check_skill(self, skill_name: str) -> None:
        config = SKILL_CONFIGS[skill_name]
        path, content = self._skill_md(skill_name)

        assert len(content.strip()) > 100, f"{path}: SKILL.md appears nearly empty"
        assert content.startswith("---"), f"{path}: SKILL.md must start with YAML frontmatter"
        assert f"model: {config['model']}" in content, (
            f"{path}: must pin model: {config['model']}"
        )

        for header in config["required_headers"]:
            assert header in content, f"{path}: missing required section header '{header}'"

        # Input contract fields
        for field in ("title", "author", "text", "tier"):
            assert field in content, f"{path}: missing input field '{field}' in SKILL.md"

        # Output contract: H1 suffix
        assert config["h1_suffix"] in content, (
            f"{path}: SKILL.md must mention H1 suffix '{config['h1_suffix']}'"
        )

    def test_argument_skill_md_content(self) -> None:
        self._check_skill("book_note_argument")

    def test_narrative_skill_md_content(self) -> None:
        self._check_skill("book_note_narrative")

    def test_poetry_skill_md_content(self) -> None:
        self._check_skill("book_note_poetry")


class TestFixtures:
    """Each skill must have exactly one fixture JSON with required keys."""

    def _fixture(self, skill_name: str) -> tuple[Path, dict]:
        config = SKILL_CONFIGS[skill_name]
        path = REPO_ROOT / "skills" / skill_name / "fixtures" / config["fixture"]
        assert path.exists(), f"Fixture not found: {path}"
        with open(path) as f:
            data = json.load(f)
        return path, data

    def _check_fixture(self, skill_name: str) -> None:
        path, data = self._fixture(skill_name)
        assert isinstance(data, dict), f"{path.name}: must be a JSON object"

        for key in FIXTURE_REQUIRED_KEYS:
            assert key in data, f"{path.name}: missing required key '{key}'"
            assert data[key], f"{path.name}: required key '{key}' must be non-empty"

        unexpected = set(data.keys()) - FIXTURE_ALL_KEYS
        assert not unexpected, f"{path.name}: unexpected keys {unexpected}"

        assert data["tier"] in VALID_TIERS, (
            f"{path.name}: 'tier' must be one of {VALID_TIERS}, got '{data['tier']}'"
        )

        assert len(data["text"]) >= 50, f"{path.name}: 'text' is too short to be useful"

    def test_argument_fixture(self) -> None:
        self._check_fixture("book_note_argument")

    def test_narrative_fixture(self) -> None:
        self._check_fixture("book_note_narrative")

    def test_poetry_fixture(self) -> None:
        self._check_fixture("book_note_poetry")

    def test_fixture_directories_exist(self) -> None:
        for skill_name in SKILL_CONFIGS:
            fixture_dir = REPO_ROOT / "skills" / skill_name / "fixtures"
            assert fixture_dir.exists(), f"Fixture directory not found: {fixture_dir}"

    def test_fixture_text_is_public_domain(self) -> None:
        """Basic sanity: fixtures use authors known to be public domain."""
        pd_authors = {"Marcus Aurelius", "Jane Austen", "Emily Dickinson"}
        for skill_name, config in SKILL_CONFIGS.items():
            path = REPO_ROOT / "skills" / skill_name / "fixtures" / config["fixture"]
            with open(path) as f:
                data = json.load(f)
            assert data.get("author") in pd_authors, (
                f"{path.name}: author '{data.get('author')}' not in known PD set {pd_authors}"
            )


class TestSmokeScript:
    def test_smoke_script_exists(self) -> None:
        assert SMOKE_SCRIPT.exists(), f"Smoke script not found at {SMOKE_SCRIPT}"

    def test_smoke_script_is_executable(self) -> None:
        mode = SMOKE_SCRIPT.stat().st_mode
        assert mode & stat.S_IXUSR, (
            f"{SMOKE_SCRIPT} is not executable (run: chmod +x {SMOKE_SCRIPT})"
        )

    def test_smoke_script_references_all_skills(self) -> None:
        content = SMOKE_SCRIPT.read_text()
        for skill_name in SKILL_CONFIGS:
            assert skill_name in content, (
                f"smoke_book_notes.sh does not reference skill '{skill_name}'"
            )

    def test_smoke_script_references_haiku(self) -> None:
        content = SMOKE_SCRIPT.read_text()
        assert "haiku" in content, "smoke_book_notes.sh must reference haiku model"

    def test_smoke_script_references_all_required_headers(self) -> None:
        content = SMOKE_SCRIPT.read_text()
        # Key required headers should appear in the smoke script for validation
        key_headers = ["## Thesis", "## Arc", "## Project"]
        for header in key_headers:
            assert header in content, (
                f"smoke_book_notes.sh does not reference required header '{header}'"
            )
