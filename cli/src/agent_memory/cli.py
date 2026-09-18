"""CLI entry point for agent-memory progressive disclosure tool.

Provides subcommands: ls, toc, section, validate, init,
search, grep, new, update, clone, sync, log.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import time
from pathlib import Path

import click

from agent_memory import __version__
from agent_memory.config import get_agent_id, resolve_base_path, resolve_file_path
from agent_memory.config_cli import config_group
from agent_memory.git_ops import (
    BranchMismatchError,
    commit_and_push,
    git_repo_root,
)
from agent_memory.logging import log_cli_command
from agent_memory.parser import (
    extract_section,
    parse_frontmatter,
    parse_sections,
    resolve_md_path,
)
from agent_memory.ripgrep import grep as ripgrep_search
from agent_memory.search import search as bm25_search
from agent_memory.validator import validate_directory, validate_file
from agent_memory.writer import create_entry, update_entry

STANDARD_DIRS = ("atlas", "efforts", "calendar", "moc")


class _SafeEncoder(json.JSONEncoder):
    """JSON encoder that handles datetime objects from YAML frontmatter."""

    def default(self, obj: object) -> object:
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        return super().default(obj)


def _resolve_base(base: str | None) -> Path:
    """Resolve --base flag via config module priority chain.

    Priority: CLI flag > AGENT_MEMORY_PATH env var > config file
              > cwd auto-detect > "memory" fallback.
    """
    return Path(resolve_base_path(base))


def _resolve_path_with_base(path: str, base: str | None) -> Path:
    """Resolve a file/directory path relative to --base when it is relative.

    If path is absolute, return it as-is. Otherwise, prepend the resolved base.
    """
    return Path(resolve_file_path(path, base))


def _suggest_directories(base: Path) -> list[str]:
    """List available subdirectories for error suggestions."""
    if not base.exists():
        parent = base.parent
        if parent.exists():
            return [str(d.relative_to(parent)) for d in sorted(parent.iterdir()) if d.is_dir()]
        return []
    return [str(d.relative_to(base)) for d in sorted(base.iterdir()) if d.is_dir()]


def _suggest_files(directory: Path) -> list[str]:
    """List available .md files in a directory for error suggestions."""
    if not directory.exists():
        return []
    return [f.name for f in sorted(directory.iterdir()) if f.is_file() and f.suffix == ".md"]


@click.group()
@click.version_option(version=__version__, prog_name="memory")
@click.option("--json-output", "use_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def cli(ctx: click.Context, use_json: bool) -> None:
    """Progressive disclosure memory management for autonomous agents."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = use_json


cli.add_command(config_group)


def _log_end(cmd: str, start: float, result_count: int | None = None) -> None:
    """Log successful command completion with duration."""
    duration_ms = (time.monotonic() - start) * 1000
    log_cli_command(cmd=cmd, duration_ms=duration_ms, result_count=result_count)


def _log_error(cmd: str, start: float, exc: Exception) -> None:
    """Log command error with duration."""
    duration_ms = (time.monotonic() - start) * 1000
    log_cli_command(
        cmd=cmd,
        duration_ms=duration_ms,
        exit_code=1,
        error=f"{type(exc).__name__}: {exc}",
    )


def _read_entry_tolerant(resolved: Path, display_path: str) -> tuple:
    """Read a markdown file, tolerating missing YAML frontmatter.

    Memory entries have frontmatter; rules and skill files may not.
    Returns (Frontmatter, body) in both cases. On OSError, exits.
    """
    from agent_memory.parser import Frontmatter as _Fm

    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as e:
        click.echo(f"Error reading {display_path}: {e}", err=True)
        sys.exit(1)

    try:
        return parse_frontmatter(text)
    except ValueError:
        fm = _Fm(description=resolved.stem, raw={"description": resolved.stem})
        return fm, text


@cli.command(name="ls")
@click.argument("path", type=click.Path(), default=".")
@click.option(
    "--base",
    default=None,
    help="Base directory (default: config/env/cwd auto-detect).",
)
@click.option(
    "--include-sources",
    is_flag=True,
    help="Also list files from configured additional sources (rules, skills).",
)
@click.pass_context
def ls_cmd(
    ctx: click.Context, path: str, base: str | None, include_sources: bool,
) -> None:
    """List memory entries with frontmatter descriptions."""
    start = time.monotonic()
    target = _resolve_path_with_base(path, base)
    use_json = ctx.obj["json"]

    if not target.exists():
        suggestions = _suggest_directories(target)
        msg = f"Directory not found: {target}"
        if suggestions:
            msg += f"\nAvailable: {', '.join(suggestions)}"
        click.echo(msg, err=True)
        sys.exit(1)

    if not target.is_dir():
        click.echo(f"Not a directory: {target}", err=True)
        sys.exit(1)

    entries = []
    md_files = sorted(f for f in target.iterdir() if f.is_file() and f.suffix == ".md")

    if not md_files:
        subdirs = _suggest_directories(target)
        if subdirs:
            if use_json:
                click.echo(json.dumps({"directories": subdirs}))
            else:
                click.echo(f"No .md files in {target}")
                click.echo("Subdirectories:")
                for d in subdirs:
                    click.echo(f"  {d}/")
        else:
            click.echo(f"No .md files in {target}")
        _log_end("ls", start)
        return

    for md_file in md_files:
        try:
            text = md_file.read_text(encoding="utf-8")
            fm, _ = parse_frontmatter(text)
            entry = {
                "file": md_file.name,
                "description": fm.description,
                "confidence": fm.confidence,
            }
            entries.append(entry)
        except (ValueError, OSError) as e:
            entries.append({
                "file": md_file.name,
                "description": f"<parse error: {e}>",
                "confidence": "",
            })

    source_entries: list[dict] = []
    if include_sources:
        source_entries = _collect_source_ls_entries()

    if use_json:
        output: dict | list = entries
        if source_entries:
            output = {"memory": entries, "sources": source_entries}
        click.echo(json.dumps(output, indent=2))
    else:
        for entry in entries:
            line = f"  {entry['file']}"
            if entry["description"]:
                line += f' -- "{entry["description"]}"'
            if entry["confidence"]:
                line += f" [{entry['confidence']}]"
            click.echo(line)
        if source_entries:
            for src_group in source_entries:
                click.echo(f"\n  [{src_group['label']}] {src_group['path']}")
                for sf in src_group["files"]:
                    line = f"    {sf['file']}"
                    if sf["description"]:
                        line += f' -- "{sf["description"]}"'
                    click.echo(line)
    _log_end("ls", start, result_count=len(entries) + sum(
        len(s["files"]) for s in source_entries
    ))


