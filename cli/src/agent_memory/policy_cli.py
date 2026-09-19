"""Thin JSON Click entry point for persistent editable policies.

Register ``policy_cmd`` with the main Click group. The instruction-file allowlist
is CLI/operator configuration, never a field supplied in an agent's JSON request.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import click

from agent_memory.config import resolve_base_path
from agent_memory.policy import PolicyError, PolicyStore

MAX_REQUEST_CHARS = 1024 * 1024


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise PolicyError(f"Invalid JSON constant: {value}")


@click.command(name="policy")
@click.option("--request", required=True, help="JSON request, or '-' to read JSON from stdin.")
@click.option("--base", type=click.Path(path_type=Path), help="Canonical memory root.")
@click.option("--instruction-file", multiple=True, type=click.Path(path_type=Path), help="Operator-configured managed instruction target allowlist; repeat as needed.")
@click.option("--no-git", is_flag=True, help="Persist files without Git operations.")
@click.option("--allow-non-main-branch", is_flag=True, help="Allow an intentional write on a non-default branch.")
def policy_cmd(request: str, base: Path | None, instruction_file: tuple[Path, ...], no_git: bool, allow_non_main_branch: bool) -> None:
    """Read, update, inspect history, roll back, or sync editable policy (JSON)."""
    try:
        raw = sys.stdin.read(MAX_REQUEST_CHARS + 1) if request == "-" else request
        if len(raw) > MAX_REQUEST_CHARS:
            raise PolicyError("Policy request exceeds 1 MiB character limit")
        value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
        store = PolicyStore(resolve_base_path(str(base) if base is not None else None), tuple(str(path) for path in instruction_file))
        result = store.execute(value, no_git=no_git, allow_non_main_branch=allow_non_main_branch)
    except (PolicyError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        click.echo(json.dumps({"ok": False, "error": str(exc), "code": getattr(exc, "code", "policy_error")}, ensure_ascii=False))
        raise click.exceptions.Exit(1) from exc
    click.echo(json.dumps(result, ensure_ascii=False))
