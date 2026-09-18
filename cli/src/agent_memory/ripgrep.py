"""Ripgrep-based exact/regex search over memory entries.

Shells out to `rg` for pattern matching and enriches results
with frontmatter metadata from matched files. Complements
BM25 relevance search with exact pattern and regex matching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_memory.rg_parser import (
    build_rg_command,
    find_rg,
    load_frontmatter_summary,
    parse_rg_json,
    run_rg,
)


@dataclass
class GrepMatch:
    """A single matching line from ripgrep output."""

    line_number: int
    line_text: str
    is_context: bool = False


@dataclass
class GrepFileResult:
    """All matches in a single file with frontmatter metadata."""

    path: str
    matches: list[GrepMatch] = field(default_factory=list)
    match_count: int = 0
    frontmatter: dict = field(default_factory=dict)


def _resolve_scope_paths(
    base_path: Path, scope: str, agent_id: str
) -> list[str]:
    """Convert scope to directory paths for ripgrep search."""
    if scope == "own":
        if not agent_id:
            raise ValueError(
                "Agent ID required for --scope own. "
                "Set AGENT_ID environment variable."
            )
        return [str(base_path / agent_id)]
    elif scope == "shared":
        return [str(base_path / "shared")]
    elif scope.startswith("agent:"):
        target_id = scope[6:]
        if not target_id:
            raise ValueError(
                "Agent ID required after 'agent:' prefix. "
                "Example: --scope agent:other-agent"
            )
        return [str(base_path / target_id)]
    elif scope == "all":
        return [str(base_path)]
    else:
        raise ValueError(
            f"Invalid scope '{scope}'. "
            "Use: all, own, shared, or agent:<id>"
        )


def grep(
    pattern: str,
    base_path: Path,
    scope: str = "all",
    agent_id: str = "",
    context_before: int = 0,
    context_after: int = 0,
    context: int = 0,
    case_insensitive: bool = False,
    fixed_strings: bool = False,
    tag: str | None = None,
    links_to: str | None = None,
) -> list[GrepFileResult]:
    """Run ripgrep search over memory entries.

    If tag is provided, overrides pattern to search for that tag
    in frontmatter. If links_to is provided, overrides pattern to
    search for [[links_to]] wiki-link references.

    Returns a list of GrepFileResult with frontmatter metadata.
    """
    rg_path = find_rg()

    if tag is not None:
        pattern = f"tags:.*{tag}"
        case_insensitive = True
    elif links_to is not None:
        pattern = f"\\[\\[{links_to}\\]\\]"
        fixed_strings = False

    if not pattern:
        return []

    search_paths = _resolve_scope_paths(base_path, scope, agent_id)

    cmd = build_rg_command(
        rg_path=rg_path,
        pattern=pattern,
        search_paths=search_paths,
        context_before=context_before,
        context_after=context_after,
        context=context,
        case_insensitive=case_insensitive,
        fixed_strings=fixed_strings,
    )

    stdout = run_rg(cmd)
    if stdout is None:
        return []

    file_matches = parse_rg_json(stdout)

    results: list[GrepFileResult] = []
    for abs_path, raw_matches in file_matches.items():
        try:
            rel_path = str(Path(abs_path).relative_to(base_path))
        except ValueError:
            rel_path = abs_path

        matches = [
            GrepMatch(
                line_number=ln, line_text=lt, is_context=ic
            )
            for ln, lt, ic in raw_matches
        ]
        match_count = sum(1 for m in matches if not m.is_context)
        fm_summary = load_frontmatter_summary(abs_path)

        results.append(GrepFileResult(
            path=rel_path,
            matches=matches,
            match_count=match_count,
            frontmatter=fm_summary,
        ))

    results.sort(key=lambda r: r.path)
    return results
