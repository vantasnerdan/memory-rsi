"""Tests for SQLite index cache: build, refresh, invalidation, CLI."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from agent_memory.cache import (
    SCHEMA_VERSION,
    IndexCache,
    resolve_cache_path,
)


def _make_entry(path: Path, content: str) -> None:
    """Write a markdown memory entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


ENTRY_A = (
    "---\n"
    "description: NaN policy for data pipeline\n"
    "author: kelvin\n"
    "created: 2026-02-10T08:00:00Z\n"
    "updated: 2026-02-11T14:30:00Z\n"
    "tags: [data, nan]\n"
    "confidence: established\n"
    "category: atlas\n"
    "status: active\n"
    "---\n"
    "# NaN Policy\n\n"
    "## Rationale\n"
    "Why missing values must not be filled.\n\n"
    "NaN represents genuine absence of data.\n\n"
    "## Implementation\n"
    "How NaN propagation works.\n\n"
    "Every transform checks for NaN.\n"
)

ENTRY_B = (
    "---\n"
    "description: Shift-one policy for z-score\n"
    "author: kelvin\n"
    "created: 2026-02-10T08:00:00Z\n"
    "updated: 2026-02-11T14:30:00Z\n"
    "tags: [data, zscore]\n"
    "confidence: working\n"
    "category: atlas\n"
    "status: active\n"
    "---\n"
    "# Shift Policy\n\n"
    "## Overview\n"
    "How shift(1) prevents lookahead bias.\n\n"
    "The expanding window uses shift(1).\n"
)

ENTRY_SHARED = (
    "---\n"
    "description: Team conventions\n"
    "author: nexus\n"
    "created: 2026-02-10T08:00:00Z\n"
    "updated: 2026-02-10T08:00:00Z\n"
    "tags: [team]\n"
    "category: atlas\n"
    "status: active\n"
    "---\n"
    "# Conventions\n\n"
    "## Style Guide\n"
    "Code formatting and naming.\n\n"
    "Use black for formatting.\n"
)


def _create_memory_tree(tmp_path: Path) -> Path:
    """Create a minimal memory tree for cache testing."""
    base = tmp_path / "memory"
    _make_entry(base / "kelvin" / "atlas" / "nan-policy.md", ENTRY_A)
    _make_entry(base / "kelvin" / "atlas" / "shift-policy.md", ENTRY_B)
    _make_entry(base / "shared" / "conventions.md", ENTRY_SHARED)
    return base