def _collect_source_ls_entries() -> list[dict]:
    """Collect ls-style entries from configured additional sources."""
    from agent_memory.sources import (
        enumerate_source_files,
        get_configured_sources,
        parse_source_file,
    )

    results: list[dict] = []
    for source in get_configured_sources():
        source_path = Path(source["path"])
        label = source.get("label", "") or source_path.name
        files = enumerate_source_files(source_path, label)
        file_entries: list[dict] = []
        for sf in files:
            entry = parse_source_file(sf)
            if entry is None:
                continue
            file_entries.append({
                "file": sf.rel_path,
                "description": entry.frontmatter.description,
            })
        if file_entries:
            results.append({
                "label": label,
                "path": str(source_path),
                "files": file_entries,
            })
    return results


@cli.command(name="toc")
@click.argument("file_path", type=click.Path())
@click.option(
    "--base",
    default=None,
    help="Base directory (default: config/env/cwd auto-detect).",
)
@click.pass_context
def toc_cmd(ctx: click.Context, file_path: str, base: str | None) -> None:
    """Show table of contents for a memory entry."""
    start = time.monotonic()
    use_json = ctx.obj["json"]
    effective_path = str(_resolve_path_with_base(file_path, base))
    resolved = resolve_md_path(effective_path)

    if not resolved.exists():
        parent = resolved.parent
        suggestions = _suggest_files(parent)
        msg = f"File not found: {file_path}"
        if suggestions:
            msg += f"\nAvailable files in {parent}:"
            for f in suggestions:
                msg += f"\n  {f}"
        click.echo(msg, err=True)
        sys.exit(1)

    fm, body = _read_entry_tolerant(resolved, file_path)

    sections = parse_sections(body)

    if use_json:
        data = {
            "file": str(resolved),
            "frontmatter": {
                "description": fm.description,
                "author": fm.author,
                "confidence": fm.confidence,
                "tags": fm.tags,
                "updated": fm.updated,
            },
            "sections": [
                {"title": s.title, "description": s.description}
                for s in sections
            ],
        }
        click.echo(json.dumps(data, indent=2))
    else:
        for section in sections:
            line = f"  {section.title}"
            if section.description:
                line += f" -- {section.description}"
            click.echo(line)
    _log_end("toc", start, result_count=len(sections))


@cli.command(name="section")
@click.argument("file_path", type=click.Path())
@click.argument("title")
@click.option(
    "--base",
    default=None,
    help="Base directory (default: config/env/cwd auto-detect).",
)
@click.pass_context
def section_cmd(
    ctx: click.Context, file_path: str, title: str, base: str | None,
) -> None:
    """Show content of a specific section (case-insensitive partial match)."""
    start = time.monotonic()
    use_json = ctx.obj["json"]
    effective_path = str(_resolve_path_with_base(file_path, base))
    resolved = resolve_md_path(effective_path)

    if not resolved.exists():
        parent = resolved.parent
        suggestions = _suggest_files(parent)
        msg = f"File not found: {file_path}"
        if suggestions:
            msg += f"\nAvailable files in {parent}:"
            for f in suggestions:
                msg += f"\n  {f}"
        click.echo(msg, err=True)
        sys.exit(1)

    fm, body = _read_entry_tolerant(resolved, file_path)

    matches = extract_section(body, title)

    if not matches:
        all_sections = parse_sections(body)
        msg = f"Section '{title}' not found in {resolved.name}"
        if all_sections:
            msg += "\nAvailable sections:"
            for s in all_sections:
                msg += f"\n  {s.title}"
        click.echo(msg, err=True)
        sys.exit(1)

    if len(matches) > 1:
        msg = f"Multiple sections match '{title}':"
        for m in matches:
            msg += f"\n  {m.title}"
        msg += "\nPlease be more specific."
        click.echo(msg, err=True)
        sys.exit(1)

    section = matches[0]
    if use_json:
        data = {
            "file": str(resolved),
            "title": section.title,
            "description": section.description,
            "content": section.content,
        }
        click.echo(json.dumps(data, indent=2))
    else:
        click.echo(section.content)
    _log_end("section", start, result_count=1)


