"""Tests for structured logging infrastructure and log viewer command."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from agent_memory.cli import cli
from agent_memory.log_viewer import (
    LogEntry,
    compute_stats,
    filter_entries,
    format_entry_human,
    parse_log_entry,
    read_log_file,
)
from agent_memory.logging import (
    CLI_LOG_FILE,
    CLI_LOGGER_NAME,
    HOOKS_LOG_FILE,
    HOOKS_LOGGER_NAME,
    CommandTimer,
    JSONLinesFormatter,
    get_cli_logger,
    get_hooks_logger,
    get_log_dir,
    log_cli_command,
    log_hook_event,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_loggers():
    """Remove handlers from audit loggers between tests to avoid duplicates."""
    yield
    for name in (CLI_LOGGER_NAME, HOOKS_LOGGER_NAME):
        logger = logging.getLogger(name)
        logger.handlers.clear()


@pytest.fixture()
def log_dir(tmp_path: Path) -> Path:
    """Provide a temporary log directory via env var."""
    d = tmp_path / "logs"
    d.mkdir()
    with patch.dict(os.environ, {"AGENT_MEMORY_LOG_PATH": str(d)}):
        yield d


@pytest.fixture()
def log_dir_env(tmp_path: Path):
    """Patch env var and clean loggers for isolated tests."""
    d = tmp_path / "logs"
    d.mkdir()
    with patch.dict(os.environ, {"AGENT_MEMORY_LOG_PATH": str(d)}):
        # Clear handlers so they use the new path
        for name in (CLI_LOGGER_NAME, HOOKS_LOGGER_NAME):
            logging.getLogger(name).handlers.clear()
        yield d


# ===========================================================================
# Kelvin's logging module tests (PR #36) -- DO NOT MODIFY
# ===========================================================================


# ---------------------------------------------------------------------------
# JSONLinesFormatter
# ---------------------------------------------------------------------------


class TestJSONLinesFormatter:
    def test_format_produces_valid_json(self) -> None:
        formatter = JSONLinesFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="", args=None, exc_info=None,
        )
        line = formatter.format(record)
        data = json.loads(line)
        assert "ts" in data
        assert data["level"] == "info"

    def test_format_includes_extra_fields(self) -> None:
        formatter = JSONLinesFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="", args=None, exc_info=None,
        )
        record.cmd = "search"
        record.agent = "kelvin"
        record.results = 5
        line = formatter.format(record)
        data = json.loads(line)
        assert data["cmd"] == "search"
        assert data["agent"] == "kelvin"
        assert data["results"] == 5

    def test_format_handles_non_serializable(self) -> None:
        formatter = JSONLinesFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="", args=None, exc_info=None,
        )
        record.path_obj = Path("/tmp/test")
        line = formatter.format(record)
        data = json.loads(line)
        assert "/tmp/test" in data["path_obj"]

    def test_format_levels(self) -> None:
        formatter = JSONLinesFormatter()
        for level, expected in [
            (logging.DEBUG, "debug"),
            (logging.WARNING, "warning"),
            (logging.ERROR, "error"),
        ]:
            record = logging.LogRecord(
                name="test", level=level,
                pathname="", lineno=0, msg="", args=None, exc_info=None,
            )
            data = json.loads(formatter.format(record))
            assert data["level"] == expected


# ---------------------------------------------------------------------------
# Log directory resolution
# ---------------------------------------------------------------------------


class TestGetLogDir:
    def test_uses_env_var(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom-logs"
        with patch.dict(os.environ, {"AGENT_MEMORY_LOG_PATH": str(custom)}):
            result = get_log_dir()
        assert result == custom
        assert custom.exists()

    def test_creates_directory(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "c"
        with patch.dict(os.environ, {"AGENT_MEMORY_LOG_PATH": str(nested)}):
            result = get_log_dir()
        assert result == nested
        assert nested.exists()

    def test_default_when_no_env(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_MEMORY_LOG_PATH", None)
            result = get_log_dir()
        assert result == Path.home() / ".agent-memory"


# ---------------------------------------------------------------------------
# Logger setup
# ---------------------------------------------------------------------------


class TestLoggerSetup:
    def test_cli_logger_writes_to_file(self, log_dir_env: Path) -> None:
        logger = get_cli_logger()
        assert logger.name == CLI_LOGGER_NAME
        assert len(logger.handlers) == 1
        log_file = log_dir_env / CLI_LOG_FILE
        # Write a record
        logger.info("", extra={"cmd": "test"})
        logger.handlers[0].flush()
        assert log_file.exists()
        line = log_file.read_text().strip()
        data = json.loads(line)
        assert data["cmd"] == "test"

    def test_hooks_logger_writes_to_file(self, log_dir_env: Path) -> None:
        logger = get_hooks_logger()
        assert logger.name == HOOKS_LOGGER_NAME
        assert len(logger.handlers) == 1
        log_file = log_dir_env / HOOKS_LOG_FILE
        logger.info("", extra={"hook": "PreCompact"})
        logger.handlers[0].flush()
        assert log_file.exists()
        data = json.loads(log_file.read_text().strip())
        assert data["hook"] == "PreCompact"

    def test_no_duplicate_handlers(self, log_dir_env: Path) -> None:
        logger1 = get_cli_logger()
        logger2 = get_cli_logger()
        assert logger1 is logger2
        assert len(logger1.handlers) == 1

    def test_loggers_are_independent(self, log_dir_env: Path) -> None:
        cli_logger = get_cli_logger()
        hooks_logger = get_hooks_logger()
        assert cli_logger is not hooks_logger
        assert cli_logger.name != hooks_logger.name


# ---------------------------------------------------------------------------
# log_cli_command
# ---------------------------------------------------------------------------


class TestLogCliCommand:
    def test_success_log(self, log_dir_env: Path) -> None:
        log_cli_command(
            cmd="search",
            args={"query": "test", "scope": "own"},
            duration_ms=142.3,
            exit_code=0,
            result_count=3,
        )
        log_file = log_dir_env / CLI_LOG_FILE
        # Flush
        logging.getLogger(CLI_LOGGER_NAME).handlers[0].flush()
        data = json.loads(log_file.read_text().strip())
        assert data["cmd"] == "search"
        assert data["cmd_args"] == {"query": "test", "scope": "own"}
        assert data["duration_ms"] == 142.3
        assert data["exit"] == 0
        assert data["results"] == 3
        assert data["level"] == "info"
        assert "error" not in data

    def test_error_log(self, log_dir_env: Path) -> None:
        log_cli_command(
            cmd="validate",
            exit_code=1,
            error="Frontmatter missing",
        )
        log_file = log_dir_env / CLI_LOG_FILE
        logging.getLogger(CLI_LOGGER_NAME).handlers[0].flush()
        data = json.loads(log_file.read_text().strip())
        assert data["level"] == "error"
        assert data["exit"] == 1
        assert data["error"] == "Frontmatter missing"

    def test_uses_agent_id_from_env(self, log_dir_env: Path) -> None:
        with patch.dict(os.environ, {"AGENT_ID": "test-agent"}):
            log_cli_command(cmd="ls")
        logging.getLogger(CLI_LOGGER_NAME).handlers[0].flush()
        data = json.loads((log_dir_env / CLI_LOG_FILE).read_text().strip())
        assert data["agent"] == "test-agent"

    def test_unknown_agent_when_no_env(self, log_dir_env: Path) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_ID", None)
            log_cli_command(cmd="ls")
        logging.getLogger(CLI_LOGGER_NAME).handlers[0].flush()
        data = json.loads((log_dir_env / CLI_LOG_FILE).read_text().strip())
        assert data["agent"] == "unknown"

    def test_minimal_args(self, log_dir_env: Path) -> None:
        log_cli_command(cmd="init")
        logging.getLogger(CLI_LOGGER_NAME).handlers[0].flush()
        data = json.loads((log_dir_env / CLI_LOG_FILE).read_text().strip())
        assert data["cmd"] == "init"
        assert data["exit"] == 0
        assert "cmd_args" not in data
        assert "duration_ms" not in data
        assert "results" not in data

    def test_duration_rounding(self, log_dir_env: Path) -> None:
        log_cli_command(cmd="search", duration_ms=142.3456789)
        logging.getLogger(CLI_LOGGER_NAME).handlers[0].flush()
        data = json.loads((log_dir_env / CLI_LOG_FILE).read_text().strip())
        assert data["duration_ms"] == 142.3


# ---------------------------------------------------------------------------
# log_hook_event
# ---------------------------------------------------------------------------


class TestLogHookEvent:
    def test_success_hook(self, log_dir_env: Path) -> None:
        log_hook_event(
            hook="SubagentStart",
            outcome="ok",
            session_id="abc123",
            agent_type="github-ops",
            context_bytes=1240,
            duration_ms=350.0,
        )
        log_file = log_dir_env / HOOKS_LOG_FILE
        logging.getLogger(HOOKS_LOGGER_NAME).handlers[0].flush()
        data = json.loads(log_file.read_text().strip())
        assert data["hook"] == "SubagentStart"
        assert data["outcome"] == "ok"
        assert data["session"] == "abc123"
        assert data["agent_type"] == "github-ops"
        assert data["context_bytes"] == 1240
        assert data["duration_ms"] == 350.0
        assert data["level"] == "info"

    def test_error_hook(self, log_dir_env: Path) -> None:
        log_hook_event(
            hook="PreCompact",
            outcome="error",
            error="memory CLI not found",
        )
        logging.getLogger(HOOKS_LOGGER_NAME).handlers[0].flush()
        data = json.loads((log_dir_env / HOOKS_LOG_FILE).read_text().strip())
        assert data["level"] == "error"
        assert data["outcome"] == "error"
        assert data["error"] == "memory CLI not found"

    def test_skip_hook(self, log_dir_env: Path) -> None:
        log_hook_event(
            hook="SubagentStop",
            outcome="skip",
            agent_type="router",
        )
        logging.getLogger(HOOKS_LOGGER_NAME).handlers[0].flush()
        data = json.loads((log_dir_env / HOOKS_LOG_FILE).read_text().strip())
        assert data["outcome"] == "skip"
        assert data["level"] == "info"

    def test_entries_written(self, log_dir_env: Path) -> None:
        log_hook_event(
            hook="SubagentStop",
            outcome="ok",
            entries_written=1,
        )
        logging.getLogger(HOOKS_LOGGER_NAME).handlers[0].flush()
        data = json.loads((log_dir_env / HOOKS_LOG_FILE).read_text().strip())
        assert data["entries_written"] == 1

    def test_minimal_hook(self, log_dir_env: Path) -> None:
        log_hook_event(hook="SessionStart", outcome="ok")
        logging.getLogger(HOOKS_LOGGER_NAME).handlers[0].flush()
        data = json.loads((log_dir_env / HOOKS_LOG_FILE).read_text().strip())
        assert data["hook"] == "SessionStart"
        assert data["outcome"] == "ok"
        assert "session" not in data
        assert "agent_type" not in data


# ---------------------------------------------------------------------------
# CommandTimer
# ---------------------------------------------------------------------------


class TestCommandTimer:
    def test_timer_logs_on_exit(self, log_dir_env: Path) -> None:
        with CommandTimer("search", args={"query": "test"}) as timer:
            timer.result_count = 5
            time.sleep(0.01)  # Ensure measurable duration

        logging.getLogger(CLI_LOGGER_NAME).handlers[0].flush()
        data = json.loads((log_dir_env / CLI_LOG_FILE).read_text().strip())
        assert data["cmd"] == "search"
        assert data["results"] == 5
        assert data["exit"] == 0
        assert data["duration_ms"] >= 5  # At least 5ms from sleep

    def test_timer_logs_error(self, log_dir_env: Path) -> None:
        with pytest.raises(ValueError, match="test error"):
            with CommandTimer("validate"):
                raise ValueError("test error")

        logging.getLogger(CLI_LOGGER_NAME).handlers[0].flush()
        data = json.loads((log_dir_env / CLI_LOG_FILE).read_text().strip())
        assert data["exit"] == 1
        assert data["error"] == "test error"

    def test_timer_manual_error(self, log_dir_env: Path) -> None:
        with CommandTimer("new") as timer:
            timer.exit_code = 1
            timer.error = "file exists"

        logging.getLogger(CLI_LOGGER_NAME).handlers[0].flush()
        data = json.loads((log_dir_env / CLI_LOG_FILE).read_text().strip())
        assert data["exit"] == 1
        assert data["error"] == "file exists"

    def test_timer_no_args(self, log_dir_env: Path) -> None:
        with CommandTimer("ls"):
            pass

        logging.getLogger(CLI_LOGGER_NAME).handlers[0].flush()
        data = json.loads((log_dir_env / CLI_LOG_FILE).read_text().strip())
        assert data["cmd"] == "ls"
        assert data["exit"] == 0


# ---------------------------------------------------------------------------
# Multiple log entries
# ---------------------------------------------------------------------------


class TestMultipleEntries:
    def test_multiple_cli_entries(self, log_dir_env: Path) -> None:
        log_cli_command(cmd="search", result_count=3)
        log_cli_command(cmd="new", exit_code=0)
        log_cli_command(cmd="validate", exit_code=1, error="bad frontmatter")

        logging.getLogger(CLI_LOGGER_NAME).handlers[0].flush()
        lines = (log_dir_env / CLI_LOG_FILE).read_text().strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            data = json.loads(line)
            assert "ts" in data
            assert "cmd" in data

    def test_cli_and_hooks_separate(self, log_dir_env: Path) -> None:
        log_cli_command(cmd="search")
        log_hook_event(hook="PreCompact", outcome="ok")

        logging.getLogger(CLI_LOGGER_NAME).handlers[0].flush()
        logging.getLogger(HOOKS_LOGGER_NAME).handlers[0].flush()

        cli_data = json.loads(
            (log_dir_env / CLI_LOG_FILE).read_text().strip()
        )
        hook_data = json.loads(
            (log_dir_env / HOOKS_LOG_FILE).read_text().strip()
        )
        assert "cmd" in cli_data
        assert "hook" not in cli_data
        assert "hook" in hook_data
        assert "cmd" not in hook_data


# ===========================================================================
# Log viewer + CLI instrumentation tests (PR #39)
# ===========================================================================

# ---------------------------------------------------------------------------
# parse_log_entry
# ---------------------------------------------------------------------------


class TestParseLogEntry:
    """Tests for log_viewer.parse_log_entry."""

    def test_valid_entry_legacy_format(self) -> None:
        line = json.dumps(
            {
                "timestamp": "2026-02-14T11:00:00+00:00",
                "level": "INFO",
                "message": "command started",
                "command": "search",
                "agent_id": "nexus",
                "cmd_args": {"query": "test"},
            }
        )
        entry = parse_log_entry(line)
        assert entry is not None
        assert entry.command == "search"
        assert entry.agent_id == "nexus"
        assert entry.cmd_args == {"query": "test"}

    def test_valid_entry_kelvin_format(self) -> None:
        line = json.dumps(
            {
                "ts": "2026-02-14T11:00:00+00:00",
                "level": "info",
                "cmd": "search",
                "agent": "nexus",
                "cmd_args": {"query": "test"},
                "duration_ms": 42.0,
                "results": 3,
                "exit": 0,
            }
        )
        entry = parse_log_entry(line)
        assert entry is not None
        assert entry.command == "search"
        assert entry.agent_id == "nexus"
        assert entry.timestamp == "2026-02-14T11:00:00+00:00"
        assert entry.level == "INFO"
        assert entry.duration_ms == 42.0

    def test_empty_line_returns_none(self) -> None:
        assert parse_log_entry("") is None
        assert parse_log_entry("   ") is None

    def test_invalid_json_returns_none(self) -> None:
        assert parse_log_entry("not json at all") is None
        assert parse_log_entry("{broken") is None

    def test_minimal_entry(self) -> None:
        line = json.dumps({"timestamp": "t", "level": "INFO", "message": "m"})
        entry = parse_log_entry(line)
        assert entry is not None
        assert entry.command == ""
        assert entry.duration_ms == 0.0


# ---------------------------------------------------------------------------
# read_log_file
# ---------------------------------------------------------------------------


class TestReadLogFile:
    """Tests for log_viewer.read_log_file."""

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        result = read_log_file(tmp_path / "nonexistent.log")
        assert result == []

    def test_reads_entries(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        lines = [
            json.dumps({"timestamp": "t1", "level": "INFO", "message": "m1"}),
            json.dumps({"timestamp": "t2", "level": "ERROR", "message": "m2"}),
        ]
        log_file.write_text("\n".join(lines) + "\n")
        entries = read_log_file(log_file)
        assert len(entries) == 2
        assert entries[0].level == "INFO"
        assert entries[1].level == "ERROR"


# ---------------------------------------------------------------------------
# filter_entries
# ---------------------------------------------------------------------------


class TestFilterEntries:
    """Tests for log_viewer.filter_entries."""

    @pytest.fixture()
    def sample_entries(self) -> list[LogEntry]:
        return [
            LogEntry(
                timestamp="2026-02-14T10:00:00+00:00",
                level="INFO",
                message="command completed",
                command="search",
                agent_id="nexus",
            ),
            LogEntry(
                timestamp="2026-02-14T11:00:00+00:00",
                level="ERROR",
                message="command failed",
                command="new",
                agent_id="kelvin",
            ),
            LogEntry(
                timestamp="2026-02-15T09:00:00+00:00",
                level="INFO",
                message="command completed",
                command="ls",
                agent_id="nexus",
            ),
        ]

    def test_filter_by_level(self, sample_entries: list[LogEntry]) -> None:
        result = filter_entries(sample_entries, level="ERROR")
        assert len(result) == 1
        assert result[0].command == "new"

    def test_filter_by_level_case_insensitive(
        self,
        sample_entries: list[LogEntry],
    ) -> None:
        result = filter_entries(sample_entries, level="error")
        assert len(result) == 1

    def test_filter_by_since(self, sample_entries: list[LogEntry]) -> None:
        result = filter_entries(sample_entries, since="2026-02-15")
        assert len(result) == 1
        assert result[0].command == "ls"

    def test_filter_by_command(self, sample_entries: list[LogEntry]) -> None:
        result = filter_entries(sample_entries, command="search")
        assert len(result) == 1

    def test_filter_by_agent(self, sample_entries: list[LogEntry]) -> None:
        result = filter_entries(sample_entries, agent="kelvin")
        assert len(result) == 1
        assert result[0].command == "new"

    def test_no_filter_returns_all(self, sample_entries: list[LogEntry]) -> None:
        result = filter_entries(sample_entries)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# compute_stats
# ---------------------------------------------------------------------------


class TestComputeStats:
    """Tests for log_viewer.compute_stats."""

    def test_empty_entries(self) -> None:
        stats = compute_stats([])
        assert stats.total_entries == 0
        assert stats.total_commands == 0
        assert stats.error_rate == 0.0

    def test_command_counts(self) -> None:
        entries = [
            LogEntry(
                timestamp="t",
                level="INFO",
                message="command completed",
                command="search",
                duration_ms=10,
            ),
            LogEntry(
                timestamp="t",
                level="INFO",
                message="command completed",
                command="search",
                duration_ms=20,
            ),
            LogEntry(
                timestamp="t",
                level="INFO",
                message="command completed",
                command="ls",
                duration_ms=5,
            ),
        ]
        stats = compute_stats(entries)
        assert stats.total_commands == 3
        assert stats.command_counts["search"] == 2
        assert stats.command_counts["ls"] == 1

    def test_error_rate(self) -> None:
        entries = [
            LogEntry(
                timestamp="t",
                level="INFO",
                message="command completed",
                command="search",
                duration_ms=10,
            ),
            LogEntry(
                timestamp="t", level="ERROR", message="command failed", command="new"
            ),
            LogEntry(
                timestamp="t",
                level="INFO",
                message="command completed",
                command="ls",
                duration_ms=5,
            ),
        ]
        stats = compute_stats(entries)
        assert stats.total_errors == 1
        assert stats.total_commands == 2
        assert stats.error_rate == 50.0

    def test_search_terms_collected(self) -> None:
        entries = [
            LogEntry(
                timestamp="t",
                level="INFO",
                message="",
                command="search",
                cmd_args={"query": "deployment"},
            ),
            LogEntry(
                timestamp="t",
                level="INFO",
                message="",
                command="search",
                cmd_args={"query": "logging"},
            ),
        ]
        stats = compute_stats(entries)
        assert stats.search_terms == ["deployment", "logging"]

    def test_avg_duration(self) -> None:
        entries = [
            LogEntry(
                timestamp="t",
                level="INFO",
                message="command completed",
                command="search",
                duration_ms=10,
            ),
            LogEntry(
                timestamp="t",
                level="INFO",
                message="command completed",
                command="ls",
                duration_ms=30,
            ),
        ]
        stats = compute_stats(entries)
        assert stats.avg_duration_ms == 20.0

    def test_kelvin_format_detected_as_completed(self) -> None:
        """Kelvin's format uses duration_ms > 0 as completion indicator."""
        entries = [
            LogEntry(
                timestamp="t",
                level="INFO",
                message="",
                command="search",
                duration_ms=42.0,
            ),
        ]
        stats = compute_stats(entries)
        assert stats.total_commands == 1
        assert stats.command_counts["search"] == 1


