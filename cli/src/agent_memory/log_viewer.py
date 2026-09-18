"""Log viewer for agent-memory CLI log files.

Reads JSON Lines log files and provides filtering, formatting,
and statistics for CLI command usage analysis.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LogEntry:
    """A parsed log entry from the JSON Lines log file."""

    timestamp: str
    level: str
    message: str
    command: str = ""
    agent_id: str = ""
    cmd_args: dict = field(default_factory=dict)
    duration_ms: float = 0.0
    result_summary: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass
class LogStats:
    """Aggregated statistics from log entries."""

    total_entries: int = 0
    total_commands: int = 0
    total_errors: int = 0
    error_rate: float = 0.0
    command_counts: dict = field(default_factory=dict)
    search_terms: list[str] = field(default_factory=list)
    avg_duration_ms: float = 0.0


def parse_log_entry(line: str) -> LogEntry | None:
    """Parse a single JSON line into a LogEntry, or None if invalid.

    Handles both field name formats:
    - Kelvin's logging.py: ts, cmd, agent, exit, results
    - Legacy logging_config.py: timestamp, command, agent_id, result_summary
    """
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None

    timestamp = data.get("timestamp", "") or data.get("ts", "")
    level = data.get("level", "").upper()
    command = data.get("command", "") or data.get("cmd", "")
    agent_id = data.get("agent_id", "") or data.get("agent", "")

    return LogEntry(
        timestamp=timestamp,
        level=level,
        message=data.get("message", ""),
        command=command,
        agent_id=agent_id,
        cmd_args=data.get("cmd_args", {}),
        duration_ms=data.get("duration_ms", 0.0),
        result_summary=data.get("result_summary", {}),
        raw=data,
    )


def read_log_file(log_path: Path) -> list[LogEntry]:
    """Read and parse all entries from a log file."""
    if not log_path.exists():
        return []
    entries = []
    text = log_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        entry = parse_log_entry(line)
        if entry is not None:
            entries.append(entry)
    return entries


def filter_entries(
    entries: list[LogEntry],
    level: str | None = None,
    since: str | None = None,
    command: str | None = None,
    agent: str | None = None,
) -> list[LogEntry]:
    """Filter log entries by level, date, command, or agent."""
    filtered = entries

    if level:
        level_upper = level.upper()
        filtered = [e for e in filtered if e.level == level_upper]

    if since:
        filtered = [e for e in filtered if e.timestamp >= since]

    if command:
        cmd_lower = command.lower()
        filtered = [e for e in filtered if e.command.lower() == cmd_lower]

    if agent:
        filtered = [e for e in filtered if e.agent_id == agent]

    return filtered


def compute_stats(entries: list[LogEntry]) -> LogStats:
    """Compute aggregate statistics from log entries.

    Detects completed commands by duration_ms > 0 (Kelvin's single-event
    format) or message == 'command completed' (legacy two-event format).
    """
    completed = [
        e for e in entries
        if e.duration_ms > 0 or e.message == "command completed"
    ]
    errors = [e for e in entries if e.level == "ERROR"]
    command_counter: Counter[str] = Counter()
    search_terms: list[str] = []
    total_duration = 0.0

    for entry in completed:
        if entry.command:
            command_counter[entry.command] += 1
        total_duration += entry.duration_ms

    for entry in entries:
        if entry.command == "search":
            query = entry.cmd_args.get("query", "")
            if query:
                search_terms.append(query)

    total_commands = len(completed)
    total_errors = len(errors)
    error_rate = (total_errors / total_commands * 100) if total_commands > 0 else 0.0
    avg_duration = total_duration / total_commands if total_commands > 0 else 0.0

    return LogStats(
        total_entries=len(entries),
        total_commands=total_commands,
        total_errors=total_errors,
        error_rate=round(error_rate, 1),
        command_counts=dict(command_counter.most_common()),
        search_terms=search_terms,
        avg_duration_ms=round(avg_duration, 2),
    )


def _format_timestamp(ts: str) -> str:
    """Trim ISO timestamp to YYYY-MM-DD HH:MM:SS."""
    ts = ts.replace("T", " ") if "T" in ts else ts
    ts = ts[: ts.index("+")] if "+" in ts else ts
    return ts[:19] if len(ts) > 19 else ts


def _format_summary(summary: dict) -> str:
    """Format a result_summary dict into a short string."""
    if "results" in summary:
        return f"-> {summary['results']} results"
    if "files" in summary:
        detail = f"{summary['files']} files"
        if summary.get("errors", 0) > 0:
            detail += f", {summary['errors']} errors"
        return f"-> {detail}"
    if "error_type" in summary:
        return f"-> {summary['error_type']}: {summary.get('error_message', '')}"
    if "matched" in summary:
        return f"-> {summary['matched']}"
    if "path" in summary:
        return f"-> {summary['path']}"
    return ""


def format_entry_human(entry: LogEntry) -> str:
    """Format a log entry as a human-readable one-liner."""
    parts = [_format_timestamp(entry.timestamp), f"[{entry.level}]"]
    if entry.command:
        parts.append(entry.command)
    if entry.command == "search":
        query = entry.cmd_args.get("query", "")
        if query:
            parts.append(f'"{query}"')
    if entry.duration_ms > 0:
        parts.append(f"({entry.duration_ms:.0f}ms)")
    # Handle Kelvin's top-level results field
    raw = entry.raw
    if "results" in raw:
        parts.append(f"-> {raw['results']} results")
    elif "error" in raw:
        parts.append(f"-> {raw['error']}")
    elif isinstance(entry.result_summary, dict):
        s = _format_summary(entry.result_summary)
        if s:
            parts.append(s)
    return " ".join(parts)


def resolve_log_path() -> Path:
    """Resolve the CLI log file path."""
    from agent_memory.logging import CLI_LOG_FILE, get_log_dir

    return get_log_dir() / CLI_LOG_FILE