@cli.command(name="validate")
@click.argument("path", type=click.Path())
@click.option(
    "--base",
    default=None,
    help="Base directory (default: config/env/cwd auto-detect).",
)
@click.pass_context
def validate_cmd(ctx: click.Context, path: str, base: str | None) -> None:
    """Validate frontmatter and section format of memory entries."""
    start = time.monotonic()
    use_json = ctx.obj["json"]
    target = _resolve_path_with_base(path, base)

    if not target.exists():
        click.echo(f"Path not found: {target}", err=True)
        sys.exit(1)

    if target.is_file():
        results = [validate_file(target)]
    elif target.is_dir():
        results = validate_directory(target)
    else:
        click.echo(f"Not a file or directory: {path}", err=True)
        sys.exit(1)

    if not results:
        click.echo("No .md files found to validate.")
        return

    has_errors = False
    json_output = []

    for result in results:
        if use_json:
            json_output.append({
                "path": result.path,
                "valid": result.is_valid,
                "errors": result.errors,
                "warnings": result.warnings,
                "checks_passed": result.checks_passed,
            })
        else:
            click.echo(f"\n  {result.path}")
            for check in result.checks_passed:
                click.echo(f"    [pass] {check}")
            for error in result.errors:
                click.echo(f"    [error] {error}")
            for warning in result.warnings:
                click.echo(f"    [warn] {warning}")

        if not result.is_valid:
            has_errors = True

    total = len(results)
    failed = sum(1 for r in results if not r.is_valid)

    if use_json:
        click.echo(json.dumps(json_output, indent=2))
    else:
        click.echo("")
        if has_errors:
            click.echo(f"  {failed}/{total} file(s) have errors.")
        else:
            click.echo(f"  All {total} file(s) valid.")

    _log_end("validate", start, result_count=total)

    if has_errors:
        sys.exit(1)


@cli.command(name="init")
@click.argument("agent_id")
@click.option(
    "--base",
    default=None,
    help="Base directory (default: config/env/cwd auto-detect).",
)
@click.pass_context
def init_cmd(ctx: click.Context, agent_id: str, base: str | None) -> None:
    """Create standard directory structure for a new agent."""
    start = time.monotonic()
    use_json = ctx.obj["json"]
    base_path = _resolve_base(base) / agent_id
    created = []

    for dirname in STANDARD_DIRS:
        dir_path = base_path / dirname
        if dir_path.exists():
            if not use_json:
                click.echo(f"  Already exists: {dir_path}")
            continue
        dir_path.mkdir(parents=True, exist_ok=True)
        created.append(str(dir_path))
        if not use_json:
            click.echo(f"  Created {dir_path}/")

    if use_json:
        click.echo(json.dumps({"agent_id": agent_id, "created": created}))
    _log_end("init", start, result_count=len(created))


@cli.command(name="search")
@click.argument("query")
@click.option(
    "--scope",
    default="all",
    help="Directory scope: all, own, shared, agent:<id>. Default: all.",
)
@click.option(
    "--field",
    default="content",
    type=click.Choice(["description", "tags", "content"]),
    help="Target field for ranking. Default: content (full section).",
)
@click.option("--category", default=None, help="Filter by category enum.")
@click.option("--confidence", default=None, help="Filter by confidence enum.")
@click.option("--author", default=None, help="Filter by author.")
@click.option("--status", default=None, help="Filter by status enum.")
@click.option("--tag", default=None, help="Filter by tag.")
@click.option(
    "--limit",
    default=10,
    type=int,
    help="Maximum results. Default: 10.",
)
@click.option(
    "--base",
    default=None,
    help="Base directory for memory entries (default: config/env/cwd auto-detect).",
)
@click.option(
    "--no-cache",
    is_flag=True,
    help="Bypass SQLite index cache, read files directly.",
)
@click.option(
    "--include-sources",
    is_flag=True,
    help="Also search configured additional sources (rules, skills).",
)
@click.pass_context
def search_cmd(
    ctx: click.Context,
    query: str,
    scope: str,
    field: str,
    category: str | None,
    confidence: str | None,
    author: str | None,
    status: str | None,
    tag: str | None,
    limit: int,
    base: str | None,
    no_cache: bool,
    include_sources: bool,
) -> None:
    """BM25 relevance-ranked search over memory sections."""
    start = time.monotonic()
    use_json = ctx.obj["json"]
    base_path = _resolve_base(base)

    if not base_path.exists():
        click.echo(f"Base directory not found: {base_path}", err=True)
        sys.exit(1)

    agent_id = get_agent_id() or ""

    filters: dict[str, str | None] = {}
    if category is not None:
        filters["category"] = category
    if confidence is not None:
        filters["confidence"] = confidence
    if author is not None:
        filters["author"] = author
    if status is not None:
        filters["status"] = status
    if tag is not None:
        filters["tags"] = tag

    try:
        results = bm25_search(
            query=query,
            base_path=base_path,
            scope=scope,
            agent_id=agent_id,
            field=field,
            limit=limit,
            no_cache=no_cache,
            include_sources=include_sources,
            **filters,
        )
    except ValueError as e:
        click.echo(f"Search error: {e}", err=True)
        sys.exit(1)

    if use_json:
        json_results = [
            {
                "rank": r.rank,
                "path": r.path,
                "section": r.section,
                "section_description": r.section_description,
                "score": r.score,
                "file_frontmatter": r.file_frontmatter,
                "snippet": r.snippet,
                "source": r.source,
            }
            for r in results
        ]
        click.echo(json.dumps(json_results, indent=2, cls=_SafeEncoder))
    else:
        if not results:
            click.echo("No results found.")
            _log_end("search", start, result_count=0)
            return
        for r in results:
            source_tag = f" [{r.source}]" if r.source != "memory" else ""
            click.echo(
                f"[{r.rank}] {r.path} > {r.section}  "
                f"(score: {r.score}){source_tag}"
            )
            if r.section_description:
                click.echo(f"    {r.section_description}")
            if r.snippet:
                click.echo(f'    "{r.snippet}"')
            click.echo()
    _log_end("search", start, result_count=len(results))


