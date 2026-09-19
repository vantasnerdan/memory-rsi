"""Editable policy storage and conservative, allowlisted instruction synchronization.

The canonical file is plain UTF-8 Markdown at POLICY_RELATIVE_PATH. Its revision
is SHA-256 of the exact bytes, so direct human edits immediately change revisions.
Read does not initialize the canonical file: an absent file exports DEFAULT_POLICY.
History snapshots are immutable, content-addressed JSON; events record operations.
POSIX advisory locks serialize cooperating writers; atomic replacement prevents
partial files. Editors that ignore these locks are not transaction participants.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import difflib
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Iterator

from agent_memory.git_ops import git_default_branch
from agent_memory.policy_git import publish_git as _git_publish
from agent_memory.policy_git import run_git as _git

POLICY_RELATIVE_PATH = Path("shared/policies/agent-policy.md")
HISTORY_RELATIVE_PATH = Path("shared/policies/.agent-policy-history")
DEFAULT_POLICY = Path(__file__).with_name("default_policy.md").read_text(encoding="utf-8")
MAX_BODY_CHARS = 32768
MAX_FILE_BYTES = 4 * 1024 * 1024
BEGIN_MARKER = "<!-- memory-rsi:policy:begin -->"
END_MARKER = "<!-- memory-rsi:policy:end -->"
_REVISION = re.compile(r"sha256:[0-9a-f]{64}\Z")
REVIEW_GUIDANCE = (
    "Shared-policy weakening should receive human review. Actor labels are "
    "self-reported provenance, not verified authorization or enforced approval."
)


class PolicyError(ValueError):
    """A safe, machine-readable validation or conflict failure."""

    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code


def content_revision(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _string(value: object, name: str, *, limit: int = MAX_BODY_CHARS) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise PolicyError(f"{name} must be a nonempty string of at most {limit} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PolicyError(f"{name} must be valid UTF-8") from exc
    return value


def _revision(value: object, name: str = "expected_revision", *, missing: bool = False) -> str:
    if not isinstance(value, str) or not (_REVISION.fullmatch(value) or (missing and value == "missing")):
        raise PolicyError(f"{name} must be a sha256 content revision" + (" or 'missing'" if missing else ""))
    return value


def _identity(request: dict) -> tuple[str, str]:
    actor = request.get("actor")
    if not isinstance(actor, str) or actor not in ("human", "agent"):
        raise PolicyError("actor must be 'human' or 'agent' (self-reported, not authorization)")
    return actor, _string(request.get("reason"), "reason", limit=4096)


def _inside(root: Path, relative: Path) -> Path:
    """Resolve every storage path, including existing symlinks, under root."""
    try:
        resolved = (root / relative).resolve()
    except (OSError, RuntimeError) as exc:
        raise PolicyError(f"Cannot resolve policy storage path: {exc}", "path_error") from exc
    if not resolved.is_relative_to(root):
        raise PolicyError("Policy storage path escapes memory root", "path_error")
    # Reject internal symlinks too: replacement must not change alias semantics.
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PolicyError("Policy storage paths must not contain symlinks", "path_error")
    return resolved


def _read_text(path: Path, *, max_bytes: int = MAX_FILE_BYTES) -> str:
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise PolicyError(f"Not a regular bounded file: {path}", "file_error")
    try:
        # Do not normalize CRLF: revisions and nonmanaged preservation are exact.
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyError(f"File is not valid UTF-8: {path}", "file_error") from exc


def _atomic_write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(body.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def _lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _json_file(path: Path) -> dict:
    try:
        # JSON escaping can expand a bounded direct-edited Markdown body.
        value = json.loads(_read_text(path, max_bytes=MAX_FILE_BYTES * 8))
    except (ValueError, OSError) as exc:
        raise PolicyError(f"Corrupt policy history: {path.name}", "history_error") from exc
    if not isinstance(value, dict):
        raise PolicyError(f"Corrupt policy history: {path.name}", "history_error")
    return value


def _stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _git_prepare(base: Path, no_git: bool, allow_branch: bool, paths: list[Path]) -> Path | None:
    if no_git:
        return None
    result = _git(base, ["rev-parse", "--show-toplevel"])
    if result.returncode:
        return None
    repo = Path(result.stdout.strip()).resolve()
    branch = _git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    if branch.returncode or (not allow_branch and branch.stdout.strip() != git_default_branch(repo)):
        raise PolicyError("Refusing policy write on detached or non-default branch; use --allow-non-main-branch for an intentional non-default branch", "branch_error")
    owned = sorted({str(path.relative_to(repo)) for path in paths if path.is_relative_to(repo)})
    if owned:
        staged = _git(repo, ["diff", "--cached", "--quiet", "--", *owned])
        if staged.returncode == 1:
            raise PolicyError("Policy-owned file already has staged changes; preserve or commit them before this operation", "staged_changes")
        if staged.returncode:
            raise PolicyError(staged.stderr.strip() or "Cannot check staged policy-owned files", "git_error")
    return repo


class PolicyStore:
    """One canonical policy and its history, rooted at the configured memory base."""

    def __init__(self, base: str | Path, instruction_files: tuple[str, ...] = ()):
        self.root = Path(base).expanduser().resolve()
        self.instruction_files = tuple(Path(p).expanduser().absolute() for p in instruction_files)

    def path(self, relative: Path = POLICY_RELATIVE_PATH) -> Path:
        return _inside(self.root, relative)

    def _current(self) -> dict:
        path = self.path()
        exists = path.exists()
        body = _read_text(path) if exists else DEFAULT_POLICY
        return {"body": body, "revision": content_revision(body), "exists": exists, "path": str(path)}

    def _snapshot_path(self, revision: str) -> Path:
        _revision(revision, "revision")
        return self.path(HISTORY_RELATIVE_PATH / "snapshots" / (revision[7:] + ".json"))

    def _load_snapshot(self, revision: str) -> dict:
        path = self._snapshot_path(revision)
        if not path.exists():
            raise PolicyError("Policy revision not found in persisted history", "not_found")
        value = _json_file(path)
        if not isinstance(value.get("body"), str) or value.get("revision") != revision or content_revision(value["body"]) != revision:
            raise PolicyError("Policy history snapshot content hash mismatch", "history_error")
        if any(not isinstance(value.get(key), str) or not value[key] for key in ("observed_at", "actor", "reason")):
            raise PolicyError("Corrupt policy history snapshot metadata", "history_error")
        return value

    def _save_snapshot(self, body: str, actor: str, reason: str) -> Path:
        revision = content_revision(body)
        path = self._snapshot_path(revision)
        if path.exists():
            self._load_snapshot(revision)
        else:
            record = {"revision": revision, "body": body, "observed_at": _stamp(), "actor": actor, "reason": reason}
            _atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        return path

    def _event(self, action: str, before: str, after: str, actor: str, reason: str) -> Path:
        event = {"id": uuid.uuid4().hex, "action": action, "at": _stamp(), "previous_revision": before, "revision": after, "actor": actor, "reason": reason}
        path = self.path(HISTORY_RELATIVE_PATH / "events" / (event["id"] + ".json"))
        _atomic_write(path, json.dumps(event, ensure_ascii=False, indent=2) + "\n")
        return path

    def _check_expected(self, request: dict, current: dict) -> None:
        expected = _revision(request.get("expected_revision"))
        if expected != current["revision"]:
            raise PolicyError(f"Policy revision conflict: expected {expected}, current {current['revision']}", "revision_conflict")

    def _history(self, request: dict, current: dict) -> dict:
        if "revision" in request:
            revision = _revision(request["revision"], "revision")
            return {"snapshot": self._load_snapshot(revision), "current_revision": current["revision"]}
        directory = self.path(HISTORY_RELATIVE_PATH / "snapshots")
        snapshots = []
        if directory.exists():
            for path in sorted(directory.glob("*.json")):
                value = self._load_snapshot("sha256:" + path.stem)
                snapshots.append({key: value.get(key) for key in ("revision", "observed_at", "actor", "reason")})
        directory = self.path(HISTORY_RELATIVE_PATH / "events")
        events = []
        if directory.exists():
            for path in sorted(directory.glob("*.json")):
                path = self.path(path.relative_to(self.root))
                event = _json_file(path)
                if event.get("id") != path.stem or event.get("action") not in ("update", "rollback"):
                    raise PolicyError("Corrupt policy history event", "history_error")
                _identity(event)
                _string(event.get("at"), "history timestamp", limit=128)
                self._load_snapshot(_revision(event.get("previous_revision")))
                self._load_snapshot(_revision(event.get("revision")))
                events.append(event)
        return {"current_revision": current["revision"], "current_recorded": any(item["revision"] == current["revision"] for item in snapshots), "snapshots": sorted(snapshots, key=lambda item: item["observed_at"]), "events": sorted(events, key=lambda item: (item["at"], item["id"]))}

    def _target(self, request: dict) -> Path:
        raw = _string(request.get("target"), "target", limit=4096)
        target = Path(raw).expanduser().absolute()
        # Match the operator's lexical configuration AND its resolved destination.
        # A new symlink cannot broaden that configuration.
        configured = next((p for p in self.instruction_files if p == target), None)
        if configured is None:
            raise PolicyError("Instruction target is not in the operator-configured allowlist", "path_error")
        for path in (target, *target.parents):
            if path.is_symlink():
                raise PolicyError("Instruction target paths must not contain symlinks", "path_error")
        resolved = target.resolve()
        if resolved.is_relative_to(self.path(HISTORY_RELATIVE_PATH)) or resolved == self.path():
            raise PolicyError("Instruction target cannot overwrite policy storage", "path_error")
        if resolved.name == ".agent-policy.lock":
            raise PolicyError("Instruction target cannot overwrite the policy lock", "path_error")
        return resolved

    def _sync(self, request: dict, current: dict, *, no_git: bool, allow_branch: bool) -> dict:
        target = self._target(request)
        apply = request.get("apply", False)
        if not isinstance(apply, bool):
            raise PolicyError("apply must be a boolean")
        if "expected_revision" in request:
            _revision(request["expected_revision"])
        if "expected_target_revision" in request:
            _revision(request["expected_target_revision"], "expected_target_revision", missing=True)
        if "actor" in request or "reason" in request:
            _identity(request)
        actor, reason = _identity(request) if apply else ("", "")
        if apply:
            self._check_expected(request, current)
            _revision(request.get("expected_target_revision"), "expected_target_revision", missing=True)
        # The lock is separate from the replaceable target inode, and works even
        # when different memory roots are synchronizing the same instruction file.
        lock = target.with_name("." + target.name + ".memory-policy.lock")
        with _lock(lock):
            target = self._target(request)
            exists = target.exists()
            original = _read_text(target) if exists else ""
            revision = content_revision(original) if exists else "missing"
            if apply and revision != request["expected_target_revision"]:
                raise PolicyError("Instruction target revision conflict; request a new dry-run diff", "target_revision_conflict")
            proposed = managed_instructions(original, current["body"], current["revision"], source=str(self.path()))
            changed = proposed != original
            result = {"target": str(target), "applied": False, "changed": changed, "expected_revision": current["revision"], "expected_target_revision": revision, "proposed_target_revision": content_revision(proposed), "diff": "".join(difflib.unified_diff(original.splitlines(keepends=True), proposed.splitlines(keepends=True), fromfile=str(target), tofile=str(target))), "proposal": proposed}
            if apply:
                repo = _git_prepare(self.root, no_git, allow_branch, [target]) if changed else None
                # Recheck against noncooperating edits immediately before replace.
                latest = content_revision(_read_text(target)) if target.exists() else "missing"
                if latest != revision:
                    raise PolicyError("Instruction target changed during sync", "target_revision_conflict")
                if changed:
                    _atomic_write(self._target(request), proposed)
                git_result = _git_publish(repo, [target], no_git=no_git, message="memory: synchronize managed agent policy") if changed else {"status": "disabled" if no_git else "unchanged", "committed": False, "pushed": False}
                result.update(applied=True, actor=actor, reason=reason, write_success=True, git=git_result)
            return result

    def execute(self, request: dict, *, no_git: bool = False, allow_non_main_branch: bool = False) -> dict:
        if not isinstance(request, dict) or any(not isinstance(key, str) for key in request):
            raise PolicyError("request must be a JSON object with string keys")
        action = request.get("action")
        fields = {
            "read": {"action", "max_chars"},
            "history": {"action", "revision"},
            "update": {"action", "body", "expected_revision", "actor", "reason"},
            "rollback": {"action", "revision", "expected_revision", "actor", "reason"},
            "sync": {"action", "target", "apply", "expected_revision", "expected_target_revision", "actor", "reason"},
        }
        if not isinstance(action, str) or action not in fields:
            raise PolicyError("action must be read, update, history, rollback, or sync")
        unknown = request.keys() - fields[action]
        if unknown:
            raise PolicyError("Unknown request fields: " + ", ".join(sorted(unknown)))
        with _lock(self.path(Path("shared/policies/.agent-policy.lock"))):
            current = self._current()
            common = {"ok": True, "action": action}
            if action == "read":
                maximum = request.get("max_chars", MAX_BODY_CHARS)
                if type(maximum) is not int or not 1 <= maximum <= MAX_BODY_CHARS:
                    raise PolicyError(f"max_chars must be an integer between 1 and {MAX_BODY_CHARS}")
                body = current["body"]
                return {**common, **current, "body": body[:maximum], "truncated": len(body) > maximum}
            if action == "history":
                return {**common, **self._history(request, current)}
            if action == "sync":
                return {**common, **self._sync(request, current, no_git=no_git, allow_branch=allow_non_main_branch)}
            actor, reason = _identity(request)
            self._check_expected(request, current)
            body = (_string(request.get("body"), "body") if action == "update" else self._load_snapshot(_revision(request.get("revision"), "revision"))["body"])
            if BEGIN_MARKER in body or END_MARKER in body or "<!-- memory-rsi:policy:" in body:
                raise PolicyError("Policy body cannot contain managed instruction markers")
            owned = [self.path(), self._snapshot_path(current["revision"]), self._snapshot_path(content_revision(body))]
            repo = _git_prepare(self.root, no_git, allow_non_main_branch, owned)
            paths = [self._save_snapshot(current["body"], "external" if current["exists"] else "default", "Observed canonical content before explicit policy change; external author not authenticated"), self._save_snapshot(body, actor, reason)]
            # Both rollback endpoints are durable before the canonical swap.
            latest = self._current()
            if latest["revision"] != current["revision"] or latest["exists"] != current["exists"]:
                raise PolicyError("Policy changed during update", "revision_conflict")
            _atomic_write(self.path(), body)
            paths.append(self.path())
            revision = content_revision(body)
            paths.append(self._event(action, current["revision"], revision, actor, reason))
            return {**common, "path": str(self.path()), "revision": revision, "previous_revision": current["revision"], "write_success": True, "review_guidance": REVIEW_GUIDANCE, "git": _git_publish(repo, paths, no_git=no_git, message=f"memory: {action} agent policy")}


def managed_instructions(original: str, policy_body: str, revision: str, *, source: str | None = None) -> str:
    """Replace exactly one valid marker block, preserving every external byte."""
    if "<!-- memory-rsi:policy:" in policy_body:
        raise PolicyError("Policy body contains reserved managed instruction markers")
    begin_count = original.count(BEGIN_MARKER)
    end_count = original.count(END_MARKER)
    # Also catch typos that would otherwise silently append a duplicate block.
    if original.count("<!-- memory-rsi:policy:") != begin_count + end_count:
        raise PolicyError("Malformed managed policy markers", "marker_error")
    reference = ("\n\nCanonical policy: " + json.dumps(source, ensure_ascii=False)
                 + ". Update with memory_policy, then preview and sync this managed copy."
                 if source is not None else "")
    block = BEGIN_MARKER + "\n" + "<!-- Policy revision: " + revision + " -->\n" + policy_body.rstrip("\r\n") + reference + "\n" + END_MARKER
    if begin_count == end_count == 0:
        separator = "" if not original or original.endswith("\n\n") else ("\n" if original.endswith("\n") else "\n\n")
        return original + separator + block + "\n"
    if begin_count != 1 or end_count != 1:
        raise PolicyError("Managed policy markers must be one complete, unique pair", "marker_error")
    start = original.index(BEGIN_MARKER)
    end = original.index(END_MARKER)
    if start >= end:
        raise PolicyError("Managed policy markers are out of order", "marker_error")
    for offset, marker in ((start, BEGIN_MARKER), (end, END_MARKER)):
        preceding = original[:offset].rsplit("\n", 1)[-1]
        following = original[offset + len(marker):].split("\n", 1)[0]
        if preceding.strip() or following.strip():
            raise PolicyError("Managed policy markers must occupy standalone lines", "marker_error")
    return original[:start] + block + original[end + len(END_MARKER):]
