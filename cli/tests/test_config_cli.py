"""Tests for config CLI commands: show, init, set."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_memory.cli import cli

MP = pytest.MonkeyPatch


def _write_config(tmp_path: Path, content: str) -> Path:
    """Write a config file and return its path."""
    config_dir = tmp_path / ".config" / "agent-memory"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.yaml"
    config_file.write_text(textwrap.dedent(content), encoding="utf-8")
    return config_file


def _patch_config(mp: MP, config_path: Path) -> None:
    """Point CONFIG_FILE and CONFIG_DIR at test paths."""
    mp.setattr("agent_memory.config.CONFIG_FILE", config_path)
    mp.setattr("agent_memory.config_cli.CONFIG_FILE", config_path)
    mp.setattr("agent_memory.config_cli.CONFIG_DIR", config_path.parent)


def _no_config(mp: MP, tmp_path: Path) -> None:
    """Point config at a nonexistent file."""
    fake = tmp_path / "no-config" / "config.yaml"
    _patch_config(mp, fake)


def _no_auto(mp: MP) -> None:
    """Disable cwd auto-detect."""
    mp.setattr("agent_memory.config._auto_detect_base", lambda: None)


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
# config show
# -------------------------------------------------------------------


class TestConfigShow:
    def test_show_with_config_file(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_config(monkeypatch, cf)
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_CACHE_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_REPO", raising=False)
        _no_auto(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        assert "agent_id: test-agent" in result.output
        assert "(source: config file)" in result.output
        assert "base_path: /tmp/test-memory" in result.output
        assert "cache_path: /tmp/test-cache" in result.output
        assert "remote: git@github.com:test/memory.git" in result.output
        assert "(exists: yes)" in result.output

    def test_show_env_var_source(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_config(monkeypatch, cf)
        monkeypatch.setenv("AGENT_ID", "env-agent")
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        _no_auto(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        assert "agent_id: env-agent" in result.output
        assert "source: env var AGENT_ID" in result.output

    def test_show_no_config(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        _no_config(monkeypatch, tmp_path)
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_CACHE_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_REPO", raising=False)
        _no_auto(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        assert "(not set)" in result.output
        assert "(exists: no)" in result.output

    def test_show_json_output(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_config(monkeypatch, cf)
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_CACHE_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_REPO", raising=False)
        _no_auto(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json-output", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["agent_id"]["value"] == "test-agent"
        assert data["agent_id"]["source"] == "config file"
        assert data["base_path"]["value"] == "/tmp/test-memory"
        assert data["config_file"]["exists"] is True
        assert len(data["additional_repos"]) == 2
        assert data["additional_repos"][0]["path"] == "/tmp/rules"
        assert data["additional_repos"][0]["read_only"] is True

    def test_show_additional_repos_displayed(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_config(monkeypatch, cf)
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        _no_auto(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert "additional_repos:" in result.output
        assert "/tmp/rules" in result.output
        assert "read_only" in result.output

    def test_show_base_path_source_nested_form(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        """JSON output reports the structured config-file label.

        Regression for issue #80 -- ensures the base_path source agrees
        with the value when ``repos.primary.path`` is set.
        """
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_config(monkeypatch, cf)
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_CACHE_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_REPO", raising=False)
        _no_auto(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json-output", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["base_path"]["value"] == "/tmp/test-memory"
        assert (
            data["base_path"]["source"]
            == "config file (repos.primary.path)"
        )

    def test_show_base_path_source_top_level_alias(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        """Issue #80 (kelvin host): top-level ``base_path:`` config shape.

        Before the fix, the JSON output reported::

            "base_path": {
                "value": "/srv/agent-memory/memory",
                "source": "default"   <-- WRONG (was actually a config-file
                                          read via a different code path)
            }

        After the fix, ``source`` correctly identifies the config file.
        Without this regression test, downstream tools that gate on
        ``source != "default"`` (like context-engine#18's portable path
        resolver) silently false-reject valid kelvin configs.
        """
        cf = _write_config(
            tmp_path,
            """\
            version: 1
            repos:
              primary:
                remote: git@github.com:example/memory-data.git
            agent_id: kelvin
            base_path: /srv/agent-memory/memory
            """,
        )
        _patch_config(monkeypatch, cf)
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_CACHE_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_REPO", raising=False)
        _no_auto(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json-output", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert (
            data["base_path"]["value"]
            == "/srv/agent-memory/memory"
        )
        assert (
            data["base_path"]["source"] == "config file (base_path)"
        )
        # Sanity: source must NOT be "default" -- that was the bug.
        assert data["base_path"]["source"] != "default"

    def test_show_base_path_source_env_var(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        """Env var must label as such even when config file also has a path."""
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_config(monkeypatch, cf)
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.setenv("AGENT_MEMORY_PATH", "/from/env")
        _no_auto(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json-output", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["base_path"]["value"] == "/from/env"
        assert (
            data["base_path"]["source"]
            == "env var AGENT_MEMORY_PATH"
        )

    def test_show_base_path_source_default_when_unconfigured(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        """``default`` is reserved for the literal "memory" fallback."""
        _no_config(monkeypatch, tmp_path)
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_CACHE_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_REPO", raising=False)
        _no_auto(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json-output", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["base_path"]["value"] == "memory"
        assert data["base_path"]["source"] == "default"


# -------------------------------------------------------------------
# config init
# -------------------------------------------------------------------


class TestConfigInit:
    def test_init_creates_config_from_env(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        config_path = tmp_path / ".config" / "agent-memory" / "config.yaml"
        _patch_config(monkeypatch, config_path)
        monkeypatch.setenv("AGENT_ID", "my-agent")
        monkeypatch.setenv("AGENT_MEMORY_PATH", "/my/memory")
        monkeypatch.setenv("AGENT_MEMORY_CACHE_PATH", "/my/cache")
        monkeypatch.setenv("AGENT_MEMORY_REPO", "git@github.com:me/mem.git")

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "init"])
        assert result.exit_code == 0
        assert "Created config" in result.output
        assert config_path.is_file()

        import yaml
        data = yaml.safe_load(config_path.read_text())
        assert data["agent_id"] == "my-agent"
        assert data["repos"]["primary"]["path"] == "/my/memory"
        assert data["repos"]["primary"]["remote"] == "git@github.com:me/mem.git"
        assert data["repos"]["primary"]["cache_path"] == "/my/cache"

    def test_init_refuses_overwrite(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_config(monkeypatch, cf)

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "init"])
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_init_force_overwrites(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_config(monkeypatch, cf)
        monkeypatch.setenv("AGENT_ID", "new-agent")
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_CACHE_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_REPO", raising=False)
        # Disable git remote detection
        monkeypatch.setattr("agent_memory.config_cli._detect_git_remote", lambda: None)

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "init", "--force"])
        assert result.exit_code == 0
        assert "Created config" in result.output

        import yaml
        data = yaml.safe_load(cf.read_text())
        assert data["agent_id"] == "new-agent"
        # Old config data should be gone
        assert "repos" not in data or "additional" not in data.get("repos", {})

    def test_init_json_output(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        config_path = tmp_path / ".config" / "agent-memory" / "config.yaml"
        _patch_config(monkeypatch, config_path)
        monkeypatch.setenv("AGENT_ID", "json-agent")
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_CACHE_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_REPO", raising=False)
        monkeypatch.setattr("agent_memory.config_cli._detect_git_remote", lambda: None)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json-output", "config", "init"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["created"] is True
        assert data["config"]["agent_id"] == "json-agent"

    def test_init_auto_detects_git_remote(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        config_path = tmp_path / ".config" / "agent-memory" / "config.yaml"
        _patch_config(monkeypatch, config_path)
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_CACHE_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_REPO", raising=False)
        monkeypatch.setattr(
            "agent_memory.config_cli._detect_git_remote",
            lambda: "git@github.com:detected/repo.git",
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "init"])
        assert result.exit_code == 0

        import yaml
        data = yaml.safe_load(config_path.read_text())
        assert data["repos"]["primary"]["remote"] == "git@github.com:detected/repo.git"

    def test_init_env_repo_overrides_detected(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        config_path = tmp_path / ".config" / "agent-memory" / "config.yaml"
        _patch_config(monkeypatch, config_path)
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_CACHE_PATH", raising=False)
        monkeypatch.setenv("AGENT_MEMORY_REPO", "git@env.com:env/repo.git")
        monkeypatch.setattr(
            "agent_memory.config_cli._detect_git_remote",
            lambda: "git@detected.com:x/y.git",
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "init"])
        assert result.exit_code == 0

        import yaml
        data = yaml.safe_load(config_path.read_text())
        assert data["repos"]["primary"]["remote"] == "git@env.com:env/repo.git"

    def test_init_minimal_no_env(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        config_path = tmp_path / ".config" / "agent-memory" / "config.yaml"
        _patch_config(monkeypatch, config_path)
        monkeypatch.delenv("AGENT_ID", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_CACHE_PATH", raising=False)
        monkeypatch.delenv("AGENT_MEMORY_REPO", raising=False)
        monkeypatch.setattr("agent_memory.config_cli._detect_git_remote", lambda: None)

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "init"])
        assert result.exit_code == 0

        import yaml
        data = yaml.safe_load(config_path.read_text())
        assert data == {"version": 1}

    def test_init_refuses_overwrite_json(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_config(monkeypatch, cf)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json-output", "config", "init"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["created"] is False
        assert "already exists" in data["reason"]


# -------------------------------------------------------------------
# config set
# -------------------------------------------------------------------


class TestConfigSet:
    def test_set_top_level_key(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, "version: 1\n")
        _patch_config(monkeypatch, cf)

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "set", "agent_id", "my-agent"])
        assert result.exit_code == 0
        assert "Set agent_id = my-agent" in result.output

        import yaml
        data = yaml.safe_load(cf.read_text())
        assert data["agent_id"] == "my-agent"
        assert data["version"] == 1  # Preserved

    def test_set_nested_key(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, "version: 1\n")
        _patch_config(monkeypatch, cf)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["config", "set", "repos.primary.path", "/new/path"],
        )
        assert result.exit_code == 0

        import yaml
        data = yaml.safe_load(cf.read_text())
        assert data["repos"]["primary"]["path"] == "/new/path"

    def test_set_creates_config_if_missing(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        config_path = tmp_path / ".config" / "agent-memory" / "config.yaml"
        _patch_config(monkeypatch, config_path)

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "set", "agent_id", "new-agent"])
        assert result.exit_code == 0
        assert config_path.is_file()

        import yaml
        data = yaml.safe_load(config_path.read_text())
        assert data["agent_id"] == "new-agent"
        assert data["version"] == 1

    def test_set_json_output(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, "version: 1\n")
        _patch_config(monkeypatch, cf)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json-output", "config", "set", "agent_id", "x"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["key"] == "agent_id"
        assert data["value"] == "x"

    def test_set_boolean_coercion(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, "version: 1\n")
        _patch_config(monkeypatch, cf)

        runner = CliRunner()
        runner.invoke(cli, ["config", "set", "some_flag", "true"])

        import yaml
        data = yaml.safe_load(cf.read_text())
        assert data["some_flag"] is True

    def test_set_integer_coercion(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, "version: 1\n")
        _patch_config(monkeypatch, cf)

        runner = CliRunner()
        runner.invoke(cli, ["config", "set", "default_limit", "25"])

        import yaml
        data = yaml.safe_load(cf.read_text())
        assert data["default_limit"] == 25

    def test_set_preserves_existing_keys(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, SAMPLE_CONFIG)
        _patch_config(monkeypatch, cf)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["config", "set", "agent_id", "updated-agent"],
        )
        assert result.exit_code == 0

        import yaml
        data = yaml.safe_load(cf.read_text())
        assert data["agent_id"] == "updated-agent"
        # Other keys preserved
        assert data["repos"]["primary"]["path"] == "/tmp/test-memory"
        assert len(data["repos"]["additional"]) == 2

    def test_set_deeply_nested_creates_path(
        self, monkeypatch: MP, tmp_path: Path,
    ) -> None:
        cf = _write_config(tmp_path, "version: 1\n")
        _patch_config(monkeypatch, cf)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["config", "set", "a.b.c.d", "deep"],
        )
        assert result.exit_code == 0

        import yaml
        data = yaml.safe_load(cf.read_text())
        assert data["a"]["b"]["c"]["d"] == "deep"


# -------------------------------------------------------------------
# Helper function tests
# -------------------------------------------------------------------


class TestResolveSource:
    def test_env_var_detected(self, monkeypatch: MP) -> None:
        from agent_memory.config_cli import _resolve_source
        monkeypatch.setenv("AGENT_ID", "test")
        result = _resolve_source("AGENT_ID", {}, "agent_id")
        assert result == "env var AGENT_ID"

    def test_config_file_detected(self, monkeypatch: MP) -> None:
        from agent_memory.config_cli import _resolve_source
        monkeypatch.delenv("AGENT_ID", raising=False)
        result = _resolve_source("AGENT_ID", {"agent_id": "x"}, "agent_id")
        assert result == "config file"

    def test_default_when_unset(self, monkeypatch: MP) -> None:
        from agent_memory.config_cli import _resolve_source
        monkeypatch.delenv("AGENT_ID", raising=False)
        result = _resolve_source("AGENT_ID", {}, "agent_id")
        assert result == "default"

    def test_nested_config_key(self, monkeypatch: MP) -> None:
        from agent_memory.config_cli import _resolve_source
        monkeypatch.delenv("AGENT_MEMORY_PATH", raising=False)
        config = {"repos": {"primary": {"path": "/foo"}}}
        result = _resolve_source("AGENT_MEMORY_PATH", config, "repos.primary.path")
        assert result == "config file"

    def test_empty_env_var_ignored(self, monkeypatch: MP) -> None:
        from agent_memory.config_cli import _resolve_source
        monkeypatch.setenv("AGENT_ID", "  ")
        result = _resolve_source("AGENT_ID", {"agent_id": "x"}, "agent_id")
        assert result == "config file"


class TestSetNested:
    def test_simple_key(self) -> None:
        from agent_memory.config_cli import _set_nested
        d: dict = {}
        _set_nested(d, "key", "val")
        assert d["key"] == "val"

    def test_dotted_key(self) -> None:
        from agent_memory.config_cli import _set_nested
        d: dict = {}
        _set_nested(d, "a.b.c", "val")
        assert d["a"]["b"]["c"] == "val"

    def test_overwrites_non_dict(self) -> None:
        from agent_memory.config_cli import _set_nested
        d: dict = {"a": "string"}
        _set_nested(d, "a.b", "val")
        assert d["a"]["b"] == "val"
