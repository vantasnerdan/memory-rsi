"""Ripgrep subprocess execution and JSON output parsing.

Handles building the rg command, executing it, and parsing
the JSON-lines output into structured GrepMatch objects.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from agent_memory.parser import parse_frontmatter


def find_rg() -> str:
    """Locate the ripgrep binary, raising if not found."""
    rg_path = shutil.which("rg")
    if rg_path is None:
        raise FileNotFoundError(
            "ripgrep (rg) is not installed. "
            "Install it: apt install ripgrep  or  brew install ripgrep"
        )
    return rg_path


def build_rg_command(
    rg_path: str,
    pattern: str,
    search_paths: list[str],
    context_before: int = 0,
    context_after: int = 0,
    context: int = 0,
    case_insensitive: bool = False,
    fixed_strings: bool = False,
) -> list[str]:
    """Build the ripgrep command with arguments."""
    cmd = [rg_path, "--json", "--glob", "*.md"]

    if case_insensitive:
        cmd.append("-i")

    if fixed_strings:
        cmd.append("--fixed-strings")

    if context > 0:
        cmd.extend(["-C", str(context)])
    else:
        if context_before > 0:
            cmd.extend(["-B", str(context_before)])
        if context_after > 0:
            cmd.extend(["-A", str(context_after)])

    cmd.append(pattern)
    cmd.extend(search_paths)
    return cmd


def run_rg(cmd: list[str]) -> str | None:
    """Execute ripgrep and return stdout, or None if no matches.

    Raises RuntimeError on ripgrep errors (exit code 2).
    """
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=30
    )
    # ripgrep exit codes: 0=matches, 1=no matches, 2=error
    if result.returncode == 2:
        raise RuntimeError(f"ripgrep error: {result.stderr.strip()}")
    if result.returncode == 1 or not result.stdout.strip():
        return None
    return result.stdout


def parse_rg_json(
    stdout: str,
) -> dict[str, list[tuple[int, str, bool]]]:
    """Parse ripgrep JSON output into per-file match groups.

    Returns dict mapping file paths to lists of
    (line_number, line_text, is_context) tuples.
    """
    file_matches: dict[str, list[tuple[int, str, bool]]] = {}

    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        rtype = record.get("type")
        if rtype not in ("match", "context"):
            continue

        data = record.get("data", {})
        file_path = data.get("path", {}).get("text", "")
        if not file_path:
            continue

        line_number = data.get("line_number", 0)
        line_text = data.get("lines", {}).get("text", "").rstrip("\n")

        if file_path not in file_matches:
            file_matches[file_path] = []
        file_matches[file_path].append(
            (line_number, line_text, rtype == "context")
        )

    return file_matches


def load_frontmatter_summary(file_path: str) -> dict:
    """Load frontmatter from a file and return a summary dict."""
    try:
        text = Path(file_path).read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        return {
            "description": fm.description,
            "author": fm.author,
            "tags": fm.tags,
            "confidence": fm.confidence,
            "category": fm.category,
            "status": fm.status,
        }
    except (ValueError, OSError):
        return {}