@cli.command(name="grep")
@click.argument("pattern", default="")
@click.option(
    "--scope",
    default="all",
    help="Directory scope: all, own, shared, agent:<id>. Default: all.",
)
@click.option(
    "-C",
    "--context",
    "ctx_lines",
    default=0,
    type=int,
    help="Context lines before and after each match.",
)
@click.option(
    "-A",
    "--after-context",
    default=0,
    type=int,
    help="Context lines after each match.",
)
@click.option(
    "-B",
    "--before-context",
    default=0,
    type=int,
    help="Context lines before each match.",
)
@click.option("-i", "--ignore-case", is_flag=True, help="Case-insensitive search.")
@click.option(
    "-F",
    "--fixed-strings",
    is_flag=True,
    help="Treat pattern as literal string, not regex.",
)
@click.option("--tag", default=None, help="Search for entries with this frontmatter tag.")
@click.option(
    "--links-to",
    default=None,
    help="Find entries containing [[name]] wiki-link references.",
)
@click.option(
    "--base",
    default=None,
    help="Base directory for memory entries (default: config/env/cwd auto-detect).",
)
@click.pass_context
def grep_cmd(
    ctx: click.Context,
    pattern: str,
    scope: str,
    ctx_lines: int,
    after_context: int,
    before_context: int,
    ignore_case: bool,
    fixed_strings: bool,
    tag: str | None,
    links_to: str | None,
    base: str | None,
) -> None:
    """Ripgrep-based exact/regex search over memory entries.

    Searches for PATTERN across all .md files in the memory directory.
    Supports regex patterns by default, or literal strings with -F.

    Specialized modes:

      --tag NAME      Find entries with a specific frontmatter tag

      --links-to NAME Find entries referencing [[NAME]]

    Examples:

      memory grep "shift(1)" --scope own

      memory grep --tag deployment

      memory grep --links-to swarm-architecture
    """
    start = time.monotonic()
    use_json = ctx.obj["json"]
    base_path = _resolve_base(base)

    if not base_path.exists():
        click.echo(f"Base directory not found: {base_path}", err=True)
        sys.exit(1)

    if not pattern and tag is None and links_to is None:
        click.echo(
            "Pattern required. Provide a search pattern, --tag, or --links-to.",
            err=True,
        )
        sys.exit(1)

    agent_id = get_agent_id() or ""

    try:
        results = ripgrep_search(
            pattern=pattern,
            base_path=base_path,
            scope=scope,
            agent_id=agent_id,
            context_before=before_context,
            context_after=after_context,
            context=ctx_lines,
            case_insensitive=ignore_case,
            fixed_strings=fixed_strings,
            tag=tag,
            links_to=links_to,
        )
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    except (ValueError, RuntimeError) as e:
        click.echo(f"Grep error: {e}", err=True)
        sys.exit(1)

    if use_json:
        json_results = [
            {
                "path": r.path,
                "match_count": r.match_count,
                "frontmatter": r.frontmatter,
                "matches": [
                    {
                        "line_number": m.line_number,
                        "line_text": m.line_text,
                        "is_context": m.is_context,
                    }
                    for m in r.matches
                ],
            }
            for r in results
        ]
        click.echo(json.dumps(json_results, indent=2, cls=_SafeEncoder))
    else:
        if not results:
            click.echo("No matches found.")
            _log_end("grep", start, result_count=0)
            return
        for r in results:
            # File header with frontmatter summary
            header = r.path
            fm = r.frontmatter
            if fm.get("description"):
                header += f' -- "{fm["description"]}"'
            click.echo(header)
            if fm.get("author"):
                meta_parts = [f"author: {fm['author']}"]
                if fm.get("tags"):
                    meta_parts.append(f"tags: {', '.join(fm['tags'])}")
                if fm.get("confidence"):
                    meta_parts.append(f"[{fm['confidence']}]")
                click.echo(f"  {' | '.join(meta_parts)}")
            click.echo(f"  {r.match_count} match(es)")

            for m in r.matches:
                marker = " " if m.is_context else ">"
                click.echo(f" {marker}{m.line_number:4d}: {m.line_text}")

            click.echo()
    total_matches = sum(r.match_count for r in results)
    _log_end("grep", start, result_count=total_matches)


def _parse_tags(raw: str) -> list[str]:
    """Split comma-separated tag string into a cleaned list."""
    return [t.strip() for t in raw.split(",") if t.strip()]


def _read_body(body_value: str | None, auto_stdin: bool = False) -> str | None:
    """Resolve body content, with stdin support.

    When body_value is '-', reads from stdin explicitly and raises
    click.UsageError if stdin is empty.
    When body_value is None and auto_stdin is True, reads piped stdin
    (non-TTY) automatically so heredoc/pipe input is not silently lost.
    """
    if body_value == "-":
        content = sys.stdin.read()
        if not content.strip():
            raise click.UsageError(
                "Body flag '-b -' expects content on stdin, but stdin was "
                "empty. Pipe content via: echo 'body' | memory new -b - ..."
            )
        return content
    if body_value is not None:
        return body_value
    if auto_stdin and not sys.stdin.isatty():
        content = sys.stdin.read()
        if content.strip():
            return content
    return None


