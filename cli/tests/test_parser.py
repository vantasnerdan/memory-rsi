"""Tests for frontmatter and section parsing."""

from pathlib import Path

import pytest

from agent_memory.parser import (
    extract_section,
    parse_frontmatter,
    parse_sections,
    read_entry,
    resolve_md_path,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestParseFrontmatter:
    def test_valid_frontmatter(self) -> None:
        fm, body = read_entry(FIXTURES / "valid_entry.md")
        assert fm.description == "NaN policy — never fillna, missing means unknown"
        assert fm.author == "mlops-kelvin"
        assert fm.confidence == "established"
        assert fm.category == "atlas"
        assert fm.status == "active"
        assert fm.tags == ["data", "policy", "nan"]
        assert fm.related == ["[[shift-one-policy]]"]
        assert "# NaN Policy" in body

    def test_minimal_frontmatter(self) -> None:
        fm, body = read_entry(FIXTURES / "minimal_valid.md")
        assert fm.description == "Minimal valid entry with only required fields"
        assert fm.author == "test-agent"
        assert fm.confidence == ""
        assert fm.tags == []

    def test_missing_frontmatter_delimiter(self) -> None:
        with pytest.raises(ValueError, match="does not start with '---'"):
            text = "# No frontmatter\nContent here."
            parse_frontmatter(text)

    def test_missing_closing_delimiter(self) -> None:
        with pytest.raises(ValueError, match="missing closing '---'"):
            text = "---\ndescription: broken\nno closing delimiter"
            parse_frontmatter(text)

    def test_invalid_yaml(self) -> None:
        with pytest.raises(ValueError, match="Invalid YAML"):
            text = "---\n: invalid: yaml: here:\n---\n"
            parse_frontmatter(text)

    def test_non_mapping_yaml(self) -> None:
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            text = "---\n- list\n- not mapping\n---\n"
            parse_frontmatter(text)

    def test_empty_frontmatter(self) -> None:
        text = "---\n---\nBody content."
        fm, body = parse_frontmatter(text)
        assert fm.description == ""
        assert "Body content." in body

    def test_multiline_description(self) -> None:
        text = "---\ndescription: >\n  A long description\n  that spans lines\nauthor: test\ncreated: 2026-01-01\nupdated: 2026-01-01\n---\n"
        fm, _ = parse_frontmatter(text)
        assert "long description" in fm.description


class TestParseSections:
    def test_extract_sections(self) -> None:
        _, body = read_entry(FIXTURES / "valid_entry.md")
        sections = parse_sections(body)
        assert len(sections) == 3
        assert sections[0].title == "Rationale"
        assert sections[1].title == "Implementation"
        assert sections[2].title == "Exceptions"

    def test_section_descriptions(self) -> None:
        _, body = read_entry(FIXTURES / "valid_entry.md")
        sections = parse_sections(body)
        assert sections[0].description == "Why missing values must not be filled with defaults."
        assert sections[2].description == "Cases where None is substituted instead of NaN."

    def test_section_content(self) -> None:
        _, body = read_entry(FIXTURES / "valid_entry.md")
        sections = parse_sections(body)
        assert "silent bias" in sections[0].content
        assert "## Rationale" in sections[0].content

    def test_ignores_h1_and_h3(self) -> None:
        text = "---\ndescription: test\n---\n# H1\n## H2\nDesc.\n### H3\nContent."
        _, body = parse_frontmatter(text)
        sections = parse_sections(body)
        assert len(sections) == 1
        assert sections[0].title == "H2"

    def test_no_sections(self) -> None:
        text = "---\ndescription: test\n---\nJust a body with no sections."
        _, body = parse_frontmatter(text)
        sections = parse_sections(body)
        assert len(sections) == 0


class TestExtractSection:
    def test_exact_match(self) -> None:
        _, body = read_entry(FIXTURES / "valid_entry.md")
        matches = extract_section(body, "Rationale")
        assert len(matches) == 1
        assert matches[0].title == "Rationale"

    def test_case_insensitive(self) -> None:
        _, body = read_entry(FIXTURES / "valid_entry.md")
        matches = extract_section(body, "rationale")
        assert len(matches) == 1
        assert matches[0].title == "Rationale"

    def test_partial_match(self) -> None:
        _, body = read_entry(FIXTURES / "valid_entry.md")
        matches = extract_section(body, "ration")
        assert len(matches) == 1
        assert matches[0].title == "Rationale"

    def test_no_match(self) -> None:
        _, body = read_entry(FIXTURES / "valid_entry.md")
        matches = extract_section(body, "nonexistent")
        assert len(matches) == 0

    def test_multiple_matches(self) -> None:
        body = "## Alpha One\nDesc.\n\n## Alpha Two\nDesc.\n"
        matches = extract_section(body, "alpha")
        assert len(matches) == 2


class TestResolveMdPath:
    def test_existing_file(self) -> None:
        path = resolve_md_path(str(FIXTURES / "valid_entry.md"))
        assert path.exists()

    def test_auto_append_md(self) -> None:
        path = resolve_md_path(str(FIXTURES / "valid_entry"))
        assert path.exists()
        assert path.suffix == ".md"

    def test_nonexistent_returns_original(self) -> None:
        path = resolve_md_path("/nonexistent/file")
        assert str(path) == "/nonexistent/file"


class TestReadEntry:
    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            read_entry(Path("/nonexistent/file.md"))

    def test_not_a_file(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a file"):
            read_entry(tmp_path)
