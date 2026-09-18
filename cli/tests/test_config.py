"""Tests for agent_memory.config -- centralized path resolution."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agent_memory.config import (
    get_additional_repos,
    get_agent_id,
    get_cache_path,
    load_config,
    resolve_base_path,
    resolve_base_path_with_source,
    resolve_file_path,
)

MP = pytest.MonkeyPatch


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _write_config(tmp_path: Path, content: str) -> Path:
    """Write a config file and return its path."""
    config_dir = tmp_path / ".config" / "agent-memory"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.yaml"
    config_file.write_text(
        textwrap.dedent(content), encoding="utf-8"
    )
    return config_file


def _patch_cfg(mp: MP, config_path: Path) -> None:
    """Patch CONFIG_FILE to point at a test config."""
    mp.setattr(
        "agent_memory.config.CONFIG_FILE", config_path,
    )


def _no_auto(mp: MP) -> None:
    """Disable cwd auto-detect so it doesn't interfere."""
    mp.setattr(
        "agent_memory.config._auto_detect_base", lambda: None,
    )


def _no_cfg(mp: MP, tmp_path: Path) -> None:
    """Point config at a nonexistent file."""
    _patch_cfg(mp, tmp_path / "x" / "config.yaml")


SAMPLE_CONFIG = """\
version: 1
agent_id: test-agent
repos:
  primary:
    path: /tmp/test-memory
    remote: git@github.com:test/memory.git
    cache_path: /tmp/test-cache
  additional:
    - path: /tmp/rules
      label: rules
      read_only: true
    - path: /tmp/skills
      label: skills
      read_only: true
default_scope: all
"""


# -------------------------------------------------------------------
# load_config
# -------------------------------------------------------------------


