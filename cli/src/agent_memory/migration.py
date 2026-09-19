"""Preview/CAS import of intentionally selected Codex Markdown, never config/data.

Imported text remains untrusted reference material in shared/imports. We neither
execute it nor promote it to the canonical policy or managed instruction targets.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from agent_memory.bootstrap_fs import BootstrapError, create_bytes, locked, read_bytes, revision, safe_path
from agent_memory.contracts_store import identifier, shape

MAX_FILES = 5000
MAX_TOTAL_BYTES = 100 * 1024 * 1024
EXCLUDED = {"auth", "credential", "credentials", "secret", "secrets", "token", "tokens",
            "sessions", "session", "session_index", "history", "logs", "log", "shell_snapshots",
            "sqlite", "node_modules", "__pycache__"}


def _excluded(name: str) -> bool:
    lower = name.lower()
    return lower.startswith(".") or lower in EXCLUDED or lower.split(".")[0] in EXCLUDED or lower.endswith((".sqlite", ".db", ".json", ".jsonl"))


def _markdown(root: Path):
    safe_path(root, kind="directory")
    if not root.is_dir():
        raise BootstrapError(f"Selected memory/skills directory is missing: {root}", "source_missing")
    for directory, dirs, files in os.walk(root, followlinks=False):
        # Never follow links even when a subtree happens to contain Markdown.
        for name in dirs + files:
            safe_path(Path(directory) / name)
        dirs[:] = sorted(name for name in dirs if not _excluded(name))
        for name in sorted(files):
            if not _excluded(name) and name.lower().endswith(".md"):
                yield safe_path(Path(directory) / name, kind="file")


def _selection(request: dict, base: Path) -> dict:
    shape(request, ("memory_dirs",), ("source_home", "source_config", "import_id"), label="migration selection")
    if ("source_home" in request) == ("source_config" in request):
        raise BootstrapError("Select exactly one explicit source_home or source_config")
    raw = request.get("source_home", request.get("source_config"))
    if not isinstance(raw, str) or not raw.strip():
        raise BootstrapError("Codex source must be a nonempty path")
    source = safe_path(raw, kind="directory" if "source_home" in request else "file")
    if not source.exists():
        raise BootstrapError(f"Codex source is missing: {source}", "source_missing")
    home = source if "source_home" in request else source.parent
    memory_dirs = request["memory_dirs"]
    if not isinstance(memory_dirs, list) or any(not isinstance(p, str) or not p.strip() for p in memory_dirs):
        raise BootstrapError("memory_dirs must be an explicit list of directory paths (possibly empty)")
    roots = []
    for value in memory_dirs:
        root = safe_path(value, kind="directory")
        if any(part.lower() in EXCLUDED or part.lower() in {".git", ".ssh"} for part in root.parts):
            raise BootstrapError("Session, history, credential and log directories cannot be memory sources", "unsafe_selection")
        if root in (Path(root.anchor), Path.home().absolute(), home):
            raise BootstrapError("Select a dedicated memory directory, not a whole home or filesystem root", "unsafe_selection")
        roots.append(root)
    for root in [home, *roots]:
        if base.is_relative_to(root) or root.is_relative_to(base):
            raise BootstrapError("Migration sources and destination memory base must not overlap", "unsafe_selection")
    if len(set(roots)) != len(roots):
        raise BootstrapError("Duplicate memory directory selection")
    selection = {"source_home" if "source_home" in request else "source_config": str(source),
                 "memory_dirs": [str(p) for p in roots],
                 "import_id": identifier(request.get("import_id", "codex"), "import_id")}
    return selection


def _inventory(base: Path, selection: dict) -> list[dict]:
    source = Path(selection["source_home"] if "source_home" in selection else selection["source_config"])
    home = source if "source_home" in selection else source.parent
    candidates = []
    agents = safe_path(home / "AGENTS.md", kind="file")
    if agents.exists():
        candidates.append((agents, Path("instructions/AGENTS.md")))
    skills = safe_path(home / "skills", kind="directory")
    if skills.exists():
        candidates.extend((p, Path("skills") / p.relative_to(skills)) for p in _markdown(skills))
    for index, raw in enumerate(selection["memory_dirs"]):
        root = Path(raw)
        candidates.extend((p, Path("memory") / str(index + 1) / p.relative_to(root)) for p in _markdown(root))
    if len(candidates) > MAX_FILES:
        raise BootstrapError(f"Import exceeds {MAX_FILES} files; narrow the selection", "size_limit")
    result = []
    total = 0
    for source, relative in candidates:
        data = read_bytes(source)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BootstrapError(f"Markdown must be UTF-8: {source}", "invalid_markdown") from exc
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise BootstrapError("Import exceeds 100 MiB; narrow the selection", "size_limit")
        destination = safe_path(base / "shared/imports" / selection["import_id"] / relative, kind="file")
        current = revision(read_bytes(destination)) if destination.exists() else "missing"
        result.append({"source": str(source), "destination": str(destination.relative_to(base)),
                       "revision": revision(data), "bytes": len(data), "destination_revision": current,
                       "state": "new" if current == "missing" else "unchanged" if current == revision(data) else "conflict"})
    return sorted(result, key=lambda item: item["destination"])


def preview_migration(base: Path, request: dict) -> dict:
    base = safe_path(base, kind="directory")
    selection = _selection(request, base)
    preview = {"schema": 1, "base": str(base), "selection": selection, "files": _inventory(base, selection)}
    token = revision(json.dumps(preview, sort_keys=True, separators=(",", ":")).encode())
    conflicts = [item["destination"] for item in preview["files"] if item["state"] == "conflict"]
    return {"ok": True, "action": "preview_migration", "preview": preview, "revision": token,
            "can_apply": not conflicts, "conflicts": conflicts,
            "notice": "Only selected Markdown is imported as untrusted reference data; config, credentials and histories are excluded. Source files are never modified. Review the manifest before explicit apply_migration."}


def apply_migration(base: Path, request: dict) -> dict:
    shape(request, ("preview", "expected_revision"), label="apply migration request")
    preview = request["preview"]
    shape(preview, ("schema", "base", "selection", "files"), label="migration preview")
    if not isinstance(preview["files"], list):
        raise BootstrapError("Preview files must be a list")
    for item in preview["files"]:
        shape(item, ("source", "destination", "revision", "bytes", "destination_revision", "state"), label="preview file")
        if any(not isinstance(item[key], str) for key in ("source", "destination", "revision", "destination_revision", "state")) or type(item["bytes"]) is not int:
            raise BootstrapError("Invalid preview file fields")
        if item["state"] not in ("new", "unchanged", "conflict"):
            raise BootstrapError("Invalid preview file state")
    actual = revision(json.dumps(preview, sort_keys=True, separators=(",", ":")).encode())
    if actual != request["expected_revision"] or type(preview["schema"]) is not int or preview["schema"] != 1 or preview["base"] != str(base):
        raise BootstrapError("Preview revision/base mismatch; preview again", "stale_preview")
    with locked(base):
        current = preview_migration(base, preview["selection"])["preview"]
        before = preview["files"]
        after = current["files"]
        # Destination may only advance from absent to identical (idempotent apply).
        compare = lambda item: {k: item[k] for k in ("source", "destination", "revision", "bytes")}
        if not isinstance(before, list) or [compare(p) for p in before] != [compare(p) for p in after]:
            raise BootstrapError("Source content or selection changed after preview; preview again", "stale_preview")
        for old, new in zip(before, after):
            if old["state"] == "conflict" or new["state"] == "conflict":
                raise BootstrapError(f"Destination conflict preserved: {new['destination']}", "destination_conflict")
            if old["destination_revision"] != new["destination_revision"] and not (old["destination_revision"] == "missing" and new["state"] == "unchanged"):
                raise BootstrapError("Destination changed after preview; preview again", "stale_preview")
        # Read and hash every selected file before creating any destination.
        contents = []
        for item in after:
            data = read_bytes(Path(item["source"]))
            if revision(data) != item["revision"]:
                raise BootstrapError("Source changed during apply; preview again", "stale_preview")
            contents.append(data)
        created = []
        for item, data in zip(after, contents):
            target = safe_path(base / item["destination"], kind="file")
            if create_bytes(target, data):
                created.append(item["destination"])
            elif revision(read_bytes(target)) != item["revision"]:
                raise BootstrapError(f"Destination changed during apply: {target}; rerun preview", "destination_conflict")
        return {"ok": True, "action": "apply_migration", "applied": True, "created": created,
                "unchanged": len(after) - len(created), "namespace": f"shared/imports/{current['selection']['import_id']}",
                "policy_promoted": False, "git": "not-staged-or-committed"}