@cli.command(name="new")
@click.argument("name")
@click.option("-d", "--description", required=True, help="Entry description for frontmatter.")
@click.option("-b", "--body", default=None, help="Body content. Use '-' to read from stdin.")
@click.option("--author", default=None, help="Agent ID override (default: AGENT_ID env var).")
@click.option("-t", "--tags", "raw_tags", default=None, help="Comma-separated tags.")
@click.option(
    "-c",
    "--category",
    default="",
    help=(
        "Category subdirectory. Built-ins are atlas, efforts, calendar, moc; "
        "custom categories may be configured."
    ),
)
@click.option(
    "--confidence",
    default="",
    type=click.Choice(["established", "working", "exploratory", ""], case_sensitive=False),
    help="Confidence level.",
)
@click.option(
    "--status",
    default="active",
    type=click.Choice(["active", "archived", "draft"], case_sensitive=False),
    help="Entry status (default: active).",
)
@click.option(
    "--shared",
    is_flag=True,
    help="Write to memory/shared/ instead of memory/{agent_id}/.",
)
@click.option("--no-git", is_flag=True, help="Skip git add/commit/push after creating.")
@click.option(
    "--allow-non-main-branch",
    is_flag=True,
    help=(
        "Allow committing while HEAD is on a non-default branch. "
        "Without this flag, writes refuse to land on feature branches "
        "(issue #82 guard against orphaned commits)."
    ),
)
@click.option(
    "--base",
    default=None,
    help="Base directory (default: config/env/cwd auto-detect).",
)
@click.pass_context
def new_cmd(
    ctx: click.Context,
    name: str,
    description: str,
    body: str | None,
    author: str | None,
    raw_tags: str | None,
    category: str,
    confidence: str,
    status: str,
    shared: bool,
    no_git: bool,
    allow_non_main_branch: bool,
    base: str | None,
) -> None:
    """Create a new memory entry with frontmatter."""
    start = time.monotonic()
    use_json = ctx.obj["json"]

    # Resolve agent_id: --author flag > env var > config file
    agent_id = author or get_agent_id() or ""
    if not agent_id:
        click.echo(
            "Agent ID required: set --author, AGENT_ID env var,"
            " or agent_id in config file",
            err=True,
        )
        sys.exit(1)

    # Resolve base path
    base_path = _resolve_base(base)

    # Parse tags
    tags = _parse_tags(raw_tags) if raw_tags else None
    category = category.strip().lower()

    # Resolve body (stdin support: -b - explicit, or auto-detect piped stdin)
    try:
        resolved_body = _read_body(body, auto_stdin=True) or ""
    except click.UsageError as e:
        _log_error("new", start, e)
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    try:
        file_path = create_entry(
            base_path=base_path,
            agent_id=agent_id,
            name=name,
            description=description,
            body=resolved_body,
            tags=tags,
            category=category,
            confidence=confidence,
            status=status,
            shared=shared,
        )
    except (ValueError, FileExistsError) as e:
        _log_error("new", start, e)
        click.echo(f"Error creating entry: {e}", err=True)
        sys.exit(1)

    # Git workflow
    git_sha = None
    if not no_git:
        try:
            repo_path = git_repo_root(base_path)
            git_sha = commit_and_push(
                file_path,
                agent_id,
                "add",
                name,
                repo_path,
                allow_non_default_branch=allow_non_main_branch,
            )
        except BranchMismatchError as e:
            # Issue #82: roll back the just-created entry so the failure
            # is transactional. Without this, the file exists on disk but
            # is not committed -- a re-run after `git checkout main` would
            # fail with "Entry already exists", which is the wrong error
            # for the wrong reason.
            #
            # Rollback failures are surfaced on stderr (not silently swallowed)
            # so the operator knows the on-disk state diverges from what the
            # primary error implies.
            try:
                file_path.unlink(missing_ok=True)
                # Also remove the empty `<agent_id>/<category>/` tree that
                # `_create_entry` may have created via `mkdir(parents=True)`
                # solely for this entry. Walk up at most two levels (the
                # category dir, then the agent_id dir). rmdir refuses to
                # delete non-empty dirs, so each step is a no-op if the dir
                # holds other entries.
                #
                # Bound the walk so it never touches `base_path` itself --
                # the memory root belongs to the operator, not the rollback.
                # This protects `--shared` writes (where category_dir.parent
                # IS base_path) and the empty-base case (where base_path
                # could be empty if the just-rejected entry was the only
                # thing in it).
                base_resolved = base_path.resolve()
                category_dir = file_path.parent
                agent_dir = category_dir.parent
                for empty_candidate in (category_dir, agent_dir):
                    try:
                        if empty_candidate.resolve() == base_resolved:
                            break  # never remove the memory root itself
                    except OSError:
                        break  # candidate already gone
                    try:
                        empty_candidate.rmdir()
                    except OSError:
                        break  # not empty (or gone) -- stop walking up
            except OSError as rollback_exc:
                click.echo(
                    f"warning: rollback after branch-mismatch failed -- "
                    f"file {file_path} may still exist on disk "
                    f"({type(rollback_exc).__name__}: {rollback_exc})",
                    err=True,
                )
            _log_error("new", start, e)
            click.echo(f"error: {e}", err=True)
            sys.exit(2)
        except RuntimeError as e:
            _log_error("new", start, e)
            click.echo(f"Git error: {e}", err=True)
            sys.exit(1)

    if use_json:
        result = {
            "path": str(file_path),
            "agent_id": agent_id,
            "name": name,
            "description": description,
        }
        if tags:
            result["tags"] = tags
        if category:
            result["category"] = category
        if confidence:
            result["confidence"] = confidence
        if status:
            result["status"] = status
        if git_sha:
            result["git_sha"] = git_sha
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(f"Created {file_path}")
        if git_sha:
            click.echo(f"Committed: {git_sha[:12]}")
    _log_end("new", start)


