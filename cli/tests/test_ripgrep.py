"""Tests for ripgrep-based search: wrapper, enrichment, and CLI."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_memory.rg_parser import (
    build_rg_command,
    find_rg,
    load_frontmatter_summary,
    parse_rg_json,
)
from agent_memory.ripgrep import (
    _resolve_scope_paths,
    grep,
)


def _create_memory_tree(tmp_path: Path) -> Path:
    """Create a memory tree with searchable .md files."""
    base = tmp_path / "memory"
    kelvin_dir = base / "kelvin" / "atlas"
    kelvin_dir.mkdir(parents=True)
    shared_dir = base / "shared"
    shared_dir.mkdir(parents=True)
    sage_dir = base / "sage" / "atlas"
    sage_dir.mkdir(parents=True)

    (kelvin_dir / "nan-policy.md").write_text(
        "---\n"
        "description: NaN policy for data pipeline\n"
        "author: kelvin\n"
        "created: 2026-02-10T08:00:00Z\n"
        "updated: 2026-02-11T14:30:00Z\n"
        "tags: [data, nan, pipeline]\n"
        "confidence: established\n"
        "category: atlas\n"
        "status: active\n"
        "---\n"
        "# NaN Policy\n\n"
        "## Rationale\n"
        "Why missing values must not be filled.\n\n"
        "NaN represents genuine absence of data.\n"
        "Filling with zeros introduces silent bias.\n\n"
        "## Implementation\n"
        "How the pipeline enforces NaN propagation.\n\n"
        "Every transform checks for NaN via shift(1).\n"
    )

    (kelvin_dir / "shift-policy.md").write_text(
        "---\n"
        "description: Shift-one policy for z-score pipeline\n"
        "author: kelvin\n"
        "created: 2026-02-10T08:00:00Z\n"
        "updated: 2026-02-11T14:30:00Z\n"
        "tags: [data, zscore]\n"
        "related: ['[[nan-policy]]']\n"
        "confidence: working\n"
        "category: atlas\n"
        "status: active\n"
        "---\n"
        "# Shift-One Policy\n\n"
        "## Overview\n"
        "How shift(1) prevents lookahead bias.\n\n"
        "The expanding window z-score uses shift(1) to avoid leakage.\n"
    )

    (shared_dir / "conventions.md").write_text(
        "---\n"
        "description: Team coding conventions\n"
        "author: nexus\n"
        "created: 2026-02-10T08:00:00Z\n"
        "updated: 2026-02-10T08:00:00Z\n"
        "tags: [team, conventions]\n"
        "category: atlas\n"
        "status: active\n"
        "---\n"
        "# Conventions\n\n"
        "## Style Guide\n"
        "Code formatting and naming conventions.\n\n"
        "Use black for formatting. See [[nan-policy]] for data rules.\n"
    )

    (sage_dir / "prompts.md").write_text(
        "---\n"
        "description: Meta-prompt engineering patterns\n"
        "author: sage\n"
        "created: 2026-02-10T08:00:00Z\n"
        "updated: 2026-02-10T08:00:00Z\n"
        "tags: [prompts, meta]\n"
        "category: atlas\n"
        "status: active\n"
        "---\n"
        "# Prompt Engineering\n\n"
        "## Patterns\n"
        "Standard meta-prompt patterns.\n\n"
        "Use chain-of-thought for complex reasoning.\n"
    )

    return base


class TestFindRg:
    def test_finds_rg_when_installed(self) -> None:
        # rg should be installed on this system
        rg_path = find_rg()
        assert rg_path is not None
        assert "rg" in rg_path

    def test_raises_when_rg_missing(self) -> None:
        with patch.object(shutil, "which", return_value=None):
            with pytest.raises(FileNotFoundError, match="ripgrep.*not installed"):
                find_rg()


class TestResolveScopePaths:
    def test_scope_all(self, tmp_path: Path) -> None:
        paths = _resolve_scope_paths(tmp_path, "all", "")
        assert paths == [str(tmp_path)]

    def test_scope_own(self, tmp_path: Path) -> None:
        paths = _resolve_scope_paths(tmp_path, "own", "kelvin")
        assert paths == [str(tmp_path / "kelvin")]

    def test_scope_own_missing_agent_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Agent ID required"):
            _resolve_scope_paths(tmp_path, "own", "")

    def test_scope_shared(self, tmp_path: Path) -> None:
        paths = _resolve_scope_paths(tmp_path, "shared", "")
        assert paths == [str(tmp_path / "shared")]

    def test_scope_agent_prefix(self, tmp_path: Path) -> None:
        paths = _resolve_scope_paths(tmp_path, "agent:sage", "")
        assert paths == [str(tmp_path / "sage")]

    def test_scope_agent_empty_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Agent ID required"):
            _resolve_scope_paths(tmp_path, "agent:", "")

    def test_invalid_scope(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Invalid scope"):
            _resolve_scope_paths(tmp_path, "bogus", "")


class TestBuildRgCommand:
    def test_basic_command(self) -> None:
        cmd = build_rg_command("/usr/bin/rg", "pattern", ["/tmp/mem"])
        assert cmd[0] == "/usr/bin/rg"
        assert "--json" in cmd
        assert "--glob" in cmd
        assert "*.md" in cmd
        assert "pattern" in cmd
        assert "/tmp/mem" in cmd

    def test_case_insensitive(self) -> None:
        cmd = build_rg_command(
            "/usr/bin/rg", "test", ["/tmp"], case_insensitive=True
        )
        assert "-i" in cmd

    def test_fixed_strings(self) -> None:
        cmd = build_rg_command(
            "/usr/bin/rg", "test()", ["/tmp"], fixed_strings=True
        )
        assert "--fixed-strings" in cmd

    def test_context_lines(self) -> None:
        cmd = build_rg_command(
            "/usr/bin/rg", "test", ["/tmp"], context=3
        )
        assert "-C" in cmd
        assert "3" in cmd

    def test_before_after_context(self) -> None:
        cmd = build_rg_command(
            "/usr/bin/rg", "test", ["/tmp"],
            context_before=2, context_after=5,
        )
        assert "-B" in cmd
        assert "2" in cmd
        assert "-A" in cmd
        assert "5" in cmd

    def test_context_overrides_before_after(self) -> None:
        cmd = build_rg_command(
            "/usr/bin/rg", "test", ["/tmp"],
            context=3, context_before=1, context_after=2,
        )
        # When context is set, -B and -A should not be present
        assert "-C" in cmd
        assert "-B" not in cmd
        assert "-A" not in cmd


class TestParseRgJson:
    def test_parses_match_lines(self) -> None:
        rg_output = (
            '{"type":"begin","data":{"path":{"text":"/tmp/file.md"}}}\n'
            '{"type":"match","data":{"path":{"text":"/tmp/file.md"},'
            '"lines":{"text":"NaN represents absence\\n"},'
            '"line_number":5}}\n'
            '{"type":"end","data":{"path":{"text":"/tmp/file.md"}}}\n'
        )
        result = parse_rg_json(rg_output)
        assert "/tmp/file.md" in result
        matches = result["/tmp/file.md"]
        assert len(matches) == 1
        line_number, line_text, is_context = matches[0]
        assert line_number == 5
        assert line_text == "NaN represents absence"
        assert is_context is False

    def test_parses_context_lines(self) -> None:
        rg_output = (
            '{"type":"context","data":{"path":{"text":"/tmp/file.md"},'
            '"lines":{"text":"before line\\n"},"line_number":4}}\n'
            '{"type":"match","data":{"path":{"text":"/tmp/file.md"},'
            '"lines":{"text":"NaN value\\n"},"line_number":5}}\n'
            '{"type":"context","data":{"path":{"text":"/tmp/file.md"},'
            '"lines":{"text":"after line\\n"},"line_number":6}}\n'
        )
        result = parse_rg_json(rg_output)
        matches = result["/tmp/file.md"]
        assert len(matches) == 3
        assert matches[0][2] is True   # is_context
        assert matches[1][2] is False  # is_context
        assert matches[2][2] is True   # is_context

    def test_multiple_files(self) -> None:
        rg_output = (
            '{"type":"match","data":{"path":{"text":"/tmp/a.md"},'
            '"lines":{"text":"match a\\n"},"line_number":1}}\n'
            '{"type":"match","data":{"path":{"text":"/tmp/b.md"},'
            '"lines":{"text":"match b\\n"},"line_number":2}}\n'
        )
        result = parse_rg_json(rg_output)
        assert len(result) == 2
        assert "/tmp/a.md" in result
        assert "/tmp/b.md" in result

    def test_empty_output(self) -> None:
        result = parse_rg_json("")
        assert result == {}

    def test_ignores_summary_lines(self) -> None:
        rg_output = (
            '{"type":"summary","data":{"elapsed_total":{"secs":0}}}\n'
        )
        result = parse_rg_json(rg_output)
        assert result == {}

    def test_handles_malformed_json(self) -> None:
        rg_output = "not json at all\n"
        result = parse_rg_json(rg_output)
        assert result == {}


class TestLoadFrontmatterSummary:
    def test_loads_valid_file(self, tmp_path: Path) -> None:
        md_file = tmp_path / "test.md"
        md_file.write_text(
            "---\n"
            "description: Test entry\n"
            "author: kelvin\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-10T08:00:00Z\n"
            "tags: [data, test]\n"
            "confidence: established\n"
            "category: atlas\n"
            "status: active\n"
            "---\n"
            "# Test\n"
        )
        summary = load_frontmatter_summary(str(md_file))
        assert summary["description"] == "Test entry"
        assert summary["author"] == "kelvin"
        assert summary["tags"] == ["data", "test"]
        assert summary["confidence"] == "established"

    def test_returns_empty_dict_for_missing_file(self) -> None:
        summary = load_frontmatter_summary("/nonexistent/file.md")
        assert summary == {}

    def test_returns_empty_dict_for_invalid_frontmatter(
        self, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "bad.md"
        md_file.write_text("No frontmatter here")
        summary = load_frontmatter_summary(str(md_file))
        assert summary == {}


class TestGrep:
    def test_basic_search(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("NaN", base)
        assert len(results) > 0
        paths = [r.path for r in results]
        assert any("nan-policy" in p for p in paths)

    def test_results_have_frontmatter(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("NaN", base)
        for r in results:
            assert r.frontmatter.get("description")
            assert r.frontmatter.get("author")

    def test_match_count(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("NaN", base)
        for r in results:
            assert r.match_count > 0

    def test_scope_own(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("shift", base, scope="own", agent_id="kelvin")
        for r in results:
            assert r.path.startswith("kelvin/")

    def test_scope_shared(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("conventions", base, scope="shared")
        assert len(results) > 0
        for r in results:
            assert r.path.startswith("shared/")

    def test_scope_agent(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("prompt", base, scope="agent:sage")
        assert len(results) > 0
        for r in results:
            assert r.path.startswith("sage/")

    def test_scope_own_no_agent_id(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        with pytest.raises(ValueError, match="Agent ID required"):
            grep("test", base, scope="own", agent_id="")

    def test_no_matches(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("xyzzyzzyxyz", base)
        assert results == []

    def test_empty_pattern_no_mode(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("", base)
        assert results == []

    def test_case_insensitive(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("nan", base, case_insensitive=True)
        assert len(results) > 0

    def test_fixed_strings(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("shift(1)", base, fixed_strings=True)
        assert len(results) > 0
        paths = [r.path for r in results]
        assert any("shift-policy" in p or "nan-policy" in p for p in paths)

    def test_regex_pattern(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("shift\\(1\\)", base)
        assert len(results) > 0

    def test_context_lines(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("shift(1)", base, context=2, fixed_strings=True)
        assert len(results) > 0
        for r in results:
            has_context = any(m.is_context for m in r.matches)
            # Context lines should be present if there are surrounding lines
            assert len(r.matches) > r.match_count or not has_context

    def test_tag_search(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("", base, tag="zscore")
        assert len(results) > 0
        assert any("shift-policy" in r.path for r in results)

    def test_links_to_search(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("", base, links_to="nan-policy")
        assert len(results) > 0
        # conventions.md and shift-policy.md both reference [[nan-policy]]
        paths = [r.path for r in results]
        assert any("conventions" in p or "shift-policy" in p for p in paths)

    def test_results_sorted_by_path(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("data", base, case_insensitive=True)
        if len(results) > 1:
            paths = [r.path for r in results]
            assert paths == sorted(paths)

    def test_relative_paths(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        results = grep("NaN", base)
        for r in results:
            assert not r.path.startswith("/")
            assert str(tmp_path) not in r.path

    def test_rg_not_installed(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        with patch.object(shutil, "which", return_value=None):
            with pytest.raises(FileNotFoundError, match="ripgrep"):
                grep("test", base)


class TestGrepCLI:
    """Integration tests for the grep CLI command."""

    def test_grep_human_output(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["grep", "NaN", "--base", str(base)]
        )
        assert result.exit_code == 0
        assert "nan-policy" in result.output
        assert "match(es)" in result.output

    def test_grep_json_output(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json-output", "grep", "NaN", "--base", str(base)]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) > 0
        assert "path" in data[0]
        assert "match_count" in data[0]
        assert "frontmatter" in data[0]
        assert "matches" in data[0]

    def test_grep_no_matches(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["grep", "xyzzyzzyxyz", "--base", str(base)]
        )
        assert result.exit_code == 0
        assert "No matches found" in result.output

    def test_grep_json_no_matches(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "grep", "xyzzyzzyxyz", "--base", str(base)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == []

    def test_grep_with_scope(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "grep", "shift", "--base", str(base),
             "--scope", "agent:kelvin"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        for item in data:
            assert item["path"].startswith("kelvin/")

    def test_grep_scope_own(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "grep", "shift", "--base", str(base),
             "--scope", "own"],
            env={"AGENT_ID": "kelvin"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) > 0

    def test_grep_scope_own_requires_env(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner(env={"AGENT_ID": ""})
        result = runner.invoke(
            cli,
            ["grep", "test", "--base", str(base), "--scope", "own"],
        )
        assert result.exit_code == 1
        assert "Agent ID required" in result.output

    def test_grep_tag_search(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "grep", "--tag", "zscore",
             "--base", str(base)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) > 0
        assert any("shift-policy" in item["path"] for item in data)

    def test_grep_links_to(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "grep", "--links-to", "nan-policy",
             "--base", str(base)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) > 0

    def test_grep_case_insensitive(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "grep", "nan", "-i", "--base", str(base)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) > 0

    def test_grep_fixed_strings(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "grep", "shift(1)", "-F",
             "--base", str(base)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) > 0

    def test_grep_context_lines(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "grep", "shift(1)", "-F", "-C", "2",
             "--base", str(base)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) > 0
        # With context, should have context lines
        all_matches = []
        for item in data:
            all_matches.extend(item["matches"])
        context_lines = [m for m in all_matches if m["is_context"]]
        assert len(context_lines) > 0

    def test_grep_before_after_context(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "grep", "NaN", "-B", "1", "-A", "1",
             "--base", str(base)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) > 0

    def test_grep_invalid_base(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["grep", "test", "--base", str(tmp_path / "nonexistent")],
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_grep_no_pattern_no_mode(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["grep", "--base", str(base)],
        )
        assert result.exit_code == 1
        assert "Pattern required" in result.output

    def test_grep_help(self) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["grep", "--help"])
        assert result.exit_code == 0
        assert "--scope" in result.output
        assert "--tag" in result.output
        assert "--links-to" in result.output
        assert "-C" in result.output or "--context" in result.output
        assert "-i" in result.output or "--ignore-case" in result.output
        assert "-F" in result.output or "--fixed-strings" in result.output

    def test_grep_frontmatter_in_human_output(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["grep", "NaN", "--base", str(base)]
        )
        assert result.exit_code == 0
        # Should show description and author in human output
        assert "author:" in result.output
        assert "NaN policy" in result.output

    def test_grep_json_match_structure(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "grep", "NaN", "--base", str(base)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        item = data[0]
        # Verify match structure
        match = item["matches"][0]
        assert "line_number" in match
        assert "line_text" in match
        assert "is_context" in match
        # Verify frontmatter structure
        fm = item["frontmatter"]
        assert "description" in fm
        assert "author" in fm
        assert "tags" in fm

    def test_grep_scope_shared(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "grep", "conventions", "--base", str(base),
             "--scope", "shared"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) > 0
        for item in data:
            assert item["path"].startswith("shared/")
