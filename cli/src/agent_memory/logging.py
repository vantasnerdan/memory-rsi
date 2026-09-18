"""Structured logging infrastructure for agent-memory CLI and hooks.

Provides JSON Lines logging to two separate files:
- cli.log: Every CLI command invocation (command, args, duration, exit code)
- hooks.log: Every hook invocation (hook type, agent, session, outcome)

Log path is configurable via AGENT_MEMORY_LOG_PATH env var.
Default: ~/.agent-memory/

Rotation: 7-day retention, daily rotation via TimedRotatingFileHandler.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# Default log directory
DEFAULT_LOG_DIR = Path.home() / ".agent-memory"

# Log file names
CLI_LOG_FILE = "cli.log"
HOOKS_LOG_FILE = "hooks.log"

# Logger names (module-level, importable)
CLI_LOGGER_NAME = "agent_memory.cli_audit"
HOOKS_LOGGER_NAME = "agent_memory.hooks_audit"

# Retention
BACKUP_COUNT = 7  # Keep 7 days of rotated logs


class JSONLinesFormatter(logging.Formatter):
    """Format log records as JSON Lines (one JSON object per line).

    Each line is a self-contained JSON object with at minimum:
    - ts: ISO-8601 UTC timestamp
    - level: log level name
    - Plus any extra fields passed via the `extra` dict.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
        }

        # Merge all extra fields from the record
        # LogRecord stores extra keys directly as attributes
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in (
                "name", "msg", "args", "created", "relativeCreated",
                "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                "filename", "module", "pathname", "thread", "threadName",
                "process", "processName", "levelname", "levelno",
                "msecs", "message", "taskName",
            ):
                continue
            entry[key] = value

        return json.dumps(entry, default=str)


def get_log_dir() -> Path:
    """Resolve the log directory from environment or default.

    Reads AGENT_MEMORY_LOG_PATH. Creates the directory if it doesn't exist.
    """
    env_path = os.environ.get("AGENT_MEMORY_LOG_PATH")
    if env_path:
        log_dir = Path(env_path)
    else:
        log_dir = DEFAULT_LOG_DIR

    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _setup_file_logger(
    name: str,
    filename: str,
    log_dir: Path,
) -> logging.Logger:
    """Create a logger with a TimedRotatingFileHandler writing JSON Lines.

    Args:
        name: Logger name (should be unique per log target).
        filename: Log file name (e.g. 'cli.log').
        log_dir: Directory for the log file.

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)

    # Reuse a handler only when it already targets the requested file. This
    # matters for tests, containers, and long-running agents that change
    # AGENT_MEMORY_LOG_PATH between invocations.
    log_path = (log_dir / filename).resolve()
    for handler in logger.handlers:
        handler_path = getattr(handler, "baseFilename", None)
        if handler_path and Path(handler_path).resolve() == log_path:
            return logger

    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    handler = TimedRotatingFileHandler(
        str(log_path),
        when="midnight",
        backupCount=BACKUP_COUNT,
        utc=True,
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(JSONLinesFormatter())

    logger.addHandler(handler)
    return logger


def get_cli_logger() -> logging.Logger:
    """Get the CLI audit logger (writes to cli.log)."""
    return _setup_file_logger(CLI_LOGGER_NAME, CLI_LOG_FILE, get_log_dir())


def get_hooks_logger() -> logging.Logger:
    """Get the hooks audit logger (writes to hooks.log)."""
    return _setup_file_logger(HOOKS_LOGGER_NAME, HOOKS_LOG_FILE, get_log_dir())


def log_cli_command(
    cmd: str,
    args: dict | None = None,
    duration_ms: float | None = None,
    exit_code: int = 0,
    result_count: int | None = None,
    error: str | None = None,
) -> None:
    """Log a CLI command invocation to cli.log.

    Args:
        cmd: Command name (e.g. 'search', 'new', 'validate').
        args: Command arguments as a dict.
        duration_ms: Execution time in milliseconds.
        exit_code: Command exit code (0 = success). When error is set
            and exit_code is 0, exit is overridden to 1. Pass a specific
            non-zero exit_code (e.g. 2) to preserve it alongside the error.
        result_count: Number of results returned (for search/ls/grep).
        error: Error message if command failed.
    """
    logger = get_cli_logger()
    agent = os.environ.get("AGENT_ID", "unknown")

    extra = {
        "cmd": cmd,
        "agent": agent,
    }
    if args is not None:
        extra["cmd_args"] = args
    if duration_ms is not None:
        extra["duration_ms"] = round(duration_ms, 1)
    if result_count is not None:
        extra["results"] = result_count
    if error is not None:
        extra["error"] = error
        extra["exit"] = exit_code if exit_code != 0 else 1
    else:
        extra["exit"] = exit_code

    level = logging.ERROR if exit_code != 0 else logging.INFO
    logger.log(level, "", extra=extra)


def log_hook_event(
    hook: str,
    outcome: str,
    session_id: str | None = None,
    agent_type: str | None = None,
    context_bytes: int | None = None,
    entries_written: int | None = None,
    duration_ms: float | None = None,
    error: str | None = None,
) -> None:
    """Log a hook invocation to hooks.log.

    Args:
        hook: Hook name (e.g. 'PreCompact', 'SessionStart',
              'SubagentStart', 'SubagentStop').
        outcome: 'ok', 'error', 'skip', 'empty'.
        session_id: Claude Code session ID.
        agent_type: Subagent type (for SubagentStart/Stop).
        context_bytes: Bytes of context loaded/saved.
        entries_written: Number of memory entries written.
        duration_ms: Hook execution time in milliseconds.
        error: Error message if hook failed.
    """
    logger = get_hooks_logger()
    agent = os.environ.get("AGENT_ID", "unknown")

    extra = {
        "hook": hook,
        "agent": agent,
        "outcome": outcome,
    }
    if session_id is not None:
        extra["session"] = session_id
    if agent_type is not None:
        extra["agent_type"] = agent_type
    if context_bytes is not None:
        extra["context_bytes"] = context_bytes
    if entries_written is not None:
        extra["entries_written"] = entries_written
    if duration_ms is not None:
        extra["duration_ms"] = round(duration_ms, 1)
    if error is not None:
        extra["error"] = error

    level = logging.ERROR if outcome == "error" else logging.INFO
    logger.log(level, "", extra=extra)


class CommandTimer:
    """Context manager for timing CLI commands and logging the result.

    Usage:
        with CommandTimer("search", args={"query": "test"}) as timer:
            results = do_search()
            timer.result_count = len(results)
    """

    def __init__(self, cmd: str, args: dict | None = None) -> None:
        self.cmd = cmd
        self.args = args
        self.result_count: int | None = None
        self.exit_code: int = 0
        self.error: str | None = None
        self._start: float = 0.0

    def __enter__(self) -> CommandTimer:
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:  # noqa: ANN001
        elapsed_ms = (time.monotonic() - self._start) * 1000

        if exc_type is not None:
            self.exit_code = 1
            self.error = str(exc_val)

        log_cli_command(
            cmd=self.cmd,
            args=self.args,
            duration_ms=elapsed_ms,
            exit_code=self.exit_code,
            result_count=self.result_count,
            error=self.error,
        )
        # Don't suppress exceptions
        return False