@cli.command(name="update")
@click.argument("file_path", type=click.Path())
@click.option("-b", "--body", default=None, help="New body content. Use '-' to read from stdin.")
@click.option("-t", "--tags", "raw_tags", default=None, help="Replace tags (comma-separated).")
@click.option("--add-tags", "raw_add_tags", default=None, help="Append tags (comma-separated).")
@click.option(
    "--confidence",
    default=None,
    type=click.Choice(["established", "working", "exploratory"], case_sensitive=False),
    help="Update confidence level.",
)
@click.option(
    "--status",
    default=None,
    type=click.Choice(["active", "archived", "draft"], case_sensitive=False),
    help="Update status.",
)
@click.option("--no-git", is_flag=True, help="Skip git add/commit/push after updating.")
@click.option(
    "--allow-non-main-branch",
    is_flag=True,
    help=(
        "Allow committing while HEAD is on a non-default branch. "
        "Without this flag, writes refuse to land on feature branches "
        "(issue #82 guard against orphaned commits)."
    ),
)
@click.option(
    "--base",
    default=None,
    help="Base directory for git repo root detection.",
)
@click.pass_context
def update_cmd(
    ctx: click.Context,
    file_path: str,
    body: str | None,
    raw_tags: str | None,
    raw_add_tags: str | None,
    confidence: str | None,
    status: str | None,
    no_git: bool,
    allow_non_main_branch: bool,
    base: str | None,
) -> None:
    """Update an existing memory entry."""
    start = time.monotonic()
    use_json = ctx.obj["json"]

    # Resolve file path
    resolved = resolve_md_path(file_path)
    if not resolved.exists():
        parent = resolved.parent
        suggestions = _suggest_files(parent)
        msg = f"File not found: {file_path}"
        if suggestions:
            msg += f"\nAvailable files in {parent}:"
            for f in suggestions:
                msg += f"\n  {f}"
        click.echo(msg, err=True)
        sys.exit(1)

    # Parse tags
    tags = _parse_tags(raw_tags) if raw_tags is not None else None
    add_tags = _parse_tags(raw_add_tags) if raw_add_tags is not None else None

    # Resolve body (stdin support: -b - explicit, or auto-detect piped stdin)
    try:
        resolved_body = _read_body(body, auto_stdin=True)
    except click.UsageError as e:
        _log_error("update", start, e)
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Snapshot original content before mutation so we can roll back on
    # BranchMismatchError below (issue #82 transactional behavior).
    original_content = resolved.read_text(encoding="utf-8")

    try:
        updated_path = update_entry(
            file_path=resolved,
            body=resolved_body,
            tags=tags,
            add_tags=add_tags,
            confidence=confidence,
            status=status,
        )
    except (ValueError, FileNotFoundError) as e:
        _log_error("update", start, e)
        click.echo(f"Error updating entry: {e}", err=True)
        sys.exit(1)

    # Git workflow
    git_sha = None
    if not no_git:
        try:
            # Read author from existing frontmatter for commit message
            text = resolved.read_text(encoding="utf-8")
            fm, _ = parse_frontmatter(text)
            agent_id = fm.author
            if not agent_id:
                click.echo(
                    "Cannot determine agent_id from frontmatter author field for git commit",
                    err=True,
                )
                sys.exit(1)

            entry_name = resolved.stem
            # Resolve repo root from --base (via config) or from the file itself
            if base:
                repo_path = git_repo_root(_resolve_base(base))
            else:
                repo_path = git_repo_root(resolved)
            git_sha = commit_and_push(
                updated_path,
                agent_id,
                "update",
                entry_name,
                repo_path,
                allow_non_default_branch=allow_non_main_branch,
            )
        except BranchMismatchError as e:
            # Issue #82: roll back the in-place mutation so the failure
            # is transactional. Without this, the file on disk would be
            # modified but uncommitted -- a re-run after `git checkout main`
            # would commit a half-state the user did not see.
            #
            # Rollback failures are surfaced on stderr (not silently swallowed)
            # so the operator knows the file diverges from what the primary
            # error implies. Without this warning the user sees only the
            # branch error and assumes the file is unchanged.
            try:
                resolved.write_text(original_content, encoding="utf-8")
            except OSError as rollback_exc:
                click.echo(
                    f"warning: rollback after branch-mismatch failed -- "
                    f"file {resolved} contains the unsaved mutation "
                    f"({type(rollback_exc).__name__}: {rollback_exc})",
                    err=True,
                )
            _log_error("update", start, e)
            click.echo(f"error: {e}", err=True)
            sys.exit(2)
        except RuntimeError as e:
            _log_error("update", start, e)
            click.echo(f"Git error: {e}", err=True)
            sys.exit(1)

    if use_json:
        # Re-read updated frontmatter for JSON output
        text = resolved.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        result = {
            "path": str(updated_path),
            "frontmatter": {
                "description": fm.description,
                "author": fm.author,
                "created": fm.created,
                "updated": fm.updated,
                "tags": fm.tags,
                "confidence": fm.confidence,
                "status": fm.status,
            },
        }
        if git_sha:
            result["git_sha"] = git_sha
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(f"Updated {updated_path}")
        if git_sha:
            click.echo(f"Committed: {git_sha[:12]}")
    _log_end("update", start)


@cli.group(name="cache")
@click.pass_context
def cache_group(ctx: click.Context) -> None:
    """Manage the SQLite index cache for BM25 search."""
    ctx.ensure_object(dict)


@cache_group.command(name="build")
@click.option(
    "--base",
    default=None,
    help="Base directory for memory entries (default: config/env/cwd auto-detect).",
)
@click.option(
    "--verify-hash",
    is_flag=True,
    help="Use SHA-256 content hash for staleness (slower, more accurate).",
)
@click.pass_context
def cache_build_cmd(
    ctx: click.Context, base: str | None, verify_hash: bool
) -> None:
    """Build or refresh the SQLite index cache."""
    from agent_memory.cache import IndexCache, resolve_cache_path

    use_json = ctx.obj["json"]
    base_path = _resolve_base(base)

    if not base_path.exists():
        click.echo(f"Base directory not found: {base_path}", err=True)
        sys.exit(1)

    cache_path = resolve_cache_path(base_path)
    cache = IndexCache(cache_path)
    cache.open()
    try:
        changed = cache.refresh(base_path, verify_hash=verify_hash)
        stats = cache.get_global_stats()
    finally:
        cache.close()

    if use_json:
        click.echo(json.dumps({
            "cache_path": str(cache_path),
            "files_changed": changed,
            "file_count": stats.file_count,
            "section_count": stats.section_count,
            "term_count": stats.term_count,
            "avgdl": round(stats.avgdl, 2),
            "db_size_bytes": stats.db_size_bytes,
        }, indent=2))
    else:
        click.echo(f"Cache: {cache_path}")
        click.echo(f"  Files changed: {changed}")
        click.echo(f"  Files indexed: {stats.file_count}")
        click.echo(f"  Sections: {stats.section_count}")
        click.echo(f"  Unique terms: {stats.term_count}")
        click.echo(f"  Avg doc length: {stats.avgdl:.1f}")
        click.echo(f"  DB size: {stats.db_size_bytes:,} bytes")


