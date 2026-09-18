"""CLI commands for managing agent-memory configuration.

Provides `memory config show`, `memory config init`, and `memory config set`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import click
import yaml

from agent_memory.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    get_additional_repos,
    get_agent_id,
    get_cache_path,
    load_config,
    resolve_base_path_with_source,
)


def _resolve_source(env_name: str | None, config: dict, config_key: str) -> str:
    """Determine the source of a resolved value in the priority chain."""
    if env_name:
        env_val = os.environ.get(env_name, "").strip()
        if env_val:
            return f"env var {env_name}"
    parts = config_key.split(".")
    obj: object = config
    for p in parts:
        if isinstance(obj, dict):
            obj = obj.get(p)
        else:
            return "default"
    if obj is not None and (not isinstance(obj, str) or obj.strip()):
        return "config file"
    return "default"


def _detect_git_remote() -> str | None:
    """Auto-detect git remote URL from cwd if in a repo."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _set_nested(data: dict, dotted_key: str, value: str) -> None:
    """Set a value in a nested dict using a dotted key path.

    Creates intermediate dicts as needed. Coerces booleans and integers.
    """
    parts = dotted_key.split(".")
    obj = data
    for part in parts[:-1]:
        if part not in obj or not isinstance(obj[part], dict):
            obj[part] = {}
        obj = obj[part]
    final: object = value
    if value.lower() in ("true", "false"):
        final = value.lower() == "true"
    elif value.isdigit():
        final = int(value)
    obj[parts[-1]] = final


@click.group(name="config")
@click.pass_context
def config_group(ctx: click.Context) -> None:
    """Manage agent-memory configuration."""
    ctx.ensure_object(dict)


@config_group.command(name="show")
@click.pass_context
def config_show_cmd(ctx: click.Context) -> None:
    """Display resolved configuration with source annotations."""
    use_json = ctx.obj.get("json", False)
    config = load_config()

    agent_id = get_agent_id()
    base_resolution = resolve_base_path_with_source(None)
    base_path = base_resolution.value
    cache_path = get_cache_path()
    additional = get_additional_repos(config)

    remote = None
    repos = config.get("repos")
    if isinstance(repos, dict):
        primary = repos.get("primary")
        if isinstance(primary, dict):
            remote = primary.get("remote")

    sources = {
        "agent_id": _resolve_source("AGENT_ID", config, "agent_id"),
        # base_path source is computed by the same priority chain that
        # produced the value, so the two cannot disagree (issue #80).
        "base_path": base_resolution.source,
        "cache_path": _resolve_source(
            "AGENT_MEMORY_CACHE_PATH", config, "repos.primary.cache_path",
        ),
        "remote": _resolve_source(
            "AGENT_MEMORY_REPO", config, "repos.primary.remote",
        ),
    }
    config_exists = CONFIG_FILE.is_file()

    if use_json:
        click.echo(json.dumps({
            "agent_id": {"value": agent_id, "source": sources["agent_id"]},
            "base_path": {"value": base_path, "source": sources["base_path"]},
            "cache_path": {"value": cache_path, "source": sources["cache_path"]},
            "remote": {"value": remote, "source": sources["remote"]},
            "additional_repos": [
                {"path": r["path"], "label": r["label"], "read_only": r["read_only"]}
                for r in additional
            ],
            "config_file": {"path": str(CONFIG_FILE), "exists": config_exists},
        }, indent=2))
        return

    click.echo(f"agent_id: {agent_id or '(not set)'}  (source: {sources['agent_id']})")
    click.echo(f"base_path: {base_path}  (source: {sources['base_path']})")
    cache_display = cache_path or "(not set)"
    click.echo(
        f"cache_path: {cache_display}  (source: {sources['cache_path']})",
    )
    click.echo(f"remote: {remote or '(not set)'}  (source: {sources['remote']})")
    if additional:
        click.echo("additional_repos:")
        for repo in additional:
            parts = [repo["label"]] if repo["label"] else []
            if repo["read_only"]:
                parts.append("read_only")
            flags = parts
            flag_str = f"  [{', '.join(flags)}]" if flags else ""
            click.echo(f"  - {repo['path']}{flag_str}  (source: config file)")
    config_status = "yes" if config_exists else "no"
    click.echo(f"config_file: {CONFIG_FILE}  (exists: {config_status})")


@config_group.command(name="init")
@click.option("--force", is_flag=True, help="Overwrite existing config file.")
@click.pass_context
def config_init_cmd(ctx: click.Context, force: bool) -> None:
    """Generate config file from environment variables and auto-detection."""
    use_json = ctx.obj.get("json", False)

    if CONFIG_FILE.is_file() and not force:
        if use_json:
            click.echo(json.dumps({
                "created": False, "reason": "config file already exists",
                "path": str(CONFIG_FILE), "hint": "Use --force to overwrite.",
            }, indent=2))
        else:
            click.echo(f"Config file already exists: {CONFIG_FILE}", err=True)
            click.echo("Use --force to overwrite.", err=True)
        sys.exit(1)

    new_config: dict = {"version": 1}
    agent_id = os.environ.get("AGENT_ID", "").strip()
    if agent_id:
        new_config["agent_id"] = agent_id

    memory_path = os.environ.get("AGENT_MEMORY_PATH", "").strip()
    cache_path = os.environ.get("AGENT_MEMORY_CACHE_PATH", "").strip()
    repo_url = os.environ.get("AGENT_MEMORY_REPO", "").strip()
    detected_remote = _detect_git_remote()

    primary: dict = {}
    if memory_path:
        primary["path"] = memory_path
    if repo_url:
        primary["remote"] = repo_url
    elif detected_remote:
        primary["remote"] = detected_remote
    if cache_path:
        primary["cache_path"] = cache_path
    if primary:
        new_config["repos"] = {"primary": primary}

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    yaml_text = yaml.dump(new_config, default_flow_style=False, sort_keys=False)
    CONFIG_FILE.write_text(yaml_text, encoding="utf-8")

    if use_json:
        click.echo(json.dumps({
            "created": True,
            "path": str(CONFIG_FILE),
            "config": new_config,
        }, indent=2))
    else:
        click.echo(f"Created config: {CONFIG_FILE}")
        click.echo(yaml_text.rstrip())


@config_group.command(name="set")
@click.argument("key")
@click.argument("value")
@click.pass_context
def config_set_cmd(ctx: click.Context, key: str, value: str) -> None:
    """Set a single config value using dotted key notation.

    Examples:\n
      memory config set agent_id <agent-id>\n
      memory config set repos.primary.path /srv/agent-memory/memory
    """
    use_json = ctx.obj.get("json", False)
    config = load_config() or {"version": 1}

    _set_nested(config, key, value)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    yaml_text = yaml.dump(config, default_flow_style=False, sort_keys=False)
    CONFIG_FILE.write_text(yaml_text, encoding="utf-8")

    if use_json:
        click.echo(json.dumps({
            "path": str(CONFIG_FILE),
            "key": key,
            "value": value,
        }, indent=2))
    else:
        click.echo(f"Set {key} = {value}")
        click.echo(f"Config: {CONFIG_FILE}")
