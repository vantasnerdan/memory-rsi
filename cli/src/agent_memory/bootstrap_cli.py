"""Thin JSON CLI adapter; operator allowlists never come from request JSON."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import click

from agent_memory.bootstrap import BootstrapStore
from agent_memory.bootstrap_fs import BootstrapError
from agent_memory.config import resolve_base_path
from agent_memory.policy_cli import MAX_REQUEST_CHARS, _constant, _object


@click.command(name="bootstrap")
@click.option("--request", required=True, help="JSON request or '-' for stdin.")
@click.option("--base", type=click.Path(path_type=Path), help="Memory root; uses normal memory configuration if omitted.")
@click.option("--agent-id", default="agent", show_default=True, help="Portable local agent namespace (safe lowercase ID).")
@click.option("--instruction-file", multiple=True, type=click.Path(path_type=Path), help="Explicit operator allowlist for managed instruction sync; repeat as needed.")
@click.option("--no-git", is_flag=True, help="File-only setup without Git initialization or commit.")
def bootstrap_cmd(request: str, base: Path | None, agent_id: str, instruction_file: tuple[Path, ...], no_git: bool) -> None:
    """Check readiness, initialize portable defaults, or preview/apply Codex imports."""
    try:
        raw = sys.stdin.read(MAX_REQUEST_CHARS + 1) if request == "-" else request
        if len(raw) > MAX_REQUEST_CHARS:
            raise BootstrapError("Bootstrap request exceeds 1 MiB character limit")
        value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
        store = BootstrapStore(resolve_base_path(str(base) if base is not None else None), agent_id,
                               tuple(str(path) for path in instruction_file))
        result = store.execute(value, no_git=no_git)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        click.echo(json.dumps({"ok": False, "state": "error", "error": str(exc),
                               "code": getattr(exc, "code", "bootstrap_error"),
                               "remedy": "Correct the reported input or filesystem condition and rerun; existing user files are never replaced by initialization or import."}, ensure_ascii=False))
        raise click.exceptions.Exit(1) from exc
    click.echo(json.dumps(result, ensure_ascii=False))