# ---------------------------------------------------------------------------
# format_entry_human
# ---------------------------------------------------------------------------


class TestFormatEntryHuman:
    """Tests for log_viewer.format_entry_human."""

    def test_basic_format(self) -> None:
        entry = LogEntry(
            timestamp="2026-02-14T11:07:28.123+00:00",
            level="INFO",
            message="command completed",
            command="search",
            duration_ms=42.0,
            result_summary={"results": 3},
        )
        output = format_entry_human(entry)
        assert "2026-02-14 11:07:28" in output
        assert "[INFO]" in output
        assert "search" in output
        assert "42ms" in output

    def test_error_format(self) -> None:
        entry = LogEntry(
            timestamp="2026-02-14T11:06:58.000+00:00",
            level="ERROR",
            message="command failed",
            command="new",
            result_summary={
                "error_type": "FileExistsError",
                "error_message": "file already exists",
            },
        )
        output = format_entry_human(entry)
        assert "[ERROR]" in output
        assert "FileExistsError" in output

    def test_search_shows_query(self) -> None:
        entry = LogEntry(
            timestamp="2026-02-14T11:07:28.000+00:00",
            level="INFO",
            message="",
            command="search",
            cmd_args={"query": "logging design"},
        )
        output = format_entry_human(entry)
        assert '"logging design"' in output

    def test_kelvin_format_results(self) -> None:
        """Kelvin's format has top-level 'results' field in raw data."""
        entry = LogEntry(
            timestamp="2026-02-14T11:07:28.000+00:00",
            level="INFO",
            message="",
            command="search",
            duration_ms=42.0,
            raw={"results": 5},
        )
        output = format_entry_human(entry)
        assert "5 results" in output

    def test_kelvin_format_error(self) -> None:
        """Kelvin's format has top-level 'error' field in raw data."""
        entry = LogEntry(
            timestamp="2026-02-14T11:07:28.000+00:00",
            level="ERROR",
            message="",
            command="new",
            duration_ms=5.0,
            raw={"error": "FileExistsError: exists"},
        )
        output = format_entry_human(entry)
        assert "FileExistsError" in output


