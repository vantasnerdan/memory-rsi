"""Tests for frontmatter generation and file writing (writer.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from agent_memory.parser import parse_frontmatter
from agent_memory.validator import (
    CATEGORY_VALUES,
    CONFIDENCE_VALUES,
    STATUS_VALUES,
    validate_file,
)
from agent_memory.writer import (
    create_entry,
    generate_frontmatter,
    to_kebab_case,
    update_entry,
)


class TestToKebabCase:
    def test_spaces(self) -> None:
        assert to_kebab_case("my entry name") == "my-entry-name"

    def test_underscores(self) -> None:
        assert to_kebab_case("nan_policy") == "nan-policy"

    def test_mixed_case(self) -> None:
        assert to_kebab_case("NaN Policy") == "nan-policy"

    def test_special_chars(self) -> None:
        assert to_kebab_case("hello! @world#") == "hello-world"

    def test_leading_trailing_hyphens(self) -> None:
        assert to_kebab_case("--leading-trailing--") == "leading-trailing"

    def test_collapsed_hyphens(self) -> None:
        assert to_kebab_case("a---b---c") == "a-b-c"

    def test_empty_string(self) -> None:
        assert to_kebab_case("") == ""

    def test_mixed_separators(self) -> None:
        assert to_kebab_case("My_Entry Name") == "my-entry-name"

    def test_alphanumeric_preserved(self) -> None:
        assert to_kebab_case("version2 release") == "version2-release"

    def test_only_special_chars(self) -> None:
        assert to_kebab_case("!@#$%") == ""


class TestGenerateFrontmatter:
    def test_required_fields_present(self) -> None:
        fm_str = generate_frontmatter(description="Test entry", author="agent-1")
        fm, _ = parse_frontmatter(fm_str + "\n")
        assert fm.description == "Test entry"
        assert fm.author == "agent-1"

    def test_timestamps_set(self) -> None:
        fm_str = generate_frontmatter(description="Test", author="agent-1")
        fm, _ = parse_frontmatter(fm_str + "\n")
        assert fm.created != ""
        assert fm.updated != ""
        assert fm.created == fm.updated  # both set to same timestamp on creation

    def test_optional_fields_included_when_provided(self) -> None:
        fm_str = generate_frontmatter(
            description="Test",
            author="agent-1",
            tags=["tag1", "tag2"],
            category="atlas",
            confidence="established",
            status="active",
        )
        fm, _ = parse_frontmatter(fm_str + "\n")
        assert fm.tags == ["tag1", "tag2"]
        assert fm.category == "atlas"
        assert fm.confidence == "established"
        assert fm.status == "active"

    def test_optional_fields_omitted_when_empty(self) -> None:
        fm_str = generate_frontmatter(
            description="Test",
            author="agent-1",
            tags=None,
            category="",
            confidence="",
        )
        # Parse the raw YAML between --- delimiters
        lines = fm_str.strip().split("\n")
        yaml_text = "\n".join(lines[1:-1])
        data = yaml.safe_load(yaml_text)
        assert "tags" not in data
        assert "category" not in data
        assert "confidence" not in data

    def test_invalid_confidence_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid confidence"):
            generate_frontmatter(description="Test", author="a", confidence="bogus")

    def test_invalid_category_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid category"):
            generate_frontmatter(description="Test", author="a", category="bogus")

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid status"):
            generate_frontmatter(description="Test", author="a", status="bogus")

    def test_tags_as_list(self) -> None:
        fm_str = generate_frontmatter(
            description="Test", author="a", tags=["alpha", "beta"]
        )
        fm, _ = parse_frontmatter(fm_str + "\n")
        assert isinstance(fm.tags, list)
        assert fm.tags == ["alpha", "beta"]

    def test_yaml_is_parseable(self) -> None:
        fm_str = generate_frontmatter(
            description="Valid YAML check",
            author="agent-1",
            tags=["a"],
            category="atlas",
            confidence="working",
            status="draft",
        )
        # Should parse without errors
        fm, body = parse_frontmatter(fm_str + "\n")
        assert fm.description == "Valid YAML check"

    def test_related_field(self) -> None:
        fm_str = generate_frontmatter(
            description="Test",
            author="a",
            related=["[[other-entry]]"],
        )
        fm, _ = parse_frontmatter(fm_str + "\n")
        assert fm.related == ["[[other-entry]]"]

    def test_all_valid_confidence_values(self) -> None:
        for val in CONFIDENCE_VALUES:
            fm_str = generate_frontmatter(description="T", author="a", confidence=val)
            assert f"confidence: {val}" in fm_str

    def test_all_valid_category_values(self) -> None:
        for val in CATEGORY_VALUES:
            fm_str = generate_frontmatter(description="T", author="a", category=val)
            assert f"category: {val}" in fm_str

    def test_all_valid_status_values(self) -> None:
        for val in STATUS_VALUES:
            fm_str = generate_frontmatter(description="T", author="a", status=val)
            assert f"status: {val}" in fm_str


class TestCreateEntry:
    def test_creates_file_at_correct_path(self, tmp_path: Path) -> None:
        path = create_entry(
            base_path=tmp_path,
            agent_id="test-agent",
            name="My Entry",
            description="Test entry",
        )
        assert path.exists()
        assert path.is_file()

    def test_auto_creates_directories(self, tmp_path: Path) -> None:
        path = create_entry(
            base_path=tmp_path,
            agent_id="test-agent",
            name="new-entry",
            description="Test",
        )
        assert path.parent.is_dir()

    def test_kebab_case_filename(self, tmp_path: Path) -> None:
        path = create_entry(
            base_path=tmp_path,
            agent_id="test-agent",
            name="My Entry Name",
            description="Test",
        )
        assert path.name == "my-entry-name.md"

    def test_category_subdirectory(self, tmp_path: Path) -> None:
        path = create_entry(
            base_path=tmp_path,
            agent_id="test-agent",
            name="categorized",
            description="Test",
            category="atlas",
        )
        assert "atlas" in path.parts
        assert path.parent.name == "atlas"

    def test_shared_entry_path(self, tmp_path: Path) -> None:
        path = create_entry(
            base_path=tmp_path,
            agent_id="test-agent",
            name="shared-entry",
            description="Test",
            shared=True,
        )
        assert "shared" in path.parts
        assert "test-agent" not in str(path)

    def test_shared_with_category(self, tmp_path: Path) -> None:
        path = create_entry(
            base_path=tmp_path,
            agent_id="test-agent",
            name="shared-cat",
            description="Test",
            shared=True,
            category="moc",
        )
        assert path == tmp_path / "shared" / "moc" / "shared-cat.md"

    def test_base_already_contains_agent_id_no_double_nesting(
        self, tmp_path: Path
    ) -> None:
        """When --base points to agent-scoped dir, don't double-nest agent_id."""
        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        path = create_entry(
            base_path=agent_dir,
            agent_id="test-agent",
            name="no-double",
            description="Test double-nesting fix",
            category="atlas",
        )
        # Should be {agent_dir}/atlas/no-double.md, NOT {agent_dir}/test-agent/atlas/
        assert path == agent_dir / "atlas" / "no-double.md"
        assert path.exists()
        # Verify agent_id appears exactly once in the path
        parts = path.parts
        assert parts.count("test-agent") == 1

    def test_base_already_contains_agent_id_no_trailing_slash(
        self, tmp_path: Path
    ) -> None:
        """Same fix works regardless of trailing slash on --base."""
        agent_dir = tmp_path / "my-agent"
        agent_dir.mkdir()
        path = create_entry(
            base_path=agent_dir,
            agent_id="my-agent",
            name="entry",
            description="Test",
        )
        assert path == agent_dir / "entry.md"
        assert "my-agent" not in path.name

    def test_base_at_memory_root_still_nests_correctly(
        self, tmp_path: Path
    ) -> None:
        """Normal case: --base memory/ should still add agent_id."""
        path = create_entry(
            base_path=tmp_path,
            agent_id="test-agent",
            name="normal-nesting",
            description="Normal case",
            category="efforts",
        )
        assert path == tmp_path / "test-agent" / "efforts" / "normal-nesting.md"
        assert path.exists()

    def test_shared_not_affected_by_agent_id_in_base(
        self, tmp_path: Path
    ) -> None:
        """Shared entries should always use shared/ even if base ends with agent_id."""
        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        path = create_entry(
            base_path=agent_dir,
            agent_id="test-agent",
            name="shared-test",
            description="Test",
            shared=True,
        )
        assert "shared" in path.parts
        assert path == agent_dir / "shared" / "shared-test.md"

    def test_raises_file_exists_error_for_duplicates(self, tmp_path: Path) -> None:
        create_entry(
            base_path=tmp_path,
            agent_id="test-agent",
            name="duplicate",
            description="First",
        )
        with pytest.raises(FileExistsError, match="already exists"):
            create_entry(
                base_path=tmp_path,
                agent_id="test-agent",
                name="duplicate",
                description="Second",
            )

    def test_frontmatter_is_valid(self, tmp_path: Path) -> None:
        path = create_entry(
            base_path=tmp_path,
            agent_id="test-agent",
            name="validated-entry",
            description="A validated entry",
            tags=["test"],
            category="atlas",
            confidence="working",
            status="active",
        )
        result = validate_file(path)
        assert result.is_valid, f"Validation errors: {result.errors}"

    def test_body_content_present(self, tmp_path: Path) -> None:
        path = create_entry(
            base_path=tmp_path,
            agent_id="test-agent",
            name="with-body",
            description="Test",
            body="## Section\nBody content here.",
        )
        text = path.read_text(encoding="utf-8")
        assert "Body content here." in text

    def test_no_body(self, tmp_path: Path) -> None:
        path = create_entry(
            base_path=tmp_path,
            agent_id="test-agent",
            name="no-body",
            description="No body entry",
        )
        text = path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        assert body.strip() == ""

    def test_invalid_confidence_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Invalid confidence"):
            create_entry(
                base_path=tmp_path,
                agent_id="a",
                name="bad",
                description="T",
                confidence="invalid",
            )

    def test_invalid_category_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Invalid category"):
            create_entry(
                base_path=tmp_path,
                agent_id="a",
                name="bad",
                description="T",
                category="invalid",
            )


