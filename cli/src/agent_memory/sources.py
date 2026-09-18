"""Multi-source file discovery for agent-memory.

Enumerates markdown files from additional configured source directories
(rules, skills, etc.). Handles files with or without YAML frontmatter.
Sources configured in ~/.config/agent-memory/config.yaml repos.additional.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from agent_memory.config import get_additional_repos, load_config
from agent_memory.parser import Frontmatter, parse_frontmatter

logger = logging.getLogger(__name__)


@dataclass
class SourceFile:
    """A file discovered from a registered source."""

    path: Path
    rel_path: str
    source_label: str
    source_root: Path


@dataclass
class SourceEntry:
    """A source file with parsed metadata and body text."""

    file: SourceFile
    frontmatter: Frontmatter
    body: str


def get_configured_sources() -> list[dict]:
    """Load additional sources from config (path, label, read_only)."""
    config = load_config()
    return get_additional_repos(config)


def enumerate_source_files(
    source_path: Path,
    source_label: str,
) -> list[SourceFile]:
    """Enumerate .md files from a source directory (rglob, underscore excluded)."""
    if not source_path.is_dir():
        logger.debug("Source path not a directory: %s", source_path)
        return []

    files: list[SourceFile] = []
    for md_file in sorted(source_path.rglob("*.md")):
        try:
            rel = md_file.relative_to(source_path)
        except ValueError:
            continue
        if any(p.startswith("_") for p in rel.parts):
            continue
        files.append(SourceFile(
            path=md_file,
            rel_path=str(rel),
            source_label=source_label,
            source_root=source_path,
        ))
    return files


def parse_source_file(source_file: SourceFile) -> SourceEntry | None:
    """Parse a source file, creating synthetic frontmatter when absent."""
    try:
        text = source_file.path.read_text(encoding="utf-8")
    except OSError:
        logger.debug("Cannot read file: %s", source_file.path)
        return None

    try:
        fm, body = parse_frontmatter(text)
    except ValueError:
        fm, body = _synthetic_frontmatter(source_file, text)

    return SourceEntry(file=source_file, frontmatter=fm, body=body)


def _synthetic_frontmatter(
    source_file: SourceFile,
    text: str,
) -> tuple[Frontmatter, str]:
    """Create synthetic frontmatter from first heading or content line."""
    description = ""
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            description = stripped[2:].strip()
        elif stripped.startswith("## "):
            description = stripped[3:].strip()
        else:
            description = stripped[:120]
        break

    stem = source_file.path.stem
    raw: dict = {
        "description": description,
        "source_type": source_file.source_label,
    }
    fm = Frontmatter(
        description=description,
        tags=[source_file.source_label, stem],
        category=source_file.source_label,
        raw=raw,
    )
    return fm, text


def collect_all_source_files() -> list[SourceEntry]:
    """Collect and parse files from all configured additional sources."""
    sources = get_configured_sources()
    entries: list[SourceEntry] = []
    for source in sources:
        source_path = Path(source["path"])
        label = source.get("label", "") or source_path.name
        files = enumerate_source_files(source_path, label)
        for sf in files:
            entry = parse_source_file(sf)
            if entry is not None:
                entries.append(entry)
    return entries