class TestLoadConfig:
    def test_returns_empty_when_no_file(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        _no_cfg(monkeypatch, tmp_path)
        assert load_config() == {}

    def test_loads_valid_config(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_cfg(monkeypatch, cf)
        config = load_config()
        assert config["version"] == 1
        assert config["agent_id"] == "test-agent"
        path = config["repos"]["primary"]["path"]
        assert path == "/tmp/test-memory"

    def test_returns_empty_for_malformed_yaml(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, ":\n  :\n    - [invalid")
        _patch_cfg(monkeypatch, cf)
        assert load_config() == {}

    def test_returns_empty_for_non_dict_yaml(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, "- just a list\n- of items")
        _patch_cfg(monkeypatch, cf)
        assert load_config() == {}

    def test_returns_empty_for_empty_file(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, "")
        _patch_cfg(monkeypatch, cf)
        assert load_config() == {}


# -------------------------------------------------------------------
# resolve_base_path -- priority chain
# -------------------------------------------------------------------


class TestResolveBasePath:
    def test_cli_flag_wins(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("AGENT_MEMORY_PATH", "/env/path")
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_cfg(monkeypatch, cf)
        assert resolve_base_path("/cli/path") == "/cli/path"

    def test_env_var_wins_over_config(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("AGENT_MEMORY_PATH", "/env/path")
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_cfg(monkeypatch, cf)
        assert resolve_base_path(None) == "/env/path"

    def test_config_used_when_no_env(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_cfg(monkeypatch, cf)
        _no_auto(monkeypatch)
        assert resolve_base_path(None) == "/tmp/test-memory"

    def test_auto_detect_from_cwd(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        _no_cfg(monkeypatch, tmp_path)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        monkeypatch.chdir(tmp_path)
        assert resolve_base_path(None) == str(memory_dir)

    def test_fallback_to_memory(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        _no_cfg(monkeypatch, tmp_path)
        _no_auto(monkeypatch)
        assert resolve_base_path(None) == "memory"

    def test_empty_env_var_ignored(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("AGENT_MEMORY_PATH", "")
        _no_cfg(monkeypatch, tmp_path)
        _no_auto(monkeypatch)
        assert resolve_base_path(None) == "memory"

    def test_whitespace_env_var_ignored(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("AGENT_MEMORY_PATH", "   ")
        _no_cfg(monkeypatch, tmp_path)
        _no_auto(monkeypatch)
        assert resolve_base_path(None) == "memory"

    def test_top_level_base_path_alias(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        """Top-level ``base_path:`` is an alias for ``repos.primary.path``.

        Regression test for issue #80 (kelvin host).
        """
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        cf = _write_config(
            tmp_path,
            """
            version: 1
            agent_id: kelvin
            repos:
              primary:
                remote: git@github.com:example/memory-data.git
            base_path: /srv/agent-memory/memory
            """,
        )
        _patch_cfg(monkeypatch, cf)
        _no_auto(monkeypatch)
        assert (
            resolve_base_path(None)
            == "/srv/agent-memory/memory"
        )

    def test_nested_path_wins_over_top_level_alias(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        """If both forms are set, the structured nested form wins."""
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        cf = _write_config(
            tmp_path,
            """
            version: 1
            repos:
              primary:
                path: /from/nested
            base_path: /from/top-level
            """,
        )
        _patch_cfg(monkeypatch, cf)
        _no_auto(monkeypatch)
        assert resolve_base_path(None) == "/from/nested"

    def test_empty_top_level_base_path_ignored(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        """Empty/whitespace top-level base_path falls through."""
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        cf = _write_config(
            tmp_path,
            """
            version: 1
            base_path: '   '
            """,
        )
        _patch_cfg(monkeypatch, cf)
        _no_auto(monkeypatch)
        assert resolve_base_path(None) == "memory"


# -------------------------------------------------------------------
# resolve_base_path_with_source -- single source of truth for value+label
# -------------------------------------------------------------------


class TestResolveBasePathWithSource:
    """The (value, source) tuple must always agree -- they share one chain."""

    def test_cli_flag_source(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_cfg(monkeypatch, cf)
        result = resolve_base_path_with_source("/cli/path")
        assert result.value == "/cli/path"
        assert result.source == "cli flag"

    def test_env_var_source(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("AGENT_MEMORY_PATH", "/env/path")
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_cfg(monkeypatch, cf)
        result = resolve_base_path_with_source(None)
        assert result.value == "/env/path"
        assert result.source == "env var AGENT_MEMORY_PATH"

    def test_nested_config_source(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_cfg(monkeypatch, cf)
        _no_auto(monkeypatch)
        result = resolve_base_path_with_source(None)
        assert result.value == "/tmp/test-memory"
        assert result.source == "config file (repos.primary.path)"

    def test_top_level_alias_source(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        """Issue #80: kelvin's flat config shape must report ``config file``.

        Before the fix, this returned source ``"default"`` while the value
        was correctly read (from a different code path) -- a synchronization
        bug between the value reader and the source reader.
        """
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        cf = _write_config(
            tmp_path,
            """
            version: 1
            agent_id: kelvin
            repos:
              primary:
                remote: git@github.com:example/memory-data.git
            base_path: /srv/agent-memory/memory
            """,
        )
        _patch_cfg(monkeypatch, cf)
        _no_auto(monkeypatch)
        result = resolve_base_path_with_source(None)
        assert result.value == "/srv/agent-memory/memory"
        assert result.source == "config file (base_path)"

    def test_auto_detect_source(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        _no_cfg(monkeypatch, tmp_path)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        monkeypatch.chdir(tmp_path)
        result = resolve_base_path_with_source(None)
        assert result.value == str(memory_dir)
        assert result.source == "cwd auto-detect"

    def test_default_source(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        _no_cfg(monkeypatch, tmp_path)
        _no_auto(monkeypatch)
        result = resolve_base_path_with_source(None)
        assert result.value == "memory"
        assert result.source == "default"

    def test_value_and_source_never_disagree(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        """The contract: if value comes from the config file, source must
        report a config-file label -- never ``default``.

        This is the invariant that was broken in issue #80.
        """
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        for shape, expected_source in [
            (
                "repos:\n  primary:\n    path: /a\n",
                "config file (repos.primary.path)",
            ),
            ("base_path: /a\n", "config file (base_path)"),
        ]:
            cf = _write_config(tmp_path, shape)
            _patch_cfg(monkeypatch, cf)
            _no_auto(monkeypatch)
            result = resolve_base_path_with_source(None)
            assert result.value == "/a"
            assert result.source == expected_source, (
                f"value came from {expected_source!r} but source label "
                f"was {result.source!r}"
            )


# -------------------------------------------------------------------
# resolve_file_path
# -------------------------------------------------------------------


class TestResolveFilePath:
    def test_absolute_path_returned_as_is(
        self, monkeypatch: MP,
    ) -> None:
        monkeypatch.setenv("AGENT_MEMORY_PATH", "/some/base")
        result = resolve_file_path("/absolute/path/file.md")
        assert result == "/absolute/path/file.md"

    def test_relative_path_prepends_base(self) -> None:
        result = resolve_file_path(
            "agent/atlas/file.md", "/base/dir",
        )
        assert result == "/base/dir/agent/atlas/file.md"

    def test_relative_uses_resolved_base(
        self, monkeypatch: MP,
    ) -> None:
        monkeypatch.setenv("AGENT_MEMORY_PATH", "/env/memory")
        result = resolve_file_path("agent/atlas/file.md")
        assert result == "/env/memory/agent/atlas/file.md"


# -------------------------------------------------------------------
# get_agent_id
# -------------------------------------------------------------------


class TestGetAgentId:
    def test_env_var_wins(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("AGENT_ID", "env-agent")
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_cfg(monkeypatch, cf)
        assert get_agent_id() == "env-agent"

    def test_config_fallback(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("AGENT_ID", raising=False)
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_cfg(monkeypatch, cf)
        assert get_agent_id() == "test-agent"

    def test_returns_none_when_unset(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("AGENT_ID", raising=False)
        _no_cfg(monkeypatch, tmp_path)
        assert get_agent_id() is None

    def test_empty_env_var_ignored(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("AGENT_ID", "")
        _no_cfg(monkeypatch, tmp_path)
        assert get_agent_id() is None


# -------------------------------------------------------------------
# get_cache_path
# -------------------------------------------------------------------


class TestGetCachePath:
    def test_env_var_wins(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("AGENT_MEMORY_CACHE_PATH", "/env/c")
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_cfg(monkeypatch, cf)
        assert get_cache_path() == "/env/c"

    def test_config_fallback(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv(
            "AGENT_MEMORY_CACHE_PATH", raising=False,
        )
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_cfg(monkeypatch, cf)
        assert get_cache_path() == "/tmp/test-cache"

    def test_returns_none_when_unset(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv(
            "AGENT_MEMORY_CACHE_PATH", raising=False,
        )
        _no_cfg(monkeypatch, tmp_path)
        assert get_cache_path() is None


# -------------------------------------------------------------------
# get_additional_repos
# -------------------------------------------------------------------


class TestGetAdditionalRepos:
    def test_returns_repos(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_cfg(monkeypatch, cf)
        repos = get_additional_repos()
        assert len(repos) == 2
        assert repos[0]["path"] == "/tmp/rules"
        assert repos[0]["label"] == "rules"
        assert repos[0]["read_only"] is True
        assert repos[1]["path"] == "/tmp/skills"

    def test_empty_when_no_config(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        _no_cfg(monkeypatch, tmp_path)
        assert get_additional_repos() == []

    def test_accepts_config_dict(self) -> None:
        config = {
            "repos": {
                "additional": [
                    {"path": "/a", "label": "a", "read_only": True},
                    {"path": "/b"},
                ],
            },
        }
        repos = get_additional_repos(config)
        assert len(repos) == 2
        assert repos[1]["label"] == ""
        assert repos[1]["read_only"] is False

    def test_skips_invalid_entries(self) -> None:
        config = {
            "repos": {
                "additional": [
                    "not a dict",
                    {"no_path_key": True},
                    {"path": ""},
                    {"path": "/valid"},
                ],
            },
        }
        repos = get_additional_repos(config)
        assert len(repos) == 1
        assert repos[0]["path"] == "/valid"

    def test_empty_when_additional_missing(self) -> None:
        config = {"repos": {"primary": {"path": "/p"}}}
        assert get_additional_repos(config) == []


# -------------------------------------------------------------------
# Config edge cases
# -------------------------------------------------------------------


class TestConfigEdgeCases:
    def test_no_repos_section(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(
            tmp_path, "version: 1\nagent_id: x\n",
        )
        _patch_cfg(monkeypatch, cf)
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        _no_auto(monkeypatch)
        assert resolve_base_path(None) == "memory"

    def test_empty_primary_path(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(
            tmp_path,
            "repos:\n  primary:\n    path: ''\n",
        )
        _patch_cfg(monkeypatch, cf)
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        _no_auto(monkeypatch)
        assert resolve_base_path(None) == "memory"

    def test_non_dict_primary(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(
            tmp_path,
            "repos:\n  primary: /just/a/string\n",
        )
        _patch_cfg(monkeypatch, cf)
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        _no_auto(monkeypatch)
        assert resolve_base_path(None) == "memory"


# -------------------------------------------------------------------
# Backward compatibility
# -------------------------------------------------------------------


class TestBackwardCompatibility:
    """Explicit --base values work exactly as before."""

    def test_explicit_base(self) -> None:
        assert resolve_base_path("memory") == "memory"
        path = resolve_base_path("/absolute/memory")
        assert path == "/absolute/memory"

    def test_resolve_file_with_base(self) -> None:
        result = resolve_file_path(
            "nexus/atlas/file.md", "memory",
        )
        expected = str(
            Path("memory") / "nexus" / "atlas" / "file.md",
        )
        assert result == expected

    def test_absolute_ignores_base(self) -> None:
        result = resolve_file_path(
            "/full/path/file.md", "memory",
        )
        assert result == "/full/path/file.md"