class TestIndexCacheLifecycle:
    """Open, close, context manager."""

    def test_open_close(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache" / "index.db"
        cache = IndexCache(db_path)
        cache.open()
        assert db_path.exists()
        cache.close()

    def test_context_manager(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache" / "index.db"
        with IndexCache(db_path) as cache:
            assert cache.db_path == db_path
        # Connection should be closed now
        assert cache._conn is None

    def test_operations_without_open_raise(self, tmp_path: Path) -> None:
        cache = IndexCache(tmp_path / "index.db")
        with pytest.raises(RuntimeError, match="not open"):
            cache.get_global_stats()

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        db_path = tmp_path / "deep" / "nested" / "index.db"
        with IndexCache(db_path):
            pass
        assert db_path.parent.exists()


class TestFreshBuild:
    """Building the cache from scratch."""

    def test_fresh_build_indexes_all_files(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            changed = cache.refresh(base)
            stats = cache.get_global_stats()

        assert changed == 3  # 3 files indexed
        assert stats.file_count == 3
        # nan-policy: 2 sections, shift-policy: 1, conventions: 1
        assert stats.section_count == 4
        assert stats.term_count > 0
        assert stats.avgdl > 0.0
        assert stats.last_rebuilt > 0.0

    def test_documents_contain_correct_data(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)
            docs = cache.get_documents()

        assert len(docs) == 4
        paths = [d.file_path for d in docs]
        assert "kelvin/atlas/nan-policy.md" in paths
        assert "kelvin/atlas/shift-policy.md" in paths
        assert "shared/conventions.md" in paths

        # Check tokens are populated
        for doc in docs:
            assert len(doc.tokens) > 0
            assert doc.token_count > 0
            assert doc.token_count == len(doc.tokens)

    def test_frontmatter_json_preserved(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)
            docs = cache.get_documents()

        nan_docs = [d for d in docs if "nan-policy" in d.file_path]
        assert len(nan_docs) == 2
        fm = json.loads(nan_docs[0].frontmatter_json)
        assert fm["description"] == "NaN policy for data pipeline"
        assert fm["author"] == "kelvin"
        assert fm["confidence"] == "established"

    def test_empty_directory(self, tmp_path: Path) -> None:
        base = tmp_path / "empty"
        base.mkdir()
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            changed = cache.refresh(base)
            stats = cache.get_global_stats()

        assert changed == 0
        assert stats.file_count == 0
        assert stats.section_count == 0

    def test_skips_invalid_files(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        # Add a broken file
        _make_entry(
            base / "kelvin" / "atlas" / "broken.md",
            "No frontmatter here",
        )
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)
            stats = cache.get_global_stats()

        # broken.md counts as changed (attempted) but has no sections
        assert stats.file_count == 3  # Only valid files
        assert stats.section_count == 4


class TestIncrementalRefresh:
    """Updating cache with changed, new, and deleted files."""

    def test_no_change_returns_zero(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)
            changed = cache.refresh(base)

        assert changed == 0

    def test_modified_file_detected(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)

            # Modify a file (change content and touch mtime)
            entry_path = base / "kelvin" / "atlas" / "nan-policy.md"
            updated_content = ENTRY_A.replace(
                "## Implementation",
                "## Implementation v2",
            )
            time.sleep(0.01)  # Ensure mtime changes
            entry_path.write_text(updated_content, encoding="utf-8")

            changed = cache.refresh(base)

        assert changed == 1

    def test_new_file_detected(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)

            # Add a new file
            new_entry = (
                "---\n"
                "description: New entry\n"
                "author: kelvin\n"
                "created: 2026-02-12T08:00:00Z\n"
                "updated: 2026-02-12T08:00:00Z\n"
                "---\n"
                "# New\n\n"
                "## Content\n"
                "New content here.\n"
            )
            _make_entry(
                base / "kelvin" / "atlas" / "new-entry.md", new_entry
            )

            changed = cache.refresh(base)
            stats = cache.get_global_stats()

        assert changed == 1
        assert stats.file_count == 4
        assert stats.section_count == 5

    def test_deleted_file_detected(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)

            # Delete a file
            (base / "kelvin" / "atlas" / "shift-policy.md").unlink()

            changed = cache.refresh(base)
            stats = cache.get_global_stats()

        assert changed == 1  # 1 deletion
        assert stats.file_count == 2
        assert stats.section_count == 3  # 2 from nan + 1 from conventions

    def test_df_rebuilt_after_change(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)
            stats_before = cache.get_global_stats()

            # Delete all files, add one new one
            for md in base.rglob("*.md"):
                md.unlink()

            new_entry = (
                "---\n"
                "description: Solo entry\n"
                "author: test\n"
                "created: 2026-02-12T08:00:00Z\n"
                "updated: 2026-02-12T08:00:00Z\n"
                "---\n"
                "# Solo\n\n"
                "## Unique\n"
                "xyzzyunique term.\n"
            )
            _make_entry(base / "test" / "solo.md", new_entry)

            cache.refresh(base)
            stats_after = cache.get_global_stats()

        assert stats_after.file_count == 1
        assert stats_after.section_count == 1
        assert stats_after.term_count < stats_before.term_count


class TestVerifyHash:
    """SHA-256 content verification mode."""

    def test_hash_stored_when_enabled(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base, verify_hash=True)

        # Check hash is stored
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT content_hash FROM files LIMIT 1"
        ).fetchone()
        conn.close()
        assert row[0] is not None
        assert len(row[0]) == 64  # SHA-256 hex

    def test_hash_not_stored_by_default(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base, verify_hash=False)

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT content_hash FROM files LIMIT 1"
        ).fetchone()
        conn.close()
        assert row[0] is None

    def test_hash_skips_unchanged_content(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base, verify_hash=True)

            # Touch file (change mtime) without changing content
            entry_path = base / "kelvin" / "atlas" / "nan-policy.md"
            content = entry_path.read_text()
            time.sleep(0.01)
            entry_path.write_text(content)

            # With hash verification, should detect same content
            changed = cache.refresh(base, verify_hash=True)

        assert changed == 0


class TestScopeFiltering:
    """Scope-based document retrieval from cache."""

    def test_scope_prefix_filtering(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)
            all_docs = cache.get_documents()
            kelvin_docs = cache.get_documents(scope_prefix="kelvin/")
            shared_docs = cache.get_documents(scope_prefix="shared/")

        assert len(all_docs) == 4
        assert len(kelvin_docs) == 3
        assert len(shared_docs) == 1

    def test_nonexistent_scope_returns_empty(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)
            docs = cache.get_documents(scope_prefix="nonexistent/")

        assert len(docs) == 0


class TestSchemaMigration:
    """Schema version handling."""

    def test_version_stored(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        with IndexCache(db_path):
            pass

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        conn.close()
        assert row[0] == SCHEMA_VERSION

    def test_migration_rebuilds_on_version_mismatch(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "index.db"
        # Create with current version
        with IndexCache(db_path):
            pass

        # Manually change version to simulate old schema
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE schema_version SET version = 0")
        conn.commit()
        conn.close()

        # Opening should trigger migration (rebuild)
        with IndexCache(db_path):
            pass

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        conn.close()
        assert row[0] == SCHEMA_VERSION


class TestCacheClear:
    """Cache deletion."""

    def test_clear_removes_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache" / "index.db"
        with IndexCache(db_path):
            pass
        assert db_path.exists()

        cache = IndexCache(db_path)
        cache.clear()
        assert not db_path.exists()

    def test_clear_nonexistent_is_noop(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nonexistent.db"
        cache = IndexCache(db_path)
        cache.clear()  # Should not raise


class TestResolveCachePath:
    """Cache path resolution."""

    def test_default_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AGENT_MEMORY_CACHE_PATH", raising=False)
        result = resolve_cache_path(tmp_path / "memory")
        assert result == tmp_path / "memory" / ".agent-memory-cache" / "index.db"

    def test_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        custom = str(tmp_path / "custom" / "cache.db")
        monkeypatch.setenv("AGENT_MEMORY_CACHE_PATH", custom)
        result = resolve_cache_path(tmp_path / "memory")
        assert result == Path(custom)


class TestConcurrentAccess:
    """WAL mode and concurrent reader safety."""

    def test_wal_mode_enabled(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        with IndexCache(db_path):
            pass

        conn = sqlite3.connect(str(db_path))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"

    def test_two_readers(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)

        # Two concurrent readers
        conn1 = sqlite3.connect(str(db_path))
        conn2 = sqlite3.connect(str(db_path))

        rows1 = conn1.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        rows2 = conn2.execute("SELECT COUNT(*) FROM files").fetchone()[0]

        conn1.close()
        conn2.close()

        assert rows1 == rows2 == 3


class TestSearchIntegration:
    """Integration between cache and search."""

    def test_search_uses_cache(self, tmp_path: Path) -> None:
        from agent_memory.search import search

        base = _create_memory_tree(tmp_path)
        # Build cache first
        db_path = resolve_cache_path(base)
        with IndexCache(db_path) as cache:
            cache.refresh(base)

        # Search should use cache
        results = search("NaN", base_path=base, no_cache=False)
        assert len(results) > 0
        assert results[0].score > 0

    def test_search_no_cache_flag(self, tmp_path: Path) -> None:
        from agent_memory.search import search

        base = _create_memory_tree(tmp_path)
        # Do NOT build cache
        results = search("NaN", base_path=base, no_cache=True)
        assert len(results) > 0

    def test_search_falls_back_without_cache(self, tmp_path: Path) -> None:
        from agent_memory.search import search

        base = _create_memory_tree(tmp_path)
        # No cache built, no_cache=False -- should fall back to disk
        results = search("NaN", base_path=base, no_cache=False)
        assert len(results) > 0

    def test_cached_search_results_match_direct(self, tmp_path: Path) -> None:
        from agent_memory.search import search

        base = _create_memory_tree(tmp_path)

        # Direct (no cache)
        direct = search("NaN data", base_path=base, no_cache=True)

        # Build cache
        db_path = resolve_cache_path(base)
        with IndexCache(db_path) as cache:
            cache.refresh(base)

        # Cached
        cached = search("NaN data", base_path=base, no_cache=False)

        # Results should match
        assert len(direct) == len(cached)
        for d, c in zip(direct, cached):
            assert d.path == c.path
            assert d.section == c.section
            assert d.score == c.score

    def test_cached_search_with_scope(self, tmp_path: Path) -> None:
        from agent_memory.search import search

        base = _create_memory_tree(tmp_path)
        db_path = resolve_cache_path(base)
        with IndexCache(db_path) as cache:
            cache.refresh(base)

        results = search(
            "NaN", base_path=base, scope="agent:kelvin", no_cache=False
        )
        for r in results:
            assert r.path.startswith("kelvin/")

    def test_cached_search_with_filter(self, tmp_path: Path) -> None:
        from agent_memory.search import search

        base = _create_memory_tree(tmp_path)
        db_path = resolve_cache_path(base)
        with IndexCache(db_path) as cache:
            cache.refresh(base)

        results = search(
            "NaN",
            base_path=base,
            confidence="established",
            no_cache=False,
        )
        for r in results:
            assert r.file_frontmatter.get("confidence") == "established"


class TestCacheCLI:
    """CLI commands for cache management."""

    def test_cache_build(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["cache", "build", "--base", str(base)]
        )
        assert result.exit_code == 0
        assert "Files indexed:" in result.output or "file_count" in result.output
        assert "Sections:" in result.output

    def test_cache_build_json(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "cache", "build", "--base", str(base)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["file_count"] == 3
        assert data["section_count"] == 4
        assert data["files_changed"] == 3

    def test_cache_status(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        # Build first
        runner.invoke(cli, ["cache", "build", "--base", str(base)])
        # Check status
        result = runner.invoke(
            cli, ["cache", "status", "--base", str(base)]
        )
        assert result.exit_code == 0
        assert "Files indexed:" in result.output

    def test_cache_status_no_cache(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["cache", "status", "--base", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "No cache found" in result.output

    def test_cache_status_json(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        runner.invoke(cli, ["cache", "build", "--base", str(base)])
        result = runner.invoke(
            cli,
            ["--json-output", "cache", "status", "--base", str(base)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["exists"] is True
        assert data["file_count"] == 3

    def test_cache_clear(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        # Build then clear
        runner.invoke(cli, ["cache", "build", "--base", str(base)])
        result = runner.invoke(
            cli, ["cache", "clear", "--base", str(base)]
        )
        assert result.exit_code == 0
        assert "Deleted" in result.output

    def test_cache_clear_nonexistent(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["cache", "clear", "--base", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "No cache found" in result.output

    def test_cache_clear_json(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        runner.invoke(cli, ["cache", "build", "--base", str(base)])
        result = runner.invoke(
            cli,
            ["--json-output", "cache", "clear", "--base", str(base)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["deleted"] is True

    def test_cache_build_invalid_base(self) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["cache", "build", "--base", "/nonexistent/path"]
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_search_no_cache_flag(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["search", "NaN", "--base", str(base), "--no-cache"],
        )
        assert result.exit_code == 0
        assert "nan-policy.md" in result.output

    def test_search_help_shows_no_cache(self) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["search", "--help"])
        assert "--no-cache" in result.output

    def test_cache_build_verify_hash(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json-output",
                "cache",
                "build",
                "--base",
                str(base),
                "--verify-hash",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["files_changed"] == 3

    def test_cache_help(self) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["cache", "--help"])
        assert result.exit_code == 0
        assert "build" in result.output
        assert "status" in result.output
        assert "clear" in result.output


class TestDocFrequency:
    """Document frequency table correctness."""

    def test_df_values_correct(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)

        conn = sqlite3.connect(str(db_path))
        # "nan" appears in both nan-policy sections and shift-policy edge case
        nan_df = conn.execute(
            "SELECT doc_count FROM doc_freq WHERE term = 'nan'"
        ).fetchone()
        conn.close()
        assert nan_df is not None
        assert nan_df[0] >= 2  # At least in Rationale + Implementation

    def test_df_rebuilt_on_change(self, tmp_path: Path) -> None:
        base = tmp_path / "memory"
        entry = (
            "---\n"
            "description: Solo\n"
            "author: test\n"
            "created: 2026-02-12T08:00:00Z\n"
            "updated: 2026-02-12T08:00:00Z\n"
            "---\n"
            "# Solo\n\n"
            "## Content\n"
            "uniqueterm123 here.\n"
        )
        _make_entry(base / "test" / "solo.md", entry)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT doc_count FROM doc_freq WHERE term = 'uniqueterm123'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 1


class TestGlobalStats:
    """Global statistics accuracy."""

    def test_avgdl_calculation(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)
            stats = cache.get_global_stats()

        assert stats.avgdl > 0.0
        # avgdl = total_tokens / section_count
        # Each section has at least a few tokens
        assert stats.avgdl >= 3.0

    def test_db_size_nonzero(self, tmp_path: Path) -> None:
        base = _create_memory_tree(tmp_path)
        db_path = tmp_path / "cache" / "index.db"

        with IndexCache(db_path) as cache:
            cache.refresh(base)
            stats = cache.get_global_stats()

        assert stats.db_size_bytes > 0
