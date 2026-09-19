"""Contained Markdown contract storage, cooperative locking, and scoped Git writes.

Contract revisions hash the complete file, not timestamps. No generic memory writer
is used: unknown frontmatter survives and structured contract metadata is retained.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent_memory.git_ops import git_default_branch


class ContractError(ValueError):
    """An invalid request, unsafe path, or stale contract revision."""


def shape(value, required, optional=(), label="object"):
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ContractError(f"{label} must be an object")
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing or extra:
        raise ContractError(f"{label}: missing {sorted(missing)}, unknown {sorted(extra)}")
    return value


def text(value, label="text"):
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a nonempty string")
    return value


def identifier(value, label="id"):
    text(value, label)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", value):
        raise ContractError(f"{label} must be a safe lowercase ID (max 80 characters)")
    return value


def sequence(value, label="list", nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise ContractError(f"{label} must be {'a nonempty' if nonempty else 'a'} list")
    return value


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def content_revision(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def now():
    return datetime.now(timezone.utc).isoformat()


def check_revision(expected, actual):
    if expected is not None:
        text(expected, "revision")
    if expected != actual:
        raise ContractError("stale revision: read/review the current content before saving")


class ContractStore:
    """All read/modify/write callers must hold ``locked()`` through persistence."""

    def __init__(self, base, *, no_git=False, allow_non_main_branch=False):
        self.base = Path(base).absolute()
        self.no_git = no_git
        self.allow_non_main_branch = allow_non_main_branch

    def _safe(self, path):
        # Reject symlinks, including configured base ancestors and dangling links.
        # These checks defend against stored malicious paths; cooperative writers
        # serialize via flock. Untrusted concurrent filesystem mutation is not supported.
        path = Path(path).absolute()
        if not path.is_relative_to(self.base):
            raise ContractError("path escapes memory base")
        for part in (*reversed(path.parents), path):
            if part.is_symlink():
                raise ContractError(f"symlink is not allowed: {part}")
        if path.exists() and path.is_file() and path.stat().st_nlink != 1:
            raise ContractError(f"hard-linked contract is not allowed: {path}")
        return path

    def path(self, kind, entry_id):
        if kind not in ("plans", "templates"):
            raise ContractError("unknown contract directory")
        identifier(entry_id)
        return self._safe(self.base / "shared" / kind / f"{entry_id}.md")

    @contextmanager
    def locked(self):
        self._safe(self.base).mkdir(parents=True, exist_ok=True)
        lock = self._safe(self.base / ".contracts.lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_nlink != 1:
                raise ContractError("unsafe contract lock")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def read(self, kind, entry_id):
        path = self.path(kind, entry_id)
        if not path.is_file():
            raise ContractError(f"{kind} entry not found: {entry_id}")
        raw = path.read_bytes().decode("utf-8")
        normalized = raw.replace("\r\n", "\n")
        parts = normalized.split("\n---\n", 1)
        if not normalized.startswith("---\n") or len(parts) != 2:
            raise ContractError("contract file requires YAML frontmatter")
        try:
            metadata = yaml.safe_load(parts[0][4:])
        except yaml.YAMLError as exc:
            raise ContractError(f"invalid contract YAML: {exc}") from exc
        if not isinstance(metadata, dict) or not isinstance(metadata.get("contract"), dict):
            raise ContractError("missing structured contract metadata")
        return {"path": str(path), "revision": digest(raw), "metadata": metadata,
                "contract": metadata["contract"], "markdown": raw}

    def list_ids(self, kind):
        directory = self.path(kind, "probe").parent
        if not directory.exists():
            return []
        return sorted(identifier(p.stem) for p in directory.glob("*.md"))

    def _git(self, cwd, *args, check=True):
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                                text=True, timeout=30)
        if check and result.returncode:
            raise ContractError(result.stderr.strip() or f"git {' '.join(args)} failed")
        return result

    def _preflight(self, path):
        if self.no_git:
            return None
        result = self._git(self.base, "rev-parse", "--show-toplevel", check=False)
        if result.returncode:
            return None
        repo = Path(result.stdout.strip()).resolve()
        branch = self._git(repo, "symbolic-ref", "--short", "HEAD", check=False)
        if not self.allow_non_main_branch and (
            branch.returncode or branch.stdout.strip() != git_default_branch(repo)
        ):
            raise ContractError("not on default branch; use --allow-non-main-branch explicitly")
        relative = str(path.relative_to(repo))
        staged = self._git(repo, "diff", "--cached", "--quiet", "--", relative, check=False)
        if staged.returncode:
            raise ContractError("contract file already has staged changes; preserve or commit them first")
        return repo

    def _persist(self, path, repo):
        result = {"saved": True, "commit": "skipped", "sync": "skipped"}
        if self.no_git:
            result["reason"] = "no_git"
            return result
        if repo is None:
            result["reason"] = "not a Git repository"
            return result
        relative = str(path.relative_to(repo))
        try:
            self._git(repo, "add", "--", relative)
            self._git(repo, "commit", "--only", "-m", f"memory: save {relative}", "--", relative)
            result["commit"] = "committed"
            result["commit_sha"] = self._git(repo, "rev-parse", "HEAD").stdout.strip()
        except (ContractError, OSError, subprocess.TimeoutExpired) as exc:
            result.update(commit="failed", error=str(exc))
            return result
        # Never pull/rebase the user's working tree. A rejected push is explicitly
        # a sync failure, not a failed save and not an invitation to overwrite it.
        try:
            remotes = self._git(repo, "remote").stdout.strip()
            if not remotes:
                result["sync"] = "local-only"
                return result
            upstream = self._git(repo, "rev-parse", "--abbrev-ref", "@{upstream}", check=False)
            if upstream.returncode:
                result["sync"] = "not-configured"
                return result
            # With an upstream, push only the checked-out branch (not push.default=matching).
            branch = self._git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
            remote = self._git(repo, "config", "--get", f"branch.{branch}.remote").stdout.strip()
            target = self._git(repo, "config", "--get", f"branch.{branch}.merge").stdout.strip()
            if remote == ".":
                result["sync"] = "local-only"
            else:
                self._git(repo, "push", "--no-follow-tags", "--", remote, f"HEAD:{target}")
                result["sync"] = "pushed"
        except (ContractError, OSError, subprocess.TimeoutExpired) as exc:
            result.update(sync="failed", sync_error=str(exc))
        return result

    def save(self, kind, entry_id, contract, body, *, actor, expected, existing=None):
        path = self.path(kind, entry_id)
        current = self.read(kind, entry_id) if path.exists() else None
        check_revision(expected, current["revision"] if current else None)
        if existing is not None and (current is None or existing["revision"] != current["revision"]):
            raise ContractError("stale revision during save")
        repo = self._preflight(path)
        metadata = dict(current["metadata"]) if current else {
            "description": contract.get("title", entry_id), "author": actor,
            "created": now(), "category": "efforts", "status": "active",
            "confidence": "working", "tags": ["reward-first", kind],
        }
        metadata.update(updated=now(), contract=contract)
        raw = "---\n" + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True) + "---\n\n" + body + "\n"
        self._safe(path.parent).mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".contract-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self._safe(path)
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"path": str(path), "revision": digest(raw),
                "persistence": self._persist(path, repo)}
