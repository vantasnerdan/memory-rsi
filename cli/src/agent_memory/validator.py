"""Schema validation for memory entry frontmatter and section format.

Validates against the issue #5 schema standard:
- Required: description, author, created, updated
- Optional enums: confidence, category, status
- Section description convention: prose first line preferred (list-first is warning)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agent_memory.config import load_config
from agent_memory.parser import Frontmatter, parse_frontmatter, parse_sections

REQUIRED_FIELDS = ("description", "author", "created", "updated")

CONFIDENCE_VALUES = ("established", "working", "exploratory")
CATEGORY_VALUES = ("atlas", "efforts", "calendar", "moc")
STATUS_VALUES = ("active", "archived", "draft")
CATEGORY_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
LOCAL_CATEGORY_CONFIG_FILES = (
    ".agent-memory.yaml",
    ".agent-memory.yml",
    ".agent-memory/config.yaml",
)


@dataclass
class ValidationResult:
    """Result of validating a memory entry."""

    path: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks_passed: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


def is_valid_category_name(value: str) -> bool:
    """Return true when ``value`` is safe for category frontmatter and dirs."""
    return bool(CATEGORY_NAME_PATTERN.fullmatch(value))


def _append_category_values(target: list[str], raw: object) -> None:
    """Append valid configured category values, preserving order."""
    if isinstance(raw, dict):
        values = raw.keys()
    elif isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        return

    for value in values:
        if not isinstance(value, str):
            continue
        category = value.strip()
        if category and is_valid_category_name(category) and category not in target:
            target.append(category)


def _append_config_categories(target: list[str], config: object) -> None:
    """Append categories from supported config shapes."""
    if not isinstance(config, dict):
        return

    _append_category_values(target, config.get("categories"))
    _append_category_values(target, config.get("category_types"))

    schema = config.get("schema")
    if isinstance(schema, dict):
        _append_category_values(target, schema.get("categories"))
        _append_category_values(target, schema.get("category_types"))


def _local_category_config_paths(context_path: Path | None) -> list[Path]:
    """Find repo-local category config files by walking up from context."""
    if context_path is None:
        return []

    start = Path(context_path)
    if start.is_file() or start.suffix == ".md":
        start = start.parent
    start = start.resolve()

    paths: list[Path] = []
    for directory in (start, *start.parents):
        for config_name in LOCAL_CATEGORY_CONFIG_FILES:
            candidate = directory / config_name
            if candidate.is_file():
                paths.append(candidate)
    return paths


def _load_local_config(path: Path) -> dict:
    """Load a repo-local category config file."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if isinstance(data, dict):
        return data
    return {}


def category_values(context_path: Path | None = None) -> tuple[str, ...]:
    """Return built-in plus configured category values.

    Categories may be added in user config, repo-local config, or the
    comma-separated AGENT_MEMORY_CATEGORIES environment variable. Invalid
    configured names are ignored here; a requested invalid name still produces
    an explicit validation error.
    """
    values = list(CATEGORY_VALUES)

    _append_config_categories(values, load_config())

    env_categories = os.environ.get("AGENT_MEMORY_CATEGORIES", "")
    if env_categories.strip():
        _append_category_values(values, env_categories.split(","))

    for config_path in _local_category_config_paths(context_path):
        _append_config_categories(values, _load_local_config(config_path))

    return tuple(values)


def validate_category_value(
    value: str,
    context_path: Path | None = None,
) -> tuple[bool, str]:
    """Validate a category value and return ``(ok, message)``."""
    if not value:
        return True, ""
    if not is_valid_category_name(value):
        return (
            False,
            f"Invalid category '{value}' (must match "
            f"{CATEGORY_NAME_PATTERN.pattern})",
        )

    allowed = category_values(context_path)
    if value in allowed:
        return True, ""

    allowed_str = ", ".join(allowed)
    return False, f"Invalid category '{value}' (allowed: {allowed_str})"


def _check_frontmatter_exists(text: str, result: ValidationResult) -> Frontmatter | None:
    """Check that frontmatter exists and is parseable."""
    try:
        fm, _ = parse_frontmatter(text)
        result.checks_passed.append("Frontmatter exists")
        return fm
    except ValueError as e:
        result.errors.append(f"Frontmatter: {e}")
        return None


