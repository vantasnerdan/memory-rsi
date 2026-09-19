"""Narrow Git publication for explicit policy-owned paths.

Persistence is already complete when publication starts. Return an inspectable
Git outcome instead of conflating commit/push failures with file-write failures.
Unlike legacy generic entry publication, policy publication never pulls/rebases
unrelated work and never commits another caller's staged files.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess:
    """Use literal file pathspecs, bounded execution, and no shell interpolation."""
    return subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def publish_git(repo: Path | None, paths: list[Path], *, no_git: bool, message: str) -> dict:
    """Commit only owned files; report a local-only commit without claiming push."""
    result = {
        "status": "disabled" if no_git else "not_a_repository",
        "committed": False,
        "pushed": False,
    }
    if no_git or repo is None:
        return result
    owned = sorted({str(path.relative_to(repo)) for path in paths if path.is_relative_to(repo)})
    skipped = [str(path) for path in paths if not path.is_relative_to(repo)]
    result.update(status="unchanged", skipped_files=skipped)
    if not owned:
        result["status"] = "outside_repository" if skipped else "unchanged"
        return result
    try:
        # Recheck immediately before staging as well as the store's pre-write
        # preflight, so a newly staged owned blob is not silently discarded.
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
            return result
        committed = run_git(repo, ["commit", "--only", "-m", message, "--", *owned])
        if committed.returncode:
            raise RuntimeError(committed.stderr.strip())
        result.update(status="committed", committed=True)
        head = run_git(repo, ["rev-parse", "HEAD"])
        if head.returncode or not head.stdout.strip():
            raise RuntimeError(head.stderr.strip() or "Commit succeeded but HEAD could not be read")
        result["commit"] = head.stdout.strip()
        remotes = run_git(repo, ["remote"])
        if remotes.returncode:
            raise RuntimeError(remotes.stderr.strip())
        if not remotes.stdout.strip():
            result["status"] = "committed_no_remote"
            return result
        upstream = run_git(repo, ["rev-parse", "--abbrev-ref", "@{upstream}"])
        if upstream.returncode:
            result["status"] = "not_configured"
            return result
        branch = run_git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        if branch.returncode or not branch.stdout.strip():
            result["status"] = "not_configured"
            return result
        branch_name = branch.stdout.strip()
        remote = run_git(repo, ["config", "--get", f"branch.{branch_name}.remote"])
        target = run_git(repo, ["config", "--get", f"branch.{branch_name}.merge"])
        if remote.returncode or target.returncode or not remote.stdout.strip() or not target.stdout.strip():
            result["status"] = "not_configured"
            return result
        remote_name, target_ref = remote.stdout.strip(), target.stdout.strip()
        if remote_name == ".":
            result["status"] = "committed_local_only"
            return result
        if not target_ref.startswith("refs/heads/"):
            result["status"] = "not_configured"
            return result
        # An explicit refspec overrides push.default=matching and remote push
        # refspecs. Do not pull/rebase unrelated work or implicitly push tags.
        pushed = run_git(repo, ["push", "--no-follow-tags", "--", remote_name, f"HEAD:{target_ref}"])
        if pushed.returncode:
            result.update(status="push_failed", error=pushed.stderr.strip())
        else:
            result.update(status="pushed", pushed=True)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        result.update(status="git_failed", error=str(exc))
    return result