@cache_group.command(name="status")
@click.option(
    "--base",
    default=None,
    help="Base directory for memory entries (default: config/env/cwd auto-detect).",
)
@click.pass_context
def cache_status_cmd(ctx: click.Context, base: str | None) -> None:
    """Show cache statistics."""
    from agent_memory.cache import IndexCache, resolve_cache_path

    use_json = ctx.obj["json"]
    base_path = _resolve_base(base)
    cache_path = resolve_cache_path(base_path)

    if not cache_path.exists():
        if use_json:
            click.echo(json.dumps({
                "exists": False,
                "cache_path": str(cache_path),
            }, indent=2))
        else:
            click.echo(f"No cache found at {cache_path}")
            click.echo("Run 'memory cache build' to create one.")
        return

    cache = IndexCache(cache_path)
    cache.open()
    try:
        stats = cache.get_global_stats()
    finally:
        cache.close()

    if use_json:
        click.echo(json.dumps({
            "exists": True,
            "cache_path": str(cache_path),
            "file_count": stats.file_count,
            "section_count": stats.section_count,
            "term_count": stats.term_count,
            "avgdl": round(stats.avgdl, 2),
            "last_rebuilt": stats.last_rebuilt,
            "db_size_bytes": stats.db_size_bytes,
        }, indent=2))
    else:
        import time as _time

        click.echo(f"Cache: {cache_path}")
        click.echo(f"  Files indexed: {stats.file_count}")
        click.echo(f"  Sections: {stats.section_count}")
        click.echo(f"  Unique terms: {stats.term_count}")
        click.echo(f"  Avg doc length: {stats.avgdl:.1f}")
        if stats.last_rebuilt > 0:
            ts = _time.strftime(
                "%Y-%m-%d %H:%M:%S",
                _time.localtime(stats.last_rebuilt),
            )
            click.echo(f"  Last rebuilt: {ts}")
        click.echo(f"  DB size: {stats.db_size_bytes:,} bytes")


@cache_group.command(name="clear")
@click.option(
    "--base",
    default=None,
    help="Base directory for memory entries (default: config/env/cwd auto-detect).",
)
@click.pass_context
def cache_clear_cmd(ctx: click.Context, base: str | None) -> None:
    """Delete the cache database file."""
    from agent_memory.cache import IndexCache, resolve_cache_path

    use_json = ctx.obj["json"]
    base_path = _resolve_base(base)
    cache_path = resolve_cache_path(base_path)

    if not cache_path.exists():
        if use_json:
            click.echo(json.dumps({
                "deleted": False,
                "reason": "not found",
            }))
        else:
            click.echo(f"No cache found at {cache_path}")
        return

    cache = IndexCache(cache_path)
    cache.clear()

    if use_json:
        click.echo(json.dumps({
            "deleted": True,
            "path": str(cache_path),
        }))
    else:
        click.echo(f"Deleted cache: {cache_path}")


@cli.command(name="clone")
@click.pass_context
def clone_cmd(ctx: click.Context) -> None:
    """Clone the memory repository to a local path.

    Uses AGENT_MEMORY_REPO (git remote URL) and AGENT_MEMORY_PATH (local path).
    Skips if already cloned with the same remote.
    """
    from agent_memory.sync import clone_repo

    start = time.monotonic()
    use_json = ctx.obj["json"]

    remote_url = os.environ.get("AGENT_MEMORY_REPO", "")
    if not remote_url:
        click.echo(
            "AGENT_MEMORY_REPO environment variable is required. "
            "Set it to the git remote URL of the memory repository.",
            err=True,
        )
        sys.exit(1)

    local_path_str = os.environ.get("AGENT_MEMORY_PATH", "")
    if not local_path_str:
        click.echo(
            "AGENT_MEMORY_PATH environment variable is required. "
            "Set it to the desired local clone path (e.g. ~/.agent-memory).",
            err=True,
        )
        sys.exit(1)

    local_path = Path(local_path_str).expanduser()

    try:
        result = clone_repo(remote_url, local_path)
    except RuntimeError as e:
        _log_error("clone", start, e)
        click.echo(f"Clone failed: {e}", err=True)
        sys.exit(1)

    if use_json:
        click.echo(json.dumps({
            "already_existed": result.already_existed,
            "local_path": result.local_path,
            "remote_url": result.remote_url,
            "message": result.message,
        }, indent=2))
    else:
        click.echo(result.message)
    _log_end("clone", start)


