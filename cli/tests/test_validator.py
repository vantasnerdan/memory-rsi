"""Tests for frontmatter and section validation."""

from pathlib import Path

import pytest

from agent_memory.validator import validate_directory, validate_file

FIXTURES = Path(__file__).parent / "fixtures"


class TestValidateFile:
    def test_valid_entry(self) -> None:
        result = validate_file(FIXTURES / "valid_entry.md")
        assert result.is_valid
        assert "Frontmatter exists" in result.checks_passed
        assert "Required field: description" in result.checks_passed
        assert "Required field: author" in result.checks_passed
        assert "Required field: created" in result.checks_passed
        assert "Required field: updated" in result.checks_passed
        assert "Section descriptions valid" in result.checks_passed

    def test_minimal_valid(self) -> None:
        result = validate_file(FIXTURES / "minimal_valid.md")
        assert result.is_valid
        assert len(result.warnings) > 0  # missing optional fields

    def test_missing_frontmatter(self) -> None:
        result = validate_file(FIXTURES / "bad_frontmatter.md")
        assert not result.is_valid
        assert any("does not start with '---'" in e for e in result.errors)

    def test_missing_required_fields(self) -> None:
        result = validate_file(FIXTURES / "missing_fields.md")
        assert not result.is_valid
        assert any("author" in e for e in result.errors)
        assert any("created" in e for e in result.errors)
        assert any("updated" in e for e in result.errors)
        # description IS present
        assert "Required field: description" in result.checks_passed

    def test_bad_section_descriptions(self) -> None:
        result = validate_file(FIXTURES / "bad_section_desc.md")
        assert not result.is_valid
        # Code-first sections remain errors
        assert any("Code First" in e for e in result.errors)
        # List-first sections are now warnings, not errors
        assert not any("List First" in e for e in result.errors)
        assert any("List First" in w for w in result.warnings)

    def test_invalid_enum_values(self) -> None:
        result = validate_file(FIXTURES / "invalid_enums.md")
        assert not result.is_valid
        assert any("confidence" in e and "high" in e for e in result.errors)
        assert any("category" in e and "reference" in e for e in result.errors)
        assert any("status" in e and "pending" in e for e in result.errors)

    def test_file_not_found(self) -> None:
        result = validate_file(Path("/nonexistent/file.md"))
        assert not result.is_valid
        assert any("not found" in e for e in result.errors)

    def test_not_a_file(self, tmp_path: Path) -> None:
        result = validate_file(tmp_path)
        assert not result.is_valid
        assert any("Not a file" in e for e in result.errors)