class TestUpdateEntry:
    def _create_test_entry(self, tmp_path: Path, **kwargs: object) -> Path:
        """Helper to create a test entry for update tests."""
        defaults = {
            "base_path": tmp_path,
            "agent_id": "test-agent",
            "name": "update-test",
            "description": "An entry to update",
            "body": "Original body content.",
            "tags": ["original"],
            "confidence": "working",
            "status": "active",
        }
        defaults.update(kwargs)
        return create_entry(**defaults)  # type: ignore[arg-type]

    def test_bumps_updated_timestamp(self, tmp_path: Path) -> None:
        path = self._create_test_entry(tmp_path)
        text_before = path.read_text(encoding="utf-8")
        fm_before, _ = parse_frontmatter(text_before)
        original_updated = fm_before.updated

        # Patch _now_iso to return a distinct timestamp
        with patch(
            "agent_memory.writer._now_iso",
            return_value="2099-01-01T00:00:00+00:00",
        ):
            update_entry(path, body="Updated body")

        text_after = path.read_text(encoding="utf-8")
        fm_after, _ = parse_frontmatter(text_after)
        assert fm_after.updated == "2099-01-01T00:00:00+00:00"
        assert fm_after.updated != original_updated

    def test_preserves_created_and_author(self, tmp_path: Path) -> None:
        path = self._create_test_entry(tmp_path)
        text_before = path.read_text(encoding="utf-8")
        fm_before, _ = parse_frontmatter(text_before)

        update_entry(path, body="New body")

        text_after = path.read_text(encoding="utf-8")
        fm_after, _ = parse_frontmatter(text_after)
        assert fm_after.created == fm_before.created
        assert fm_after.author == fm_before.author

    def test_replaces_body(self, tmp_path: Path) -> None:
        path = self._create_test_entry(tmp_path)
        update_entry(path, body="Replaced body content.")
        text = path.read_text(encoding="utf-8")
        assert "Replaced body content." in text
        assert "Original body content." not in text

    def test_replaces_tags(self, tmp_path: Path) -> None:
        path = self._create_test_entry(tmp_path)
        update_entry(path, tags=["new-tag-1", "new-tag-2"])
        text = path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        assert fm.tags == ["new-tag-1", "new-tag-2"]
        assert "original" not in fm.tags

    def test_appends_tags_without_duplicates(self, tmp_path: Path) -> None:
        path = self._create_test_entry(tmp_path)
        update_entry(path, add_tags=["original", "new-tag"])
        text = path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        assert "original" in fm.tags
        assert "new-tag" in fm.tags
        # "original" should appear only once
        assert fm.tags.count("original") == 1

    def test_updates_confidence(self, tmp_path: Path) -> None:
        path = self._create_test_entry(tmp_path)
        update_entry(path, confidence="established")
        text = path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        assert fm.confidence == "established"

    def test_updates_status(self, tmp_path: Path) -> None:
        path = self._create_test_entry(tmp_path)
        update_entry(path, status="archived")
        text = path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        assert fm.status == "archived"

    def test_raises_file_not_found_for_missing(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="File not found"):
            update_entry(tmp_path / "nonexistent.md", body="anything")

    def test_no_change_update_still_bumps_timestamp(self, tmp_path: Path) -> None:
        path = self._create_test_entry(tmp_path)
        text_before = path.read_text(encoding="utf-8")
        fm_before, _ = parse_frontmatter(text_before)

        with patch(
            "agent_memory.writer._now_iso",
            return_value="2099-12-31T23:59:59+00:00",
        ):
            update_entry(path)  # No changes provided

        text_after = path.read_text(encoding="utf-8")
        fm_after, _ = parse_frontmatter(text_after)
        assert fm_after.updated == "2099-12-31T23:59:59+00:00"

    def test_invalid_confidence_raises(self, tmp_path: Path) -> None:
        path = self._create_test_entry(tmp_path)
        with pytest.raises(ValueError, match="Invalid confidence"):
            update_entry(path, confidence="bogus")

    def test_invalid_status_raises(self, tmp_path: Path) -> None:
        path = self._create_test_entry(tmp_path)
        with pytest.raises(ValueError, match="Invalid status"):
            update_entry(path, status="bogus")

    def test_preserves_category(self, tmp_path: Path) -> None:
        path = self._create_test_entry(tmp_path, category="atlas")
        update_entry(path, body="Updated body")
        text = path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        assert fm.category == "atlas"