@cli.command(name="sync")
@click.option(
    "--pull-only", is_flag=True,
    help="Only pull, do not push local changes.",
)
@click.option(
    "--push-only", is_flag=True,
    help="Only push, do not pull remote changes.",
)
@click.option(
    "--allow-non-main-branch",
    is_flag=True,
    help=(
        "Allow committing uncommitted changes while HEAD is on a "
        "non-default branch. Without this flag, sync refuses to commit "
        "on feature branches (issue #82 guard against orphaned commits)."
    ),
)
@click.pass_context
def sync_cmd(
    ctx: click.Context,
    pull_only: bool,
    push_only: bool,
    allow_non_main_branch: bool,
) -> None:
    """Sync the local memory repository with the remote.

    Pulls latest changes, pushes local uncommitted changes,
    and reports what changed with frontmatter descriptions.
    Uses AGENT_MEMORY_PATH and AGENT_ID environment variables.
    """
    from agent_memory.sync import sync_repo

    start = time.monotonic()
    use_json = ctx.obj["json"]

    if pull_only and push_only:
        click.echo("Cannot use --pull-only and --push-only together.", err=True)
        sys.exit(1)

    agent_id = get_agent_id() or ""
    if not agent_id:
        click.echo(
            "Agent ID required for sync: set AGENT_ID env var"
            " or agent_id in config file.",
            err=True,
        )
        sys.exit(1)

    local_path_str = os.environ.get("AGENT_MEMORY_PATH", "")
    if not local_path_str:
        click.echo(
            "AGENT_MEMORY_PATH environment variable is required. "
            "Set it to the local clone path of the memory repository.",
            err=True,
        )
        sys.exit(1)

    local_path = Path(local_path_str).expanduser()

    try:
        result = sync_repo(
            repo_path=local_path,
            agent_id=agent_id,
            pull_only=pull_only,
            push_only=push_only,
            allow_non_default_branch=allow_non_main_branch,
        )
    except BranchMismatchError as e:
        _log_error("sync", start, e)
        click.echo(f"error: {e}", err=True)
        sys.exit(2)
    except RuntimeError as e:
        _log_error("sync", start, e)
        click.echo(f"Sync failed: {e}", err=True)
        sys.exit(1)

    if use_json:
        json_data = {
            "pulled": result.pulled,
            "pushed": result.pushed,
            "pre_sync_sha": result.pre_sync_sha,
            "post_sync_sha": result.post_sync_sha,
            "message": result.message,
            "changes": [
                {
                    "status": c.status,
                    "path": c.path,
                    "description": c.description,
                }
                for c in result.changes
            ],
            "validation_warnings": result.validation_warnings,
        }
        click.echo(json.dumps(json_data, indent=2))
    else:
        click.echo(result.message)
        if result.changes:
            click.echo("")
            for change in result.changes:
                line = f"  [{change.status}] {change.path}"
                if change.description:
                    line += f' -- "{change.description}"'
                click.echo(line)
        if result.validation_warnings:
            click.echo("\nValidation warnings:")
            for warning in result.validation_warnings:
                click.echo(f"  [warn] {warning}")
    _log_end("sync", start, result_count=len(result.changes))


@cli.command(name="log")
@click.option(
    "--tail",
    default=20,
    type=int,
    help="Number of recent entries to show. Default: 20.",
)
@click.option(
    "--level",
    default=None,
    type=click.Choice(["INFO", "ERROR", "WARNING", "DEBUG"], case_sensitive=False),
    help="Filter by log level.",
)
@click.option(
    "--since",
    default=None,
    help="Filter entries since date (ISO 8601, e.g. 2026-02-14).",
)
@click.option("--stats", is_flag=True, help="Show summary statistics.")
@click.option("--command", "cmd_filter", default=None, help="Filter by command name.")
@click.option("--agent", default=None, help="Filter by agent ID.")
@click.pass_context
def log_cmd(
    ctx: click.Context,
    tail: int,
    level: str | None,
    since: str | None,
    stats: bool,
    cmd_filter: str | None,
    agent: str | None,
) -> None:
    """View and analyze CLI log entries.

    Shows recent log entries in human-readable or JSON format.
    Use --stats for aggregated usage summary.

    Examples:

      memory log                          Show last 20 entries

      memory log --tail 50               Show last 50 entries

      memory log --level ERROR           Show only errors

      memory log --stats                 Show usage statistics

      memory log --since 2026-02-14      Entries since date
    """
    from agent_memory.log_viewer import (
        compute_stats,
        filter_entries,
        format_entry_human,
        read_log_file,
        resolve_log_path,
    )

    use_json = ctx.obj["json"]
    log_path = resolve_log_path()

    if not log_path.exists():
        if use_json:
            data = {"error": "no log file found", "path": str(log_path)}
            click.echo(json.dumps(data))
        else:
            click.echo(f"No log file found at {log_path}")
            click.echo("Run some commands first to generate log entries.")
        return

    entries = read_log_file(log_path)
    entries = filter_entries(
        entries, level=level, since=since, command=cmd_filter, agent=agent
    )

    if stats:
        log_stats = compute_stats(entries)
        if use_json:
            click.echo(json.dumps({
                "total_entries": log_stats.total_entries,
                "total_commands": log_stats.total_commands,
                "total_errors": log_stats.total_errors,
                "error_rate_pct": log_stats.error_rate,
                "avg_duration_ms": log_stats.avg_duration_ms,
                "command_counts": log_stats.command_counts,
                "search_terms": log_stats.search_terms,
            }, indent=2))
        else:
            click.echo("CLI Log Statistics")
            click.echo(f"  Total entries: {log_stats.total_entries}")
            click.echo(f"  Commands completed: {log_stats.total_commands}")
            click.echo(f"  Errors: {log_stats.total_errors}")
            click.echo(f"  Error rate: {log_stats.error_rate}%")
            click.echo(f"  Avg duration: {log_stats.avg_duration_ms}ms")
            if log_stats.command_counts:
                click.echo("  Most used commands:")
                for cmd, count in log_stats.command_counts.items():
                    click.echo(f"    {cmd}: {count}")
            if log_stats.search_terms:
                click.echo("  Search terms:")
                for term in log_stats.search_terms[-10:]:
                    click.echo(f'    "{term}"')
        return

    recent = entries[-tail:] if len(entries) > tail else entries

    if use_json:
        click.echo(json.dumps(
            [e.raw for e in recent],
            indent=2,
        ))
    else:
        if not recent:
            click.echo("No log entries found matching filters.")
            return
        for entry in recent:
            click.echo(format_entry_human(entry))
