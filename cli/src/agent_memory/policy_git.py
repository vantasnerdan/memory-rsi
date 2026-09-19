"""Narrow two-phase Git publication for explicit policy-owned paths.

Local staging/commit must run while the caller holds its writer locks. The
prepared publication captures one exact commit and upstream; network push can
then run after releasing those locks without staging newer live file contents
or accidentally publishing a later HEAD. Legacy publish_git remains a wrapper.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitPublication:
    """Owned internal publication state; never a live filesystem snapshot."""

    repo: Path | None
    result: dict
    remote: str | None = None
    target: str | None = None


def run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess:
    """Use literal file pathspecs, bounded execution, and no shell interpolation."""
    return subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def commit_git(repo: Path | None, paths: list[Path], *, no_git: bool, message: str) -> GitPublication:
    """Stage/commit exact owned live files under caller locks; never push/pull."""
    result = {
        "status": "disabled" if no_git else "not_a_repository",
        "committed": False,
        "pushed": False,
    }
    publication = GitPublication(repo, result)
    if no_git or repo is None:
        return publication
    owned = sorted({str(path.relative_to(repo)) for path in paths if path.is_relative_to(repo)})
    skipped = [str(path) for path in paths if not path.is_relative_to(repo)]
    result.update(status="unchanged", skipped_files=skipped)
    if not owned:
        result["status"] = "outside_repository" if skipped else "unchanged"
        return publication
    try:
        preexisting = run_git(repo, ["diff", "--cached", "--quiet", "--", *owned])
        if preexisting.returncode == 1:
            raise RuntimeError("Policy-owned files have staged changes; publication left the index untouched")
        if preexisting.returncode:
            raise RuntimeError(preexisting.stderr.strip())
        added = run_git(repo, ["add", "--", *owned])
        if added.returncode:
            raise RuntimeError(added.stderr.strip())
        difference = run_git(repo, ["diff", "--cached", "--quiet", "--", *owned])
        if difference.returncode not in (0, 1):
            raise RuntimeError(difference.stderr.strip())
        if difference.returncode == 0:
            return publication
        # Capture the commit produced by THIS command, not a subsequent moving
        # HEAD. Forty hex digits are complete SHA-1 or an unambiguous SHA-256
        # prefix; --verify resolves the latter and fails closed on ambiguity.
        committed = run_git(repo, ["-c", "core.abbrev=40", "commit", "--no-quiet", "--only", "-m", message, "--", *owned])
        if committed.returncode:
            raise RuntimeError(committed.stderr.strip())
        result.update(status="committed", committed=True)
        matches = re.findall(r"^\[[^\n]* ([0-9a-f]{40,64})\] ", committed.stdout, flags=re.MULTILINE)
        if len(matches) != 1:
            raise RuntimeError("Commit succeeded but its exact object ID could not be captured; publication stopped")
        exact = run_git(repo, ["rev-parse", "--verify", matches[0] + "^{commit}"])
        commit = exact.stdout.strip()
        if exact.returncode or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            raise RuntimeError("Commit succeeded but its exact object ID could not be verified; publication stopped")
        result["commit"] = commit
        remotes = run_git(repo, ["remote"])
        if remotes.returncode:
            raise RuntimeError(remotes.stderr.strip())
        if not remotes.stdout.strip():
            result["status"] = "committed_no_remote"
            return publication
        upstream = run_git(repo, ["rev-parse", "--abbrev-ref", "@{upstream}"])
        if upstream.returncode:
            result["status"] = "not_configured"
            return publication
        branch = run_git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        if branch.returncode or not branch.stdout.strip():
            result["status"] = "not_configured"
            return publication
        branch_name = branch.stdout.strip()
        remote = run_git(repo, ["config", "--get", f"branch.{branch_name}.remote"])
        target = run_git(repo, ["config", "--get", f"branch.{branch_name}.merge"])
        if remote.returncode or target.returncode or not remote.stdout.strip() or not target.stdout.strip():
            result["status"] = "not_configured"
            return publication
        remote_name, target_ref = remote.stdout.strip(), target.stdout.strip()
        if remote_name == ".":
            result["status"] = "committed_local_only"
            return publication
        if not target_ref.startswith("refs/heads/"):
            result["status"] = "not_configured"
            return publication
        publication.remote, publication.target = remote_name, target_ref
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        result.update(status="git_failed", error=str(exc))
    return publication


def push_git(publication: GitPublication) -> dict:
    """Push only the captured commit, without touching the index or live files."""
    result = dict(publication.result)
    if publication.remote is None or publication.target is None or publication.repo is None:
        return result
    try:
        # Explicit immutable source and destination override mutable HEAD,
        # push.default=matching, remote push refspecs, and followTags. Never force.
        pushed = run_git(publication.repo, ["push", "--no-follow-tags", "--", publication.remote,
                                             f"{result['commit']}:{publication.target}"])
        if pushed.returncode:
            result.update(status="push_failed", error=pushed.stderr.strip())
        else:
            result.update(status="pushed", pushed=True)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        result.update(status="git_failed", error=str(exc))
    return result


def publish_git(repo: Path | None, paths: list[Path], *, no_git: bool, message: str) -> dict:
    """Backward-compatible one-shot publication for existing policy callers."""
    return push_git(commit_git(repo, paths, no_git=no_git, message=message))
