"""Tests for multi-source file discovery and search integration."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_memory.cli import cli
from agent_memory.sources import (
    SourceFile,
    _synthetic_frontmatter,
    collect_all_source_files,
    enumerate_source_files,
    get_configured_sources,
    parse_source_file,
)

MP = pytest.MonkeyPatch


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _write_config(tmp_path: Path, content: str) -> Path:
    config_dir = tmp_path / ".config" / "agent-memory"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.yaml"
    config_file.write_text(textwrap.dedent(content), encoding="utf-8")
    return config_file


def _patch_cfg(mp: MP, config_path: Path) -> None:
    mp.setattr("agent_memory.config.CONFIG_FILE", config_path)


def _make_rule(tmp_path: Path, name: str, content: str) -> Path:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir(exist_ok=True)
    f = rules_dir / name
    f.write_text(content, encoding="utf-8")
    return f


def _make_skill(tmp_path: Path, skill_name: str, content: str) -> Path:
    skill_dir = tmp_path / "skills" / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    f = skill_dir / "SKILL.md"
    f.write_text(content, encoding="utf-8")
    return f


# -------------------------------------------------------------------
# enumerate_source_files
# -------------------------------------------------------------------


class TestEnumerateSourceFiles:
    def test_flat_directory(self, tmp_path: Path) -> None:
        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "rule-a.md").write_text("## Rule A\nContent.")
        (rules / "rule-b.md").write_text("## Rule B\nContent.")
        (rules / "not-md.txt").write_text("ignored")

        files = enumerate_source_files(rules, "rules")
        assert len(files) == 2
        assert files[0].rel_path == "rule-a.md"
        assert files[0].source_label == "rules"
        assert files[1].rel_path == "rule-b.md"

    def test_nested_directory(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        (skills / "agent_lessons").mkdir(parents=True)
        (skills / "agent_lessons" / "SKILL.md").write_text("## Lessons")
        (skills / "agent_patterns").mkdir(parents=True)
        (skills / "agent_patterns" / "SKILL.md").write_text("## Patterns")

        files = enumerate_source_files(skills, "skills")
        assert len(files) == 2
        assert "agent_lessons/SKILL.md" in [f.rel_path for f in files]

    def test_excludes_underscore_dirs(self, tmp_path: Path) -> None:
        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "visible.md").write_text("visible")
        archive = rules / "_archive"
        archive.mkdir()
        (archive / "hidden.md").write_text("hidden")

        files = enumerate_source_files(rules, "rules")
        assert len(files) == 1
        assert files[0].rel_path == "visible.md"

    def test_nonexistent_path(self, tmp_path: Path) -> None:
        files = enumerate_source_files(tmp_path / "nope", "test")
        assert files == []


# -------------------------------------------------------------------
# _synthetic_frontmatter
# -------------------------------------------------------------------


class TestSyntheticFrontmatter:
    def test_extracts_h1(self, tmp_path: Path) -> None:
        sf = SourceFile(
            path=tmp_path / "test.md",
            rel_path="test.md",
            source_label="rules",
            source_root=tmp_path,
        )
        fm, body = _synthetic_frontmatter(sf, "# My Rule Title\n\nContent.")
        assert fm.description == "My Rule Title"
        assert "rules" in fm.tags
        assert "test" in fm.tags
        assert body == "# My Rule Title\n\nContent."

    def test_extracts_h2(self, tmp_path: Path) -> None:
        sf = SourceFile(
            path=tmp_path / "test.md",
            rel_path="test.md",
            source_label="skills",
            source_root=tmp_path,
        )
        fm, _ = _synthetic_frontmatter(sf, "## Section Heading\nContent.")
        assert fm.description == "Section Heading"

    def test_extracts_plain_text(self, tmp_path: Path) -> None:
        sf = SourceFile(
            path=tmp_path / "test.md",
            rel_path="test.md",
            source_label="rules",
            source_root=tmp_path,
        )
        fm, _ = _synthetic_frontmatter(sf, "Plain text first line.\nMore.")
        assert fm.description == "Plain text first line."

    def test_empty_file(self, tmp_path: Path) -> None:
        sf = SourceFile(
            path=tmp_path / "test.md",
            rel_path="test.md",
            source_label="rules",
            source_root=tmp_path,
        )
        fm, _ = _synthetic_frontmatter(sf, "")
        assert fm.description == ""

    def test_truncates_long_first_line(self, tmp_path: Path) -> None:
        sf = SourceFile(
            path=tmp_path / "test.md",
            rel_path="test.md",
            source_label="rules",
            source_root=tmp_path,
        )
        long_line = "x" * 200
        fm, _ = _synthetic_frontmatter(sf, long_line)
        assert len(fm.description) == 120


# -------------------------------------------------------------------
# parse_source_file
# -------------------------------------------------------------------


class TestParseSourceFile:
    def test_with_frontmatter(self, tmp_path: Path) -> None:
        f = _make_rule(tmp_path, "rule.md", textwrap.dedent("""\
            ---
            description: A proper rule
            author: test-agent
            created: 2026-01-01
            updated: 2026-01-01
            ---
            ## Section
            Content here.
        """))
        sf = SourceFile(
            path=f, rel_path="rule.md",
            source_label="rules", source_root=tmp_path / "rules",
        )
        entry = parse_source_file(sf)
        assert entry is not None
        assert entry.frontmatter.description == "A proper rule"
        assert "Section" in entry.body

    def test_without_frontmatter(self, tmp_path: Path) -> None:
        f = _make_rule(tmp_path, "rule.md",
                       "## No Gundecking\nDon't fake work.")
        sf = SourceFile(
            path=f, rel_path="rule.md",
            source_label="rules", source_root=tmp_path / "rules",
        )
        entry = parse_source_file(sf)
        assert entry is not None
        assert entry.frontmatter.description == "No Gundecking"
        assert "## No Gundecking" in entry.body

    def test_unreadable_file(self, tmp_path: Path) -> None:
        sf = SourceFile(
            path=tmp_path / "missing.md",
            rel_path="missing.md",
            source_label="rules",
            source_root=tmp_path,
        )
        assert parse_source_file(sf) is None


# -------------------------------------------------------------------
# get_configured_sources
# -------------------------------------------------------------------


class TestGetConfiguredSources:
    def test_returns_sources_from_config(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, """\
            version: 1
            repos:
              additional:
                - path: /tmp/rules
                  label: rules
                  read_only: true
                - path: /tmp/skills
                  label: skills
        """)
        _patch_cfg(monkeypatch, cf)
        sources = get_configured_sources()
        assert len(sources) == 2
        assert sources[0]["path"] == "/tmp/rules"
        assert sources[0]["label"] == "rules"

    def test_empty_without_config(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        _patch_cfg(monkeypatch, tmp_path / "x" / "config.yaml")
        assert get_configured_sources() == []


# -------------------------------------------------------------------
# collect_all_source_files
# -------------------------------------------------------------------


class TestCollectAllSourceFiles:
    def test_collects_from_multiple_sources(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "rule-1.md").write_text("## Rule One\nDo this.")

        skills = tmp_path / "skills"
        (skills / "my_skill").mkdir(parents=True)
        (skills / "my_skill" / "SKILL.md").write_text(
            "## Patterns\nPattern content."
        )

        cf = _write_config(tmp_path, f"""\
            version: 1
            repos:
              additional:
                - path: {rules}
                  label: rules
                - path: {skills}
                  label: skills
        """)
        _patch_cfg(monkeypatch, cf)

        entries = collect_all_source_files()
        assert len(entries) == 2
        labels = [e.file.source_label for e in entries]
        assert "rules" in labels
        assert "skills" in labels

    def test_empty_when_no_config(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        _patch_cfg(monkeypatch, tmp_path / "x" / "config.yaml")
        assert collect_all_source_files() == []


# -------------------------------------------------------------------
# Search integration
# -------------------------------------------------------------------


class TestSearchWithSources:
    """Verify that search() includes results from additional sources."""

    def test_search_finds_source_content(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        from agent_memory.search import search

        memory = tmp_path / "memory"
        memory.mkdir()
        agent_dir = memory / "test-agent" / "atlas"
        agent_dir.mkdir(parents=True)
        (agent_dir / "entry.md").write_text(textwrap.dedent("""\
            ---
            description: Memory entry about deployment
            author: test-agent
            created: 2026-01-01
            updated: 2026-01-01
            ---
            ## Deployment
            How to deploy the system.
        """))

        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "deploy-rule.md").write_text(
            "## Deployment Gate\nVerify staging before production."
        )

        cf = _write_config(tmp_path, f"""\
            version: 1
            repos:
              additional:
                - path: {rules}
                  label: rules
        """)
        _patch_cfg(monkeypatch, cf)

        results = search(
            query="deployment",
            base_path=memory,
            include_sources=True,
            no_cache=True,
        )
        assert len(results) >= 2
        sources = {r.source for r in results}
        assert "memory" in sources
        assert "rules" in sources

    def test_search_without_sources_flag(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        from agent_memory.search import search

        memory = tmp_path / "memory"
        memory.mkdir()
        agent_dir = memory / "test-agent" / "atlas"
        agent_dir.mkdir(parents=True)
        (agent_dir / "entry.md").write_text(textwrap.dedent("""\
            ---
            description: Memory entry
            author: test-agent
            created: 2026-01-01
            updated: 2026-01-01
            ---
            ## Topic
            Content about the topic.
        """))

        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "rule.md").write_text("## Topic Rule\nRule content.")

        cf = _write_config(tmp_path, f"""\
            version: 1
            repos:
              additional:
                - path: {rules}
                  label: rules
        """)
        _patch_cfg(monkeypatch, cf)

        results = search(
            query="topic",
            base_path=memory,
            include_sources=False,
            no_cache=True,
        )
        sources = {r.source for r in results}
        assert "rules" not in sources

    def test_source_attribution_in_results(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        from agent_memory.search import search

        memory = tmp_path / "memory"
        memory.mkdir()

        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "security.md").write_text(
            "## Security Policy\nEnforce access controls."
        )

        cf = _write_config(tmp_path, f"""\
            version: 1
            repos:
              additional:
                - path: {rules}
                  label: rules
        """)
        _patch_cfg(monkeypatch, cf)

        results = search(
            query="security access controls",
            base_path=memory,
            include_sources=True,
            no_cache=True,
        )
        assert len(results) >= 1
        rule_results = [r for r in results if r.source == "rules"]
        assert len(rule_results) >= 1
        assert "rules/" in rule_results[0].path


# -------------------------------------------------------------------
# CLI integration: toc/section on files without frontmatter
# -------------------------------------------------------------------


class TestTocSectionNoFrontmatter:
    """Verify toc and section work on files without YAML frontmatter."""

    def test_toc_on_rule_file(self, tmp_path: Path) -> None:
        rule = tmp_path / "rule.md"
        rule.write_text("## Gate One\nFirst gate.\n\n## Gate Two\nSecond gate.")
        runner = CliRunner()
        result = runner.invoke(cli, ["toc", str(rule)])
        assert result.exit_code == 0
        assert "Gate One" in result.output
        assert "Gate Two" in result.output

    def test_section_on_rule_file(self, tmp_path: Path) -> None:
        rule = tmp_path / "rule.md"
        rule.write_text("## Gate One\nFirst gate content.\n\n## Gate Two\nSecond.")
        runner = CliRunner()
        result = runner.invoke(cli, ["section", str(rule), "Gate One"])
        assert result.exit_code == 0
        assert "First gate content" in result.output

    def test_toc_json_on_rule_file(self, tmp_path: Path) -> None:
        rule = tmp_path / "rule.md"
        rule.write_text("## Section A\nDescA.\n\n## Section B\nDescB.")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json-output", "toc", str(rule)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["sections"]) == 2
        assert data["sections"][0]["title"] == "Section A"


# -------------------------------------------------------------------
# CLI integration: search --include-sources
# -------------------------------------------------------------------


class TestSearchCLIWithSources:
    def test_include_sources_flag(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        memory = tmp_path / "memory"
        (memory / "agent" / "atlas").mkdir(parents=True)
        (memory / "agent" / "atlas" / "entry.md").write_text(textwrap.dedent("""\
            ---
            description: Verification protocol
            author: agent
            created: 2026-01-01
            updated: 2026-01-01
            ---
            ## Verification
            Three-layer verification protocol.
        """))

        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "verify.md").write_text(
            "## Verification Gate\nAlways verify before shipping."
        )

        cf = _write_config(tmp_path, f"""\
            version: 1
            repos:
              additional:
                - path: {rules}
                  label: rules
        """)
        _patch_cfg(monkeypatch, cf)

        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json-output", "search", "verification",
            "--base", str(memory),
            "--no-cache", "--include-sources",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        sources = {r["source"] for r in data}
        assert "rules" in sources

    def test_search_without_flag_excludes_sources(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        memory = tmp_path / "memory"
        memory.mkdir()

        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "rule.md").write_text("## Unique Topic\nContent.")

        cf = _write_config(tmp_path, f"""\
            version: 1
            repos:
              additional:
                - path: {rules}
                  label: rules
        """)
        _patch_cfg(monkeypatch, cf)

        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json-output", "search", "unique topic",
            "--base", str(memory), "--no-cache",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert all(r.get("source", "memory") == "memory" for r in data)


# -------------------------------------------------------------------
# CLI integration: ls --include-sources
# -------------------------------------------------------------------


class TestLsCLIWithSources:
    def test_ls_include_sources(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        memory = tmp_path / "memory"
        (memory / "agent" / "atlas").mkdir(parents=True)
        (memory / "agent" / "atlas" / "entry.md").write_text(textwrap.dedent("""\
            ---
            description: An entry
            author: agent
            created: 2026-01-01
            updated: 2026-01-01
            ---
            ## Section
            Content.
        """))

        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "gate.md").write_text("## Gate\nGate content.")

        cf = _write_config(tmp_path, f"""\
            version: 1
            repos:
              additional:
                - path: {rules}
                  label: rules
        """)
        _patch_cfg(monkeypatch, cf)

        runner = CliRunner()
        result = runner.invoke(cli, [
            "ls", str(memory / "agent" / "atlas"),
            "--include-sources",
        ])
        assert result.exit_code == 0
        assert "entry.md" in result.output
        assert "[rules]" in result.output
        assert "gate.md" in result.output

    def test_ls_json_with_sources(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        memory = tmp_path / "memory"
        (memory / "agent" / "atlas").mkdir(parents=True)
        (memory / "agent" / "atlas" / "entry.md").write_text(textwrap.dedent("""\
            ---
            description: An entry
            author: agent
            created: 2026-01-01
            updated: 2026-01-01
            ---
            ## Section
            Content.
        """))

        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "gate.md").write_text("## Gate\nGate content.")

        cf = _write_config(tmp_path, f"""\
            version: 1
            repos:
              additional:
                - path: {rules}
                  label: rules
        """)
        _patch_cfg(monkeypatch, cf)

        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json-output", "ls",
            str(memory / "agent" / "atlas"),
            "--include-sources",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "memory" in data
        assert "sources" in data
        assert len(data["sources"]) >= 1
        assert data["sources"][0]["label"] == "rules"