# ---------------------------------------------------------------------------
# CLI log command integration tests
# ---------------------------------------------------------------------------


class TestLogCommand:
    """Integration tests for the memory log CLI command."""

    @pytest.fixture()
    def log_env(self, tmp_path: Path) -> dict[str, str]:
        log_dir = tmp_path / "log-data"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "cli.log"
        entries = [
            {
                "ts": "2026-02-14T10:00:00+00:00",
                "level": "info",
                "cmd": "search",
                "agent": "nexus",
                "cmd_args": {"query": "test"},
                "duration_ms": 42.0,
                "results": 3,
                "exit": 0,
            },
            {
                "ts": "2026-02-14T10:01:00+00:00",
                "level": "error",
                "cmd": "new",
                "agent": "nexus",
                "duration_ms": 5.0,
                "exit": 1,
                "error": "FileExistsError: exists",
            },
        ]
        lines = [json.dumps(e) for e in entries]
        log_file.write_text("\n".join(lines) + "\n")
        return {"AGENT_MEMORY_LOG_PATH": str(log_dir)}

    def test_default_output(self, log_env: dict[str, str]) -> None:
        runner = CliRunner()
        with patch.dict(os.environ, log_env):
            result = runner.invoke(cli, ["log"])
        assert result.exit_code == 0
        assert "search" in result.output
        assert "[INFO]" in result.output

    def test_level_filter(self, log_env: dict[str, str]) -> None:
        runner = CliRunner()
        with patch.dict(os.environ, log_env):
            result = runner.invoke(cli, ["log", "--level", "error"])
        assert result.exit_code == 0
        assert "[ERROR]" in result.output
        lines = [ln for ln in result.output.strip().splitlines() if ln.strip()]
        assert len(lines) == 1

    def test_stats_output(self, log_env: dict[str, str]) -> None:
        runner = CliRunner()
        with patch.dict(os.environ, log_env):
            result = runner.invoke(cli, ["log", "--stats"])
        assert result.exit_code == 0
        assert "Total entries: 2" in result.output
        assert "Errors: 1" in result.output

    def test_json_output(self, log_env: dict[str, str]) -> None:
        runner = CliRunner()
        with patch.dict(os.environ, log_env):
            result = runner.invoke(cli, ["--json-output", "log"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2

    def test_json_stats_output(self, log_env: dict[str, str]) -> None:
        runner = CliRunner()
        with patch.dict(os.environ, log_env):
            result = runner.invoke(cli, ["--json-output", "log", "--stats"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "total_commands" in data
        assert "command_counts" in data

    def test_no_log_file(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        runner = CliRunner()
        with patch.dict(os.environ, {"AGENT_MEMORY_LOG_PATH": str(empty_dir)}):
            result = runner.invoke(cli, ["log"])
        assert result.exit_code == 0
        assert "No log file found" in result.output

    def test_tail_limit(self, log_env: dict[str, str]) -> None:
        runner = CliRunner()
        with patch.dict(os.environ, log_env):
            result = runner.invoke(cli, ["log", "--tail", "1"])
        assert result.exit_code == 0
        lines = [ln for ln in result.output.strip().splitlines() if ln.strip()]
        assert len(lines) == 1

    def test_since_filter(self, log_env: dict[str, str]) -> None:
        runner = CliRunner()
        with patch.dict(os.environ, log_env):
            result = runner.invoke(cli, ["log", "--since", "2026-02-14T10:01"])
        assert result.exit_code == 0
        assert "[ERROR]" in result.output
