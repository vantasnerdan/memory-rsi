"""Frontmatter and section parsing for memory entries.

Handles YAML frontmatter extraction and markdown section parsing
with progressive disclosure semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Frontmatter:
    """Parsed YAML frontmatter from a memory entry."""

    description: str = ""
    author: str = ""
    created: str = ""
    updated: str = ""
    tags: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    confidence: str = ""
    category: str = ""
    status: str = ""
    supersedes: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Section:
    """A single ## section from a memory entry."""

    title: str
    description: str
    content: str
    line_number: int


def parse_frontmatter(text: str) -> tuple[Frontmatter, str]:
    """Parse YAML frontmatter from markdown text.

    Returns (Frontmatter, body) where body is the content after frontmatter.
    Raises ValueError if frontmatter is malformed.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("File does not start with '---' frontmatter delimiter")

    end_index = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_index = i
            break

    if end_index is None:
        raise ValueError("Frontmatter missing closing '---' delimiter")

    yaml_text = "\n".join(lines[1:end_index])
    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in frontmatter: {e}") from e

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"Frontmatter must be a YAML mapping, got {type(raw).__name__}")

    fm = Frontmatter(
        description=str(raw.get("description", "")),
        author=str(raw.get("author", "")),
        created=str(raw.get("created", "")),
        updated=str(raw.get("updated", "")),
        tags=raw.get("tags", []) or [],
        related=raw.get("related", []) or [],
        confidence=str(raw.get("confidence", "")),
        category=str(raw.get("category", "")),
        status=str(raw.get("status", "")),
        supersedes=str(raw.get("supersedes", "")),
        raw=raw,
    )

    body = "\n".join(lines[end_index + 1 :])
    return fm, body


def parse_sections(body: str) -> list[Section]:
    """Parse ## sections from markdown body text.

    Only matches ## headers (not # or ###).
    Description is the first non-empty line after the header.
    """
    sections: list[Section] = []
    lines = body.split("\n")
    current: Section | None = None
    content_lines: list[str] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            if current is not None:
                current.content = "\n".join(content_lines).strip()
                sections.append(current)
            title = stripped[3:].strip()
            current = Section(
                title=title, description="", content="", line_number=i + 1
            )
            content_lines = [line]
        elif current is not None:
            content_lines.append(line)
            if not current.description and stripped:
                current.description = stripped

    if current is not None:
        current.content = "\n".join(content_lines).strip()
        sections.append(current)

    return sections


def extract_section(body: str, query: str) -> list[Section]:
    """Extract sections matching a query (case-insensitive partial match).

    Returns all matching sections. Mirrors the bash script's substring matching.
    """
    sections = parse_sections(body)
    query_lower = query.lower()
    return [s for s in sections if query_lower in s.title.lower()]


def read_entry(path: Path) -> tuple[Frontmatter, str]:
    """Read a memory entry file and return parsed frontmatter and body."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")

    text = path.read_text(encoding="utf-8")
    return parse_frontmatter(text)


def resolve_md_path(path_str: str) -> Path:
    """Resolve a path, auto-appending .md extension if needed."""
    path = Path(path_str)
    if path.exists():
        return path
    if not path.suffix and path.with_suffix(".md").exists():
        return path.with_suffix(".md")
    return path
