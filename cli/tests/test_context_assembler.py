"""Tests for hooks/context_assembler.py -- Haiku-powered context injection.

Tests cover:
1. Prompt extraction from transcript JSONL
2. Haiku response parsing (mocked API)
3. Fallback keyword extraction
4. Skill directory scanning
5. Rule directory scanning
6. Memory search (mocked subprocess)
7. Graceful degradation on API failure
8. Full assembly pipeline (integration with mocked externals)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Insert hooks/ onto sys.path so context_assembler can be imported
HOOKS_DIR = str(Path(__file__).resolve().parent.parent / "hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import context_assembler  # noqa: E402, I001


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def transcript_file(tmp_path: Path) -> Path:
    """Create a parent transcript with an Agent tool call."""
    lines = [
        json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "Fix the users table migration."},
        }),
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Agent", "input": {
                        "prompt": "Fix the database migration for the users table.",
                        "subagent_type": "memory_agent",
                        "description": "Fix DB migration",
                    }},
                ],
            },
        }),
    ]
    f = tmp_path / "parent-session.jsonl"
    f.write_text("\n".join(lines) + "\n")
    return f


@pytest.fixture()
def transcript_content_blocks(tmp_path: Path) -> Path:
    """Parent transcript with multiple Agent calls — last one wins."""
    lines = [
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Agent", "input": {
                        "prompt": "Review the PR for the authentication module.",
                        "subagent_type": "github_agent",
                    }},
                ],
            },
        }),
    ]
    f = tmp_path / "parent-blocks.jsonl"
    f.write_text("\n".join(lines) + "\n")
    return f


@pytest.fixture()
def skills_dir(tmp_path: Path) -> Path:
    """Create a mock .claude/skills/ directory with skill files."""
    base = tmp_path / ".claude" / "skills"

    (base / "memory_lessons").mkdir(parents=True)
    (base / "memory_lessons" / "SKILL.md").write_text(
        "# Memory Lessons\n\nLessons learned about memory management."
    )

    (base / "github_patterns").mkdir(parents=True)
    (base / "github_patterns" / "SKILL.md").write_text(
        "# GitHub Patterns\n\nPatterns for GitHub API and PR workflows."
    )

    (base / "domain_knowledge").mkdir(parents=True)
    (base / "domain_knowledge" / "SKILL.md").write_text(
        "# Domain Knowledge\n\nDatabase schemas and feed parsing."
    )

    (base / "shared_do-not-monkey-patch").mkdir(parents=True)
    (base / "shared_do-not-monkey-patch" / "SKILL.md").write_text(
        "# Fail Loudly\n\nFix root causes, not symptoms."
    )

    return tmp_path


@pytest.fixture()
def rules_dir(tmp_path: Path) -> Path:
    """Create a mock .claude/rules/ directory with rule files."""
    base = tmp_path / ".claude" / "rules"
    base.mkdir(parents=True)

    (base / "memory-cli.md").write_text(
        "## Memory CLI Usage\n\nBEFORE any memory CLI call, read the skill."
    )
    (base / "no-gundecking.md").write_text(
        "## No Gundecking\n\nEvery sign-off represents actual work."
    )
    (base / "git-branch-safety.md").write_text(
        "## Git Branch Safety\n\nBEFORE every git commit verify branch."
    )
    (base / "delegation.md").write_text(
        "## Delegation\n\nOrchestrator delegates all work to specialists."
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Test: Prompt extraction
# ---------------------------------------------------------------------------

class TestExtractPrompt:
    """Tests for extract_prompt()."""

    def test_basic_extraction(self, transcript_file: Path) -> None:
        result = context_assembler.extract_prompt(str(transcript_file))
        assert result == "Fix the database migration for the users table."

    def test_content_blocks(self, transcript_content_blocks: Path) -> None:
        result = context_assembler.extract_prompt(str(transcript_content_blocks))
        assert "Review the PR" in result
        assert "authentication module" in result

    def test_missing_file(self) -> None:
        result = context_assembler.extract_prompt("/nonexistent/path.jsonl")
        assert result is None

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        result = context_assembler.extract_prompt(str(f))
        assert result is None

    def test_malformed_json(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.jsonl"
        f.write_text("not valid json\n")
        result = context_assembler.extract_prompt(str(f))
        assert result is None

    def test_no_agent_call(self, tmp_path: Path) -> None:
        line = json.dumps({"type": "user", "message": {"content": "hi"}})
        f = tmp_path / "no_agent.jsonl"
        f.write_text(line + "\n")
        result = context_assembler.extract_prompt(str(f))
        assert result is None

    def test_truncation(self, tmp_path: Path) -> None:
        long_prompt = "x" * 10000
        line = json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Agent", "input": {
                        "prompt": long_prompt,
                    }},
                ],
            },
        })
        f = tmp_path / "long.jsonl"
        f.write_text(line + "\n")
        result = context_assembler.extract_prompt(str(f))
        assert result is not None
        assert len(result) == context_assembler.MAX_PROMPT_CHARS

    def test_empty_prompt(self, tmp_path: Path) -> None:
        line = json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Agent", "input": {
                        "prompt": "",
                    }},
                ],
            },
        })
        f = tmp_path / "empty.jsonl"
        f.write_text(line + "\n")
        result = context_assembler.extract_prompt(str(f))
        assert result is None

    def test_last_agent_call_wins(self, tmp_path: Path) -> None:
        lines = [
            json.dumps({
                "type": "assistant",
                "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "name": "Agent", "input": {"prompt": "first task"}},
                ]},
            }),
            json.dumps({
                "type": "assistant",
                "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "name": "Agent", "input": {"prompt": "second task"}},
                ]},
            }),
        ]
        f = tmp_path / "multi.jsonl"
        f.write_text("\n".join(lines) + "\n")
        result = context_assembler.extract_prompt(str(f))
        assert result == "second task"


# ---------------------------------------------------------------------------
# Test: Haiku call (mocked)
# ---------------------------------------------------------------------------

class TestCallClaudeAssembler:
    """Tests for the Claude CLI synthesis path with subprocesses mocked."""

    def test_successful_call(self) -> None:
        completed = MagicMock(returncode=0, stdout="focused briefing\n", stderr="")
        with (
            patch.object(context_assembler, "CLAUDE_CLI", Path("/bin/echo")),
            patch.object(context_assembler.subprocess, "run", return_value=completed),
        ):
            result = context_assembler.call_claude_assembler(
                "Fix the DB", "memory_agent", "/tmp"
            )

        assert result == ("focused briefing", "haiku")

    def test_falls_back_to_sonnet(self) -> None:
        failed = MagicMock(returncode=1, stdout="", stderr="failed")
        completed = MagicMock(returncode=0, stdout="fallback briefing", stderr="")
        with (
            patch.object(context_assembler, "CLAUDE_CLI", Path("/bin/echo")),
            patch.object(
                context_assembler.subprocess,
                "run",
                side_effect=[failed, completed],
            ),
        ):
            result = context_assembler.call_claude_assembler(
                "Fix the DB", "memory_agent", "/tmp"
            )

        assert result == ("fallback briefing", "sonnet")

    def test_missing_cli_degrades_cleanly(self) -> None:
        with patch.object(
            context_assembler, "CLAUDE_CLI", Path("/nonexistent/claude")
        ):
            result = context_assembler.call_claude_assembler(
                "test", "test_agent", "/tmp"
            )

        assert result == (None, None)


# ---------------------------------------------------------------------------
# Test: Fallback keywords
# ---------------------------------------------------------------------------

class TestFallbackKeywords:
    """Tests for fallback_keywords() heuristic extraction."""

    def test_basic_extraction(self) -> None:
        result = context_assembler.fallback_keywords(
            "Fix the database migration for users table schema"
        )
        assert "search_queries" in result
        assert len(result["search_queries"]) > 0
        assert result["task_domain"] == "general"

    def test_empty_prompt(self) -> None:
        result = context_assembler.fallback_keywords("")
        assert result["search_queries"] == ["context"]

    def test_stopwords_filtered(self) -> None:
        result = context_assembler.fallback_keywords(
            "the and for that this with from"
        )
        # All stopwords, should get default
        assert result["search_queries"] == ["context"]

    def test_frequency_ranking(self) -> None:
        result = context_assembler.fallback_keywords(
            "memory memory memory search database"
        )
        assert result["search_queries"][0] == "memory"


# ---------------------------------------------------------------------------
# Test: Skill scanning
# ---------------------------------------------------------------------------

class TestScanSkills:
    """Tests for scan_skills() directory scanning."""

    def test_matches_by_name(self, skills_dir: Path) -> None:
        results = context_assembler.scan_skills(str(skills_dir), ["memory"])
        assert len(results) > 0
        assert results[0]["name"] == "memory_lessons"

    def test_matches_by_content(self, skills_dir: Path) -> None:
        results = context_assembler.scan_skills(str(skills_dir), ["database"])
        assert any(r["name"] == "domain_knowledge" for r in results)

    def test_no_matches(self, skills_dir: Path) -> None:
        results = context_assembler.scan_skills(str(skills_dir), ["zzz_nonexistent"])
        assert len(results) == 0

    def test_missing_directory(self) -> None:
        results = context_assembler.scan_skills("/nonexistent", ["memory"])
        assert results == []

    def test_score_ordering(self, skills_dir: Path) -> None:
        results = context_assembler.scan_skills(str(skills_dir), ["github"])
        if len(results) > 1:
            assert results[0]["score"] >= results[1]["score"]


# ---------------------------------------------------------------------------
# Test: Rule scanning
# ---------------------------------------------------------------------------

class TestScanRules:
    """Tests for scan_rules() directory scanning."""

    def test_matches_by_name(self, rules_dir: Path) -> None:
        results = context_assembler.scan_rules(str(rules_dir), ["memory"])
        assert len(results) > 0
        assert results[0]["name"] == "memory-cli.md"

    def test_matches_by_content(self, rules_dir: Path) -> None:
        results = context_assembler.scan_rules(str(rules_dir), ["git", "commit"])
        assert any(r["name"] == "git-branch-safety.md" for r in results)

    def test_no_matches(self, rules_dir: Path) -> None:
        results = context_assembler.scan_rules(str(rules_dir), ["zzz_nonexistent"])
        assert len(results) == 0

    def test_missing_directory(self) -> None:
        results = context_assembler.scan_rules("/nonexistent", ["memory"])
        assert results == []


# ---------------------------------------------------------------------------
# Test: Memory search (mocked subprocess)
# ---------------------------------------------------------------------------

class TestSearchMemory:
    """Tests for search_memory() with mocked subprocess."""

    def test_successful_search(self) -> None:
        mock_output = json.dumps([
            {
                "path": "nexus-marbell/atlas/swarm.md",
                "section": "Delivery",
                "section_description": "Delivery guarantees",
                "snippet": "At-least-once delivery...",
                "score": 2.5,
            },
        ])
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=mock_output, stderr="",
            )
            results = context_assembler.search_memory(["swarm"], "/tmp/memory")

        assert len(results) == 1
        assert results[0]["section"] == "Delivery"

    def test_deduplication(self) -> None:
        item = {
            "path": "nexus/atlas/test.md",
            "section": "Sec",
            "section_description": "desc",
            "snippet": "snip",
            "score": 1.0,
        }
        mock_output = json.dumps([item])
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=mock_output, stderr="",
            )
            # Same query twice should deduplicate
            results = context_assembler.search_memory(
                ["test", "test"], "/tmp/memory",
            )

        assert len(results) == 1

    def test_subprocess_failure(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="error",
            )
            results = context_assembler.search_memory(["test"], "/tmp/memory")

        assert results == []

    def test_timeout(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="memory", timeout=4)
            results = context_assembler.search_memory(["test"], "/tmp/memory")

        assert results == []

    def test_empty_queries(self) -> None:
        results = context_assembler.search_memory([], "/tmp/memory")
        assert results == []


# ---------------------------------------------------------------------------
# Test: Context assembly
# ---------------------------------------------------------------------------

class TestAssembleContext:
    """Tests for assemble_context() output formatting."""

    def test_full_context(self) -> None:
        result = context_assembler.assemble_context(
            task_domain="database ops",
            memory_results=[{
                "path": "nexus/atlas/db.md",
                "section": "Migrations",
                "section_description": "How to run migrations",
                "snippet": "Use alembic upgrade head",
            }],
            skills=[{"name": "db_patterns", "description": "DB patterns", "score": 1}],
            rules=[{"name": "git-safety.md", "description": "Git safety", "score": 1}],
        )
        assert "Context Assembler" in result
        assert "database ops" in result
        assert "Relevant Memory" in result
        assert "Migrations" in result
        assert "Recommended Skills" in result
        assert "db_patterns" in result
        assert "Applicable Rules" in result
        assert "git-safety.md" in result

    def test_empty_results(self) -> None:
        result = context_assembler.assemble_context(
            task_domain="general",
            memory_results=[],
            skills=[],
            rules=[],
        )
        assert "Context Assembler" in result
        assert "Relevant Memory" not in result
        assert "Recommended Skills" not in result
        assert "Applicable Rules" not in result

    def test_partial_results(self) -> None:
        result = context_assembler.assemble_context(
            task_domain="testing",
            memory_results=[],
            skills=[{"name": "test_skill", "description": "Testing", "score": 1}],
            rules=[],
        )
        assert "Recommended Skills" in result
        assert "Relevant Memory" not in result


# ---------------------------------------------------------------------------
# Test: Graceful degradation
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    """Tests that the system never crashes and returns partial results."""

    def test_no_transcript(self) -> None:
        result = context_assembler.run(
            transcript_path="/nonexistent/transcript.jsonl",
            agent_type="test_agent",
            agent_id="a123",
            cwd="/tmp",
        )
        assert result["context"] == ""
        assert result["model"] is None

    def test_haiku_fails_falls_back(self, transcript_file: Path) -> None:
        with (
            patch.object(
                context_assembler,
                "call_claude_assembler",
                return_value=(None, None),
            ),
            patch.object(
                context_assembler, "search_memory", return_value=[],
            ),
        ):
            result = context_assembler.run(
                transcript_path=str(transcript_file),
                agent_type="memory_agent",
                agent_id="a123",
                cwd="/tmp",
            )

        assert result["model"] is None
        assert "fallback_keywords" in result["sources"]

    def test_haiku_succeeds(self, transcript_file: Path) -> None:
        with patch.object(
            context_assembler,
            "call_claude_assembler",
            return_value=("database briefing", "haiku"),
        ):
            result = context_assembler.run(
                transcript_path=str(transcript_file),
                agent_type="memory_agent",
                agent_id="a123",
                cwd="/tmp",
            )

        assert result["model"] == "haiku"
        assert "claude-cli" in result["sources"]


# ---------------------------------------------------------------------------
# Test: Full pipeline (integration with mocked externals)
# ---------------------------------------------------------------------------

class TestFullPipeline:
    """Integration test with all external calls mocked."""

    def test_end_to_end(
        self,
        transcript_file: Path,
        skills_dir: Path,
        rules_dir: Path,
    ) -> None:
        # Set up mock skills/rules in same tmp_path
        # skills_dir and rules_dir use different tmp_paths, so merge manually
        # Use skills_dir as the base and add rules
        rules_src = rules_dir / ".claude" / "rules"
        rules_dst = skills_dir / ".claude" / "rules"
        if not rules_dst.exists():
            import shutil
            shutil.copytree(str(rules_src), str(rules_dst))

        haiku_result = {
            "search_queries": ["database", "migration"],
            "relevant_skill_keywords": ["memory", "database"],
            "relevant_rule_keywords": ["git", "memory"],
            "task_domain": "database migration",
        }

        memory_results = [{
            "path": "nexus/atlas/db-patterns.md",
            "section": "Alembic Migrations",
            "section_description": "How to run alembic migrations",
            "snippet": "alembic upgrade head",
            "score": 3.0,
        }]

        with (
            patch.object(
                context_assembler,
                "call_claude_assembler",
                return_value=(None, None),
            ),
            patch.object(
                context_assembler,
                "fallback_keywords",
                return_value=haiku_result,
            ),
            patch.object(
                context_assembler, "search_memory", return_value=memory_results,
            ),
        ):
            result = context_assembler.run(
                transcript_path=str(transcript_file),
                agent_type="memory_agent",
                agent_id="a123",
                cwd=str(skills_dir),
            )

        assert result["model"] is None
        ctx = result["context"]
        assert "database migration" in ctx
        assert "Alembic Migrations" in ctx
        assert "memory_lessons" in ctx
        assert "memory-cli.md" in ctx or "git-branch-safety.md" in ctx
        assert "Context Assembler" in ctx
        assert "End Context Assembler" in ctx

    def test_cli_output_format(self, transcript_file: Path) -> None:
        """Verify CLI outputs valid JSON to stdout."""
        with (
            patch.object(
                context_assembler,
                "call_claude_assembler",
                return_value=(None, None),
            ),
            patch.object(context_assembler, "search_memory", return_value=[]),
        ):
            result = context_assembler.run(
                transcript_path=str(transcript_file),
                agent_type="test",
                agent_id="a123",
                cwd="/tmp",
            )

        # Verify JSON-serializable
        output = json.dumps(result)
        parsed = json.loads(output)
        assert "context" in parsed
        assert "sources" in parsed
        assert "model" in parsed
        assert parsed["model"] is None


# ---------------------------------------------------------------------------
# Test: API key loading
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    """Tests for portable memory-path selection in the synthesis prompt."""

    def test_uses_configured_memory_path(self) -> None:
        with patch.dict(
            os.environ, {"AGENT_MEMORY_PATH": "/srv/agent-memory/memory"}
        ):
            prompt = context_assembler._build_system_prompt("test", "/tmp")

        assert "/srv/agent-memory/memory" in prompt

    def test_default_path_is_portable(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            prompt = context_assembler._build_system_prompt("test", "/tmp")

        assert "memory search" in prompt
        assert "/root/" not in prompt


# ---------------------------------------------------------------------------
# Test: Timeout handling
# ---------------------------------------------------------------------------

class TestTimeoutHandling:
    """Tests for timeout budget management."""

    def test_all_model_timeouts_degrade_cleanly(self) -> None:
        with (
            patch.object(context_assembler, "CLAUDE_CLI", Path("/bin/echo")),
            patch.object(
                context_assembler.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("claude", 1),
            ),
        ):
            result = context_assembler.call_claude_assembler(
                "test", "test_agent", "/tmp"
            )

        assert result == (None, None)
