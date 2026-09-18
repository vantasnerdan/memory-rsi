"""Frontmatter generation and file writing for memory entries.

Handles creation and update of memory entry files with valid
YAML frontmatter, enum validation, and kebab-case naming.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent_memory.parser import parse_frontmatter
from agent_memory.validator import (
    CONFIDENCE_VALUES,
    STATUS_VALUES,
    validate_category_value,
)


def to_kebab_case(name: str) -> str:
    """Convert entry name to kebab-case filename.

    'My Entry Name' -> 'my-entry-name'
    'NaN_Policy' -> 'nan-policy'
    Strips special chars, collapses hyphens.
    """
    result = name.lower()
    # Replace underscores and spaces with hyphens
    result = result.replace("_", "-").replace(" ", "-")
    # Strip non-alphanumeric except hyphens
    result = re.sub(r"[^a-z0-9-]", "", result)
    # Collapse multiple hyphens into one
    result = re.sub(r"-+", "-", result)
    # Strip leading/trailing hyphens
    result = result.strip("-")
    return result


def _validate_enum(name: str, value: str, allowed: tuple[str, ...]) -> None:
    """Validate that a value is in the allowed set, raising ValueError if not."""
    if value and value not in allowed:
        allowed_str = ", ".join(allowed)
        raise ValueError(
            f"Invalid {name} '{value}' (allowed: {allowed_str})"
        )


def _validate_category(value: str, context_path: Path | None = None) -> None:
    """Validate a configured category value, raising ValueError if invalid."""
    ok, message = validate_category_value(value, context_path)
    if not ok:
        raise ValueError(message)


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def generate_frontmatter(
    description: str,
    author: str,
    tags: list[str] | None = None,
    category: str = "",
    confidence: str = "",
    status: str = "active",
    related: list[str] | None = None,
    category_context: Path | None = None,
) -> str:
    """Generate YAML frontmatter string with --- delimiters.

    Auto-sets created and updated to current ISO-8601 UTC timestamp.
    Validates enum values before generating.

    Raises ValueError if confidence, category, or status values are invalid.
    """
    _validate_enum("confidence", confidence, CONFIDENCE_VALUES)
    _validate_category(category, category_context)
    _validate_enum("status", status, STATUS_VALUES)

    now = _now_iso()

    # Build dict in logical field order
    data: dict[str, str | list[str]] = {
        "description": description,
        "author": author,
        "created": now,
        "updated": now,
    }

    if tags:
        data["tags"] = tags
    if category:
        data["category"] = category
    if confidence:
        data["confidence"] = confidence
    if status:
        data["status"] = status
    if related:
        data["related"] = related

    yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False)
    return f"---\n{yaml_str}---\n"


def create_entry(
    base_path: Path,
    agent_id: str,
    name: str,
    description: str,
    body: str = "",
    tags: list[str] | None = None,
    category: str = "",
    confidence: str = "",
    status: str = "active",
    shared: bool = False,
) -> Path:
    """Create a new memory entry file.

    Path: {base_path}/{agent_id}/{category}/{kebab-name}.md
    Or shared: {base_path}/shared/{category}/{kebab-name}.md

    Auto-creates directories if they don't exist.
    Raises FileExistsError if entry already exists.
    Raises ValueError if enum values are invalid.
    Returns the created file path.
    """
    _validate_enum("confidence", confidence, CONFIDENCE_VALUES)
    _validate_category(category, base_path)
    _validate_enum("status", status, STATUS_VALUES)

    filename = to_kebab_case(name) + ".md"

    if shared:
        owner_dir = base_path / "shared"
    elif base_path.name == agent_id:
        # Avoid double-nesting when --base already points to agent dir
        # e.g. --base memory/<agent-id> → don't append <agent-id> again
        owner_dir = base_path
    else:
        owner_dir = base_path / agent_id

    if category:
        entry_dir = owner_dir / category
    else:
        entry_dir = owner_dir

    file_path = entry_dir / filename

    if file_path.exists():
        raise FileExistsError(f"Entry already exists: {file_path}")

    entry_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = generate_frontmatter(
        description=description,
        author=agent_id,
        tags=tags,
        category=category,
        confidence=confidence,
        status=status,
        category_context=base_path,
    )

    content = frontmatter
    if body:
        content += "\n" + body + "\n"

    file_path.write_text(content, encoding="utf-8")
    return file_path


def update_entry(
    file_path: Path,
    body: str | None = None,
    tags: list[str] | None = None,
    add_tags: list[str] | None = None,
    confidence: str | None = None,
    status: str | None = None,
) -> Path:
    """Update an existing memory entry.

    - Updates body content if provided
    - Replaces tags if tags provided, appends if add_tags provided
    - Updates confidence/status if provided
    - Bumps 'updated' timestamp
    - Preserves 'created' and 'author'

    Raises FileNotFoundError if file doesn't exist.
    Raises ValueError if confidence or status values are invalid.
    Returns the updated file path.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if confidence is not None:
        _validate_enum("confidence", confidence, CONFIDENCE_VALUES)
    if status is not None:
        _validate_enum("status", status, STATUS_VALUES)

    text = file_path.read_text(encoding="utf-8")
    fm, existing_body = parse_frontmatter(text)

    # Update tags
    if tags is not None:
        fm.tags = tags
    elif add_tags is not None:
        # Append without duplicates, preserving order
        existing_set = set(fm.tags)
        for tag in add_tags:
            if tag not in existing_set:
                fm.tags.append(tag)
                existing_set.add(tag)

    # Update confidence if provided
    if confidence is not None:
        fm.confidence = confidence

    # Update status if provided
    if status is not None:
        fm.status = status

    # Bump updated timestamp, preserve created
    fm.updated = _now_iso()

    # Rebuild frontmatter dict in logical order
    data: dict[str, str | list[str]] = {
        "description": fm.description,
        "author": fm.author,
        "created": fm.created,
        "updated": fm.updated,
    }

    if fm.tags:
        data["tags"] = fm.tags
    if fm.category:
        data["category"] = fm.category
    if fm.confidence:
        data["confidence"] = fm.confidence
    if fm.status:
        data["status"] = fm.status
    if fm.related:
        data["related"] = fm.related
    if fm.supersedes:
        data["supersedes"] = fm.supersedes

    yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False)
    frontmatter_str = f"---\n{yaml_str}---\n"

    # Use new body if provided, otherwise preserve existing
    final_body = body if body is not None else existing_body.lstrip("\n")

    content = frontmatter_str
    if final_body:
        content += "\n" + final_body
        if not final_body.endswith("\n"):
            content += "\n"

    file_path.write_text(content, encoding="utf-8")
    return file_path