def _check_required_fields(fm: Frontmatter, result: ValidationResult) -> None:
    """Check that all required fields are present and non-empty."""
    for field_name in REQUIRED_FIELDS:
        value = getattr(fm, field_name, "")
        if value:
            result.checks_passed.append(f"Required field: {field_name}")
        else:
            result.errors.append(f"Missing required field: {field_name}")


def _check_enum_fields(
    fm: Frontmatter,
    result: ValidationResult,
    context_path: Path,
) -> None:
    """Validate enum fields against allowed values."""
    enum_checks = [
        ("confidence", fm.confidence, CONFIDENCE_VALUES),
        ("status", fm.status, STATUS_VALUES),
    ]
    for name, value, allowed in enum_checks:
        if not value:
            continue
        if value in allowed:
            result.checks_passed.append(f"Valid {name}: {value}")
        else:
            allowed_str = ", ".join(allowed)
            result.errors.append(
                f"Invalid {name} '{value}' (allowed: {allowed_str})"
            )

    ok, message = validate_category_value(fm.category, context_path)
    if not fm.category:
        return
    if ok:
        result.checks_passed.append(f"Valid category: {fm.category}")
    else:
        result.errors.append(message)


def _check_optional_fields(fm: Frontmatter, result: ValidationResult) -> None:
    """Warn about missing optional fields."""
    optional = [
        ("tags", fm.tags),
        ("confidence", fm.confidence),
        ("category", fm.category),
        ("status", fm.status),
    ]
    for name, value in optional:
        if not value:
            result.warnings.append(f"Optional field missing: {name}")


def _is_description_line(line: str) -> bool:
    """Check if a line qualifies as a section description.

    A valid description is non-empty, does not start with
    #, backtick, |, or whitespace-dash (code/table/list/header).
    """
    stripped = line.strip()
    if not stripped:
        return False
    if stripped[0] in ("#", "`", "|"):
        return False
    if stripped.startswith("- ") or stripped.startswith("* "):
        return False
    return True


def _is_list_line(line: str) -> bool:
    """Check if a line is a list item (- or * prefix)."""
    stripped = line.strip()
    return stripped.startswith("- ") or stripped.startswith("* ")


def _check_section_descriptions(text: str, result: ValidationResult) -> None:
    """Validate that each ## section has a description line.

    Sections starting with list items produce a warning (not an error)
    because lists are a legitimate content pattern. Sections starting
    with code fences, tables, or headers remain errors.
    """
    try:
        _, body = parse_frontmatter(text)
    except ValueError:
        body = text

    sections = parse_sections(body)
    all_valid = True
    for section in sections:
        content_lines = section.content.split("\n")
        # Skip the header line itself
        found_description = False
        for line in content_lines[1:]:
            stripped = line.strip()
            if not stripped:
                continue
            if _is_description_line(line):
                found_description = True
                break
            elif _is_list_line(line):
                result.warnings.append(
                    f"Section '{section.title}': consider adding a prose "
                    f"description before the list for better TOC readability"
                )
                found_description = True  # not an error, just a warning
                break
            else:
                result.errors.append(
                    f"Section '{section.title}': "
                    f"first content line is not plain prose"
                )
                all_valid = False
                break
        if not found_description and len(content_lines) > 1:
            has_content = any(ln.strip() for ln in content_lines[1:])
            if not has_content:
                result.errors.append(
                    f"Section '{section.title}': missing description line"
                )
                all_valid = False

    if all_valid and sections:
        result.checks_passed.append("Section descriptions valid")


def validate_file(path: Path) -> ValidationResult:
    """Validate a single memory entry file."""
    result = ValidationResult(path=str(path))

    if not path.exists():
        result.errors.append(f"File not found: {path}")
        return result

    if not path.is_file():
        result.errors.append(f"Not a file: {path}")
        return result

    text = path.read_text(encoding="utf-8")
    fm = _check_frontmatter_exists(text, result)
    if fm is None:
        return result

    _check_required_fields(fm, result)
    _check_enum_fields(fm, result, path)
    _check_optional_fields(fm, result)
    _check_section_descriptions(text, result)

    return result


def validate_directory(path: Path) -> list[ValidationResult]:
    """Validate all .md files in a directory (recursive)."""
    if not path.exists():
        raise FileNotFoundError(f"Directory not found: {path}")
    if not path.is_dir():
        raise ValueError(f"Not a directory: {path}")

    results = []
    for md_file in sorted(path.rglob("*.md")):
        if any(p.startswith("_") for p in md_file.relative_to(path).parts):
            continue
        results.append(validate_file(md_file))
    return results
