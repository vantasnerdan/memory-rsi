"""Thin bounded JSON adapter for immutable RSI artifacts and explicit promotion."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import click

from agent_memory.config import get_agent_id, resolve_base_path
from agent_memory.contracts_store import ContractError
from agent_memory.rsi import execute_request
from agent_memory.rsi_schema import MAX_REQUEST_BYTES, parse_json


@click.command(name="rsi")
@click.option("--request", required=True, help="Strict JSON request, or '-' to read bounded JSON from stdin.")
@click.option("--base", type=click.Path(path_type=Path), help="Configured memory root (no arbitrary file request inputs).")
@click.option("--no-git", is_flag=True, help="Persist locally without Git commit/push.")
@click.option("--allow-non-main-branch", is_flag=True, help="Allow an intentional write on a non-default branch.")
def rsi_cmd(request, base, no_git, allow_non_main_branch):
    """Context/sections/corpus, propose, record, read/lookup/list, or promote RSI."""
    try:
        raw = sys.stdin.read(MAX_REQUEST_BYTES + 1) if request == "-" else request
        if len(raw) > MAX_REQUEST_BYTES:
            raise ContractError("RSI request exceeds size limit")
        payload = parse_json(raw)
        result = execute_request(payload, resolve_base_path(str(base) if base is not None else None),
                                 actor=get_agent_id() or "unknown", no_git=no_git,
                                 allow_non_main_branch=allow_non_main_branch)
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, RecursionError, subprocess.SubprocessError) as exc:
        click.echo(json.dumps({"ok": False, "error": str(exc), "code": getattr(exc, "code", "rsi_error")}, ensure_ascii=False))
        raise click.exceptions.Exit(1) from exc
    click.echo(json.dumps(result, ensure_ascii=False, allow_nan=False))
