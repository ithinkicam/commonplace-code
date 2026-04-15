"""Offline tests for the classify_book skill.

Does NOT invoke claude -p — that is the smoke script's job.
Validates file structure, prompt content, and fixture integrity.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "classify_book"
SKILL_MD = SKILL_DIR / "SKILL.md"
FIXTURE_DIR = SKILL_DIR / "fixtures"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke_classify_book.sh"

REQUIRED_TOKENS = {"HIGH", "MEDIUM", "LOW", "argument", "narrative", "poetry"}

FIXTURE_REQUIRED_KEYS = {"title", "author"}
FIXTURE_OPTIONAL_KEYS = {"subjects", "description", "sample_text"}
FIXTURE_ALL_KEYS = FIXTURE_REQUIRED_KEYS | FIXTURE_OPTIONAL_KEYS


class TestSkillMd:
    def test_skill_md_exists(self) -> None:
        assert SKILL_MD.exists(), f"SKILL.md not found at {SKILL_MD}"

    def test_skill_md_non_empty(self) -> None:
        content = SKILL_MD.read_text()
        assert len(content.strip()) > 100, "SKILL.md appears nearly empty"

    def test_skill_md_has_frontmatter(self) -> None:
        content = SKILL_MD.read_text()
        assert content.startswith("---"), "SKILL.md must start with YAML frontmatter"
        assert "model: haiku" in content, "classify_book must pin model: haiku"

    def test_skill_md_mentions_all_required_tokens(self) -> None:
        content = SKILL_MD.read_text()
        missing = [token for token in REQUIRED_TOKENS if token not in content]
        assert not missing, f"SKILL.md missing required tokens: {missing}"

    def test_skill_md_has_input_contract(self) -> None:
        content = SKILL_MD.read_text()
        assert "title" in content
        assert "author" in content

    def test_skill_md_has_output_contract(self) -> None:
        content = SKILL_MD.read_text()
        assert '"tier"' in content
        assert '"template"' in content
        assert '"reasoning"' in content

    def test_skill_md_specifies_insufficient_input_behavior(self) -> None:
        content = SKILL_MD.read_text()
        assert "insufficient input" in content.lower()


class TestFixtures:
    def _get_fixtures(self) -> list[Path]:
        assert FIXTURE_DIR.exists(), f"Fixture directory not found: {FIXTURE_DIR}"
        fixtures = list(FIXTURE_DIR.glob("*.json"))
        assert len(fixtures) >= 5, f"Expected at least 5 fixtures, found {len(fixtures)}"
        return fixtures

    def test_fixture_directory_exists(self) -> None:
        assert FIXTURE_DIR.exists()

    def test_fixture_count(self) -> None:
        fixtures = list(FIXTURE_DIR.glob("*.json"))
        assert 5 <= len(fixtures) <= 10, f"Expected 5–10 fixtures, found {len(fixtures)}"

    def test_fixtures_are_valid_json(self) -> None:
        for fixture in self._get_fixtures():
            with open(fixture) as f:
                data = json.load(f)
            assert isinstance(data, dict), f"{fixture.name}: must be a JSON object"

    def test_fixtures_have_required_keys(self) -> None:
        for fixture in self._get_fixtures():
            with open(fixture) as f:
                data = json.load(f)
            for key in FIXTURE_REQUIRED_KEYS:
                assert key in data, f"{fixture.name}: missing required key '{key}'"

    def test_fixtures_have_no_unexpected_keys(self) -> None:
        for fixture in self._get_fixtures():
            with open(fixture) as f:
                data = json.load(f)
            unexpected = set(data.keys()) - FIXTURE_ALL_KEYS
            assert not unexpected, f"{fixture.name}: unexpected keys {unexpected}"

    def test_fixtures_subjects_is_list_when_present(self) -> None:
        for fixture in self._get_fixtures():
            with open(fixture) as f:
                data = json.load(f)
            if "subjects" in data:
                assert isinstance(data["subjects"], list), (
                    f"{fixture.name}: 'subjects' must be a list"
                )

    def test_at_least_one_minimal_fixture(self) -> None:
        """At least one fixture should exercise the insufficient-input branch."""
        fixtures = self._get_fixtures()
        has_minimal = False
        for fixture in fixtures:
            with open(fixture) as f:
                data = json.load(f)
            title = data.get("title", "").strip()
            author = data.get("author", "").strip()
            if not title or not author:
                has_minimal = True
                break
        assert has_minimal, (
            "No minimal/under-specified fixture found. "
            "At least one fixture must exercise the insufficient-input branch."
        )


class TestSmokeScript:
    def test_smoke_script_exists(self) -> None:
        assert SMOKE_SCRIPT.exists(), f"Smoke script not found at {SMOKE_SCRIPT}"

    def test_smoke_script_is_executable(self) -> None:
        mode = SMOKE_SCRIPT.stat().st_mode
        assert mode & stat.S_IXUSR, f"{SMOKE_SCRIPT} is not executable (run: chmod +x {SMOKE_SCRIPT})"

    def test_smoke_script_references_classify_book(self) -> None:
        content = SMOKE_SCRIPT.read_text()
        assert "classify_book" in content

    def test_smoke_script_references_haiku(self) -> None:
        content = SMOKE_SCRIPT.read_text()
        assert "haiku" in content
