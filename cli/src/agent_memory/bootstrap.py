"""Portable, local-only first-run initialization and structured readiness checks.

No global configuration, remote access, pushes, existing Git identity changes, or
implicit instruction synchronization. Existing files always win over defaults.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from agent_memory.bootstrap_fs import BootstrapError, create_bytes, locked, read_bytes, safe_path
from agent_memory.contracts_store import ContractError, ContractStore, identifier, shape
from agent_memory.migration import apply_migration, preview_migration
from agent_memory.plan_templates import BUILTINS, render_template, review_template
from agent_memory.policy import DEFAULT_POLICY, MAX_BODY_CHARS, POLICY_RELATIVE_PATH, PolicyStore

CATEGORIES = ("atlas", "efforts", "calendar", "moc")


def _git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-C", str(path), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
                            capture_output=True, text=True, timeout=30)
    if check and result.returncode:
        raise BootstrapError(result.stderr.strip() or "Git operation failed", "git_error")
    return result


class BootstrapStore:
    def __init__(self, base: str | Path, agent_id: str = "agent", instruction_files: tuple[str, ...] = ()):
        self.base = safe_path(base, kind="directory")
        self.agent_id = identifier(agent_id, "agent_id")
        if self.agent_id in {"shared", "imports", "templates", "plans", "policies"}:
            raise BootstrapError("agent_id is reserved for shared storage")
        self.instruction_files = tuple(str(safe_path(p, kind="file")) for p in instruction_files)

    def _directories(self) -> list[Path]:
        relative = ["shared", "shared/plans", "shared/templates", "shared/policies", "shared/imports", self.agent_id]
        relative += [f"{owner}/{category}" for owner in ("shared", self.agent_id) for category in CATEGORIES]
        return [safe_path(self.base / name, kind="directory") for name in relative]

    def _repository(self) -> Path | None:
        if not self.base.exists() or not shutil.which("git"):
            return None
        for parent in (self.base, *self.base.parents):
            safe_path(parent / ".git")
            if (parent / ".git").exists():
                break
        result = _git(self.base, "rev-parse", "--show-toplevel", check=False)
        return safe_path(result.stdout.strip(), kind="directory") if result.returncode == 0 else None

    def status(self, *, no_git: bool = False) -> dict:
        """Read-only doctor: no locks, mkdir, default persistence, or Git writes."""
        steps = []
        missing = [str(p.relative_to(self.base)) for p in self._directories() if not p.is_dir()]
        steps.append({"id": "directories", "state": "incomplete" if missing else "ready", "missing": missing,
                      "remedy": "Run initialize with this --base and --agent-id." if missing else None})
        store = ContractStore(self.base, no_git=True)
        absent = []
        invalid = []
        for name in BUILTINS:
            path = safe_path(store.path("templates", name), kind="file")
            if not path.exists():
                absent.append(name)
            else:
                try:
                    review_template(store, name)
                except (ContractError, ValueError, OSError) as exc:
                    invalid.append({"template": name, "error": str(exc)})
        steps.append({"id": "templates", "state": "incomplete" if absent or invalid else "ready", "missing": absent,
                      "errors": invalid, "remedy": "Initialize missing templates; review/fix invalid custom templates manually (never overwritten)." if absent or invalid else None})
        policy = safe_path(self.base / POLICY_RELATIVE_PATH, kind="file")
        valid_policy = False
        policy_error = None
        if policy.exists():
            try:
                # Match the DSH prompt reader: UTF-8, at most 32768 code points
                # and 131072 bytes. Nonempty but unusable policies are not ready.
                body = read_bytes(policy, maximum=MAX_BODY_CHARS * 4).decode("utf-8")
                if not body.strip():
                    raise BootstrapError("Policy is empty; repair the existing file explicitly.")
                if len(body) > MAX_BODY_CHARS:
                    raise BootstrapError(f"Policy exceeds {MAX_BODY_CHARS} characters; repair the existing file explicitly.")
                valid_policy = True
            except (ValueError, OSError) as exc:
                policy_error = str(exc)
        steps.append({"id": "policy", "state": "ready" if valid_policy else "incomplete", "exists": policy.exists(),
                      "error": policy_error, "remedy": None if valid_policy else "Initialize missing policy, or repair existing policy explicitly; initialization preserves existing bytes."})
        repository = self._repository() if not no_git else None
        identity_configured = bool(repository) and all(
            _git(repository, "config", "--get", key, check=False).stdout.strip()
            for key in ("user.name", "user.email")
        ) if repository is not None else False
        git_remedy = None
        if not no_git and not repository:
            git_remedy = "Install Git and run initialize, or select --no-git for file-only setup."
        elif not no_git and not identity_configured:
            git_remedy = "Configure user.name and user.email with git config --local in the existing repository; bootstrap never changes an existing repository identity."
        steps.append({"id": "git", "state": "skipped" if no_git else "ready" if repository and identity_configured else "incomplete",
                      "repository": str(repository) if repository else None, "identity_configured": identity_configured,
                      "sync": "not-attempted", "remedy": git_remedy})
        steps.append({"id": "instructions", "state": "optional", "allowlist": list(self.instruction_files),
                      "remedy": "To sync policy, explicitly allowlist each --instruction-file, preview sync_instructions, review, then apply with both revisions."})
        ready = all(step["state"] != "incomplete" for step in steps)
        return {"ok": True, "action": "status", "base": str(self.base), "agent_id": self.agent_id,
                "ready": ready, "state": "ready" if ready else "incomplete", "steps": steps,
                "warnings": ["Git persistence is disabled; file-only setup." ] if no_git else ["Bootstrap never configures remotes or pushes; Git readiness is local only."]}

    def initialize(self, *, no_git: bool = False) -> dict:
        # Preflight every destination before any writes. Reject symlinks/FIFOs,
        # including the contract lock used by the reused ContractStore API.
        directories = self._directories()
        store = ContractStore(self.base, no_git=True)
        for name in BUILTINS:
            safe_path(store.path("templates", name), kind="file")
        safe_path(self.base / POLICY_RELATIVE_PATH, kind="file")
        safe_path(self.base / ".contracts.lock", kind="file")
        if not no_git and not shutil.which("git"):
            raise BootstrapError("Git is unavailable; install Git or rerun with --no-git", "git_missing")
        created = []
        created_directories = []
        git_result = {"state": "disabled" if no_git else "preserved", "committed": False, "pushed": False}
        with locked(self.base):
            repo = self._repository() if not no_git else None
            new_repo = not no_git and repo is None
            if new_repo:
                safe_path(self.base / ".git", kind="directory")
                if (self.base / ".git").exists():
                    raise BootstrapError("Existing .git is not a usable repository; repair manually", "git_error")
                _git(self.base, "init", "--initial-branch=main")
                _git(self.base, "config", "--local", "user.name", "Memory Bootstrap")
                _git(self.base, "config", "--local", "user.email", "memory-bootstrap@localhost")
                git_result["state"] = "initialized-local"
            for path in directories:
                if not path.exists():
                    safe_path(path, kind="directory").mkdir(parents=True, exist_ok=True)
                    created_directories.append(str(path.relative_to(self.base)))
            with store.locked():
                for name, template in BUILTINS.items():
                    path = safe_path(store.path("templates", name), kind="file")
                    if not path.exists():
                        store.save("templates", name,
                                   {"kind": "template", "schema": 1, "template_id": name, "template": template},
                                   render_template(template), actor="bootstrap", expected=None)
                        created.append(str(path.relative_to(self.base)))
            if create_bytes(self.base / POLICY_RELATIVE_PATH, DEFAULT_POLICY.encode("utf-8")):
                created.append(str(POLICY_RELATIVE_PATH))
            # Existing repositories (including non-default branches and staged
            # changes) are never staged, committed, reconfigured or checked out.
            if new_repo and created:
                _git(self.base, "add", "--", *created)
                _git(self.base, "commit", "--only", "-m", "memory: initialize portable defaults", "--", *created)
                git_result.update(committed=True, commit=_git(self.base, "rev-parse", "HEAD").stdout.strip())
        return {**self.status(no_git=no_git), "action": "initialize", "created": created,
                "created_directories": created_directories, "changed": bool(created or created_directories or git_result["state"] == "initialized-local"),
                "git": git_result, "instructions_synced": False}

    def execute(self, request: dict, *, no_git: bool = False) -> dict:
        if not isinstance(request, dict):
            raise BootstrapError("request must be a JSON object")
        action = request.get("action")
        if action in ("status", "initialize"):
            shape(request, ("action",), label="bootstrap request")
            return self.status(no_git=no_git) if action == "status" else self.initialize(no_git=no_git)
        payload = {key: value for key, value in request.items() if key != "action"}
        if action == "preview_migration":
            return preview_migration(self.base, payload)
        if action == "apply_migration":
            return apply_migration(self.base, payload)
        if action == "sync_instructions":
            target = payload.get("target")
            if not isinstance(target, str):
                raise BootstrapError("sync_instructions requires an explicit target")
            safe_path(target, kind="file")
            # Validate lock/storage ancestors before PolicyStore resolves its root.
            safe_path(self.base / "shared/policies/.agent-policy.lock", kind="file")
            safe_path(self.base / POLICY_RELATIVE_PATH, kind="file")
            path = Path(target).expanduser().absolute()
            safe_path(path.with_name("." + path.name + ".memory-policy.lock"), kind="file")
            result = PolicyStore(self.base, self.instruction_files).execute({**payload, "action": "sync"}, no_git=True)
            return {**result, "action": "sync_instructions"}
        raise BootstrapError("action must be status, initialize, preview_migration, apply_migration, or sync_instructions")