class TestListStartingSections:
    """Tests for sections that start with list items (issue #50)."""

    def test_list_starting_section_is_valid(self, tmp_path: Path) -> None:
        """Sections starting with lists should pass validation (with warning)."""
        entry = tmp_path / "list-section.md"
        entry.write_text(
            "---\n"
            "description: Entry with list-starting sections\n"
            "author: test-agent\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-10T08:00:00Z\n"
            "---\n"
            "# Test Entry\n\n"
            "## Key Findings\n"
            "- Finding one\n"
            "- Finding two\n"
        )
        result = validate_file(entry)
        assert result.is_valid
        assert any("Key Findings" in w for w in result.warnings)

    def test_asterisk_list_is_valid(self, tmp_path: Path) -> None:
        """Sections starting with * lists should also pass validation."""
        entry = tmp_path / "asterisk-list.md"
        entry.write_text(
            "---\n"
            "description: Entry with asterisk list\n"
            "author: test-agent\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-10T08:00:00Z\n"
            "---\n"
            "# Test\n\n"
            "## Links\n"
            "* GitHub: https://example.com\n"
            "* Docs: https://example.com/docs\n"
        )
        result = validate_file(entry)
        assert result.is_valid

    def test_prose_then_list_no_warning(self, tmp_path: Path) -> None:
        """Sections with prose before list should produce no warning."""
        entry = tmp_path / "prose-then-list.md"
        entry.write_text(
            "---\n"
            "description: Entry with prose before list\n"
            "author: test-agent\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-10T08:00:00Z\n"
            "---\n"
            "# Test\n\n"
            "## Findings\n"
            "These are the key findings from the analysis.\n\n"
            "- Finding one\n"
            "- Finding two\n"
        )
        result = validate_file(entry)
        assert result.is_valid
        assert not any("Findings" in w for w in result.warnings)

    def test_code_first_still_error(self, tmp_path: Path) -> None:
        """Sections starting with code blocks remain errors."""
        entry = tmp_path / "code-first.md"
        entry.write_text(
            "---\n"
            "description: Entry with code-first section\n"
            "author: test-agent\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-10T08:00:00Z\n"
            "---\n"
            "# Test\n\n"
            "## Example\n"
            "```python\n"
            "print('hello')\n"
            "```\n"
        )
        result = validate_file(entry)
        assert not result.is_valid
        assert any("Example" in e for e in result.errors)

    def test_table_first_still_error(self, tmp_path: Path) -> None:
        """Sections starting with tables remain errors."""
        entry = tmp_path / "table-first.md"
        entry.write_text(
            "---\n"
            "description: Entry with table-first section\n"
            "author: test-agent\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-10T08:00:00Z\n"
            "---\n"
            "# Test\n\n"
            "## Data\n"
            "| col1 | col2 |\n"
            "|------|------|\n"
        )
        result = validate_file(entry)
        assert not result.is_valid
        assert any("Data" in e for e in result.errors)

    def test_multiple_list_sections_all_valid(self, tmp_path: Path) -> None:
        """Multiple list-starting sections should all pass validation."""
        entry = tmp_path / "multi-list.md"
        entry.write_text(
            "---\n"
            "description: Multiple list-starting sections\n"
            "author: test-agent\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-10T08:00:00Z\n"
            "---\n"
            "# Test\n\n"
            "## Agent\n"
            "- nexus-marbell\n\n"
            "## Files Modified\n"
            "- src/main.py\n"
            "- tests/test_main.py\n\n"
            "## Session Metadata\n"
            "- Duration: 30 minutes\n"
        )
        result = validate_file(entry)
        assert result.is_valid
        assert len([w for w in result.warnings if "prose description" in w]) == 3

    def test_mixed_prose_and_list_sections_valid(self, tmp_path: Path) -> None:
        """Entry with both prose-starting and list-starting sections passes."""
        entry = tmp_path / "mixed.md"
        entry.write_text(
            "---\n"
            "description: Mixed prose and list sections\n"
            "author: test-agent\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-10T08:00:00Z\n"
            "---\n"
            "# Mixed Entry\n\n"
            "## Overview\n"
            "This is a prose description.\n\n"
            "## Items\n"
            "- Item one\n"
            "- Item two\n"
        )
        result = validate_file(entry)
        assert result.is_valid
        assert "Section descriptions valid" in result.checks_passed
        # Only the list section gets a warning
        list_warnings = [w for w in result.warnings if "prose description" in w]
        assert len(list_warnings) == 1
        assert "Items" in list_warnings[0]

    def test_warning_message_is_helpful(self, tmp_path: Path) -> None:
        """Warning message should suggest adding prose for TOC readability."""
        entry = tmp_path / "helpful-warning.md"
        entry.write_text(
            "---\n"
            "description: Check warning message quality\n"
            "author: test-agent\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-10T08:00:00Z\n"
            "---\n"
            "# Test\n\n"
            "## Results\n"
            "- Result one\n"
        )
        result = validate_file(entry)
        assert result.is_valid
        assert len(result.warnings) > 0
        warning = [w for w in result.warnings if "Results" in w][0]
        assert "prose description" in warning
        assert "TOC" in warning


class TestValidateDirectory:
    def test_validate_fixtures(self) -> None:
        results = validate_directory(FIXTURES)
        assert len(results) > 0
        valid_count = sum(1 for r in results if r.is_valid)
        invalid_count = sum(1 for r in results if not r.is_valid)
        assert valid_count >= 2  # valid_entry.md and minimal_valid.md
        assert invalid_count >= 3  # bad_frontmatter, missing_fields, invalid_enums

    def test_nonexistent_directory(self) -> None:
        with pytest.raises(FileNotFoundError):
            validate_directory(Path("/nonexistent/dir"))

    def test_not_a_directory(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("hello")
        with pytest.raises(ValueError, match="Not a directory"):
            validate_directory(f)

    def test_empty_directory(self, tmp_path: Path) -> None:
        results = validate_directory(tmp_path)
        assert len(results) == 0
