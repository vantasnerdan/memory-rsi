"""Centralized configuration and path resolution for agent-memory.

Config file location: ~/.config/agent-memory/config.yaml
Priority chain: CLI flag > env var > config file > cwd auto-detect > "memory" fallback.

Supports multi-repo configuration with a primary repo
and additional read-only paths (rules, skills, etc.).

The base_path may be expressed in either of two equivalent shapes inside
the config file:

    # Nested form (preferred -- matches the multi-repo schema):
    repos:
      primary:
        path: /path/to/memory

    # Top-level alias (also supported -- simpler for single-repo setups):
    base_path: /path/to/memory

Both shapes resolve through ``resolve_base_path``. ``repos.primary.path``
wins if both are set, since it is part of the structured multi-repo schema.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

import yaml

CONFIG_DIR = Path.home() / ".config" / "agent-memory"
CONFIG_FILE = CONFIG_DIR / "config.yaml"


class BasePathResolution(NamedTuple):
    """The resolved base path together with the source that produced it.

    ``source`` is one of:
        - "cli flag"
        - "env var AGENT_MEMORY_PATH"
        - "config file (repos.primary.path)"
        - "config file (base_path)"
        - "cwd auto-detect"
        - "default"
    """

    value: str
    source: str


def load_config() -> dict:
    """Load config from ~/.config/agent-memory/config.yaml if it exists.

    Returns an empty dict if the file does not exist or is malformed.
    Malformed YAML logs a warning to stderr but does not raise.
    """
    if not CONFIG_FILE.is_file():
        return {}
    try:
        text = CONFIG_FILE.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, yaml.YAMLError):
        return {}


def _config_primary_path(config: dict) -> str | None:
    """Extract repos.primary.path from config dict."""
    repos = config.get("repos")
    if not isinstance(repos, dict):
        return None
    primary = repos.get("primary")
    if not isinstance(primary, dict):
        return None
    path = primary.get("path")
    if isinstance(path, str) and path.strip():
        return path.strip()
    return None


def _config_top_level_base_path(config: dict) -> str | None:
    """Extract top-level ``base_path`` from config dict.

    Backward-compat alias for ``repos.primary.path``. A flat config may use::

        version: 1
        agent_id: my-agent
        base_path: /srv/agent-memory/memory

    Both shapes resolve to the same value. The structured nested form
    takes precedence when both are present.
    """
    path = config.get("base_path")
    if isinstance(path, str) and path.strip():
        return path.strip()
    return None


def _config_cache_path(config: dict) -> str | None:
    """Extract repos.primary.cache_path from config dict."""
    repos = config.get("repos")
    if not isinstance(repos, dict):
        return None
    primary = repos.get("primary")
    if not isinstance(primary, dict):
        return None
    cache = primary.get("cache_path")
    if isinstance(cache, str) and cache.strip():
        return cache.strip()
    return None


def _auto_detect_base() -> str | None:
    """Auto-detect base path from cwd if a memory/ directory exists."""
    cwd = Path.cwd()
    candidate = cwd / "memory"
    if candidate.is_dir():
        return str(candidate)
    return None


def resolve_base_path_with_source(
    cli_base: str | None = None,
) -> BasePathResolution:
    """Resolve the base path AND identify which source produced it.

    This is the single source of truth for base-path resolution. The
    plain ``resolve_base_path`` function is a thin wrapper that drops
    the source label.

    Priority chain (highest to lowest):

    1. Explicit CLI --base value
    2. AGENT_MEMORY_PATH env var
    3. Config file -- repos.primary.path (nested form, preferred)
    4. Config file -- base_path (top-level alias, backward-compat)
    5. Auto-detect: cwd contains a memory/ directory
    6. "memory" fallback
    """
    if cli_base is not None:
        return BasePathResolution(cli_base, "cli flag")

    env_path = os.environ.get("AGENT_MEMORY_PATH", "").strip()
    if env_path:
        return BasePathResolution(env_path, "env var AGENT_MEMORY_PATH")

    config = load_config()
    config_path = _config_primary_path(config)
    if config_path:
        return BasePathResolution(
            config_path, "config file (repos.primary.path)",
        )

    top_level = _config_top_level_base_path(config)
    if top_level:
        return BasePathResolution(top_level, "config file (base_path)")

    auto = _auto_detect_base()
    if auto:
        return BasePathResolution(auto, "cwd auto-detect")

    return BasePathResolution("memory", "default")


def resolve_base_path(cli_base: str | None = None) -> str:
    """Resolve base path using the priority chain.

    Returns only the resolved value. Use ``resolve_base_path_with_source``
    if you also need the source label (for diagnostics, ``config show``,
    or downstream tools that gate on configuration source).

    1. Explicit CLI --base value (if provided and not the Click default)
    2. AGENT_MEMORY_PATH env var
    3. Config file repos.primary.path (nested form, preferred)
    4. Config file base_path (top-level alias, backward-compat)
    5. Auto-detect: cwd contains a memory/ directory
    6. "memory" fallback
    """
    return resolve_base_path_with_source(cli_base).value


def resolve_file_path(file_path: str, base: str | None = None) -> str:
    """Resolve a file path.

    Absolute paths are used as-is. Relative paths get the resolved base prepended.
    """
    p = Path(file_path)
    if p.is_absolute():
        return str(p)
    resolved_base = resolve_base_path(base)
    return str(Path(resolved_base) / p)


def get_agent_id() -> str | None:
    """Get agent_id from: AGENT_ID env var > config file > None."""
    env_id = os.environ.get("AGENT_ID", "").strip()
    if env_id:
        return env_id
    config = load_config()
    agent_id = config.get("agent_id")
    if isinstance(agent_id, str) and agent_id.strip():
        return agent_id.strip()
    return None


def get_cache_path() -> str | None:
    """Get cache path from: AGENT_MEMORY_CACHE_PATH env var > config file > None."""
    env_cache = os.environ.get("AGENT_MEMORY_CACHE_PATH", "").strip()
    if env_cache:
        return env_cache
    config = load_config()
    return _config_cache_path(config)


def get_additional_repos(config: dict | None = None) -> list[dict]:
    """Get additional repo paths from config file.

    Returns a list of dicts with keys: path, label, read_only.
    """
    if config is None:
        config = load_config()
    repos = config.get("repos")
    if not isinstance(repos, dict):
        return []
    additional = repos.get("additional")
    if not isinstance(additional, list):
        return []
    result = []
    for entry in additional:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        result.append({
            "path": path.strip(),
            "label": entry.get("label", ""),
            "read_only": bool(entry.get("read_only", False)),
        })
    return result
