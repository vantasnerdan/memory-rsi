"""Clone and sync operations for agent memory repositories.

Handles initial cloning, pulling changes, pushing local changes,
and reporting what changed between syncs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_memory.git_ops import (
    assert_on_default_branch,
    git_add_all,
    git_clone,
    git_commit,
    git_diff_names,
    git_has_uncommitted,
    git_is_repo,
    git_pull_merge,
    git_push,
    git_remote_url,
    git_rev_parse_head,
)
from agent_memory.parser import parse_frontmatter


@dataclass
class FileChange:
    """A single file change detected during sync."""

    status: str  # "added", "modified", "deleted", "renamed"
    path: str
    description: str = ""  # frontmatter description if available


@dataclass
class CloneResult:
    """Result of a clone operation."""

    already_existed: bool
    local_path: str
    remote_url: str
    message: str


@dataclass
class SyncResult:
    """Result of a sync operation."""

    pulled: bool
    pushed: bool
    changes: list[FileChange] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    pre_sync_sha: str = ""
    post_sync_sha: str = ""
    message: str = ""


def _parse_diff_status(status_char: str) -> str:
    """Convert git diff status character to human-readable string."""
    mapping = {
        "A": "added",
        "M": "modified",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "T": "type-changed",
    }
    return mapping.get(status_char, "unknown")


def _read_description(repo_path: Path, file_path: str) -> str:
    """Read the frontmatter description from a file, returning empty on failure."""
    full_path = repo_path / file_path
    if not full_path.exists() or not full_path.is_file():
        return ""
    if not file_path.endswith(".md"):
        return ""
    try:
        text = full_path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        return fm.description
    except (ValueError, OSError):
        return ""


def _collect_changes(
    repo_path: Path, diff_lines: list[str]
) -> list[FileChange]:
    """Parse git diff --name-status output into FileChange objects.

    Only includes .md files (memory entries), skips non-markdown files.
    Reads frontmatter description for added and modified files.
    """
    changes: list[FileChange] = []
    for line in diff_lines:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status_char = parts[0].strip()[0]  # Handle R100, C100 etc.
        # Renames/copies have two paths: old\tnew -- use the new path
        file_path = parts[-1].strip()
        if not file_path.endswith(".md"):
            continue
        status = _parse_diff_status(status_char)
        description = ""
        if status != "deleted":
            description = _read_description(repo_path, file_path)
        changes.append(FileChange(
            status=status,
            path=file_path,
            description=description,
        ))
    return changes


def _validate_changed_files(
    repo_path: Path, changes: list[FileChange]
) -> list[str]:
    """Run non-blocking schema validation on changed .md files.

    Returns warning strings only (does not raise or block).
    """
    from agent_memory.validator import validate_file

    warnings: list[str] = []
    for change in changes:
        if change.status == "deleted":
            continue
        full_path = repo_path / change.path
        if not full_path.exists():
            continue
        result = validate_file(full_path)
        for error in result.errors:
            warnings.append(f"{change.path}: {error}")
        for warning in result.warnings:
            warnings.append(f"{change.path}: {warning}")
    return warnings


def clone_repo(remote_url: str, local_path: Path) -> CloneResult:
    """Clone the memory repository to a local path.

    If the path already exists and is a git repo with the same remote,
    skip the clone and report as already existing.

    Args:
        remote_url: Git remote URL (HTTPS or SSH).
        local_path: Target directory for the clone.

    Returns:
        CloneResult with status information.

    Raises:
        RuntimeError: If clone fails or path exists with different remote.
    """
    if local_path.exists():
        if git_is_repo(local_path):
            existing_url = git_remote_url(local_path)
            if existing_url == remote_url:
                return CloneResult(
                    already_existed=True,
                    local_path=str(local_path),
                    remote_url=remote_url,
                    message=f"Repository already cloned at {local_path}",
                )
            raise RuntimeError(
                f"Directory {local_path} exists but has different remote: "
                f"expected {remote_url}, found {existing_url}"
            )
        raise RuntimeError(
            f"Directory {local_path} exists but is not a git repository. "
            f"Remove it or choose a different AGENT_MEMORY_PATH."
        )

    git_clone(remote_url, local_path)
    return CloneResult(
        already_existed=False,
        local_path=str(local_path),
        remote_url=remote_url,
        message=f"Cloned {remote_url} to {local_path}",
    )


def sync_repo(
    repo_path: Path,
    agent_id: str,
    pull_only: bool = False,
    push_only: bool = False,
    allow_non_default_branch: bool = False,
) -> SyncResult:
    """Synchronize the local memory repository with remote.

    Workflow:
    1. Record pre-sync HEAD
    2. If uncommitted local changes exist:
       a. Verify HEAD is on the default branch (issue #82 guard)
       b. Commit them
    3. Pull latest (merge strategy for shared/ safety)
    4. Push local commits
    5. Detect changed files via git diff
    6. Read frontmatter descriptions for changed files
    7. Run non-blocking validation on changed files

    Args:
        repo_path: Local repository path.
        agent_id: Agent identifier for commit messages.
        pull_only: Only pull, do not push local changes.
        push_only: Only push, do not pull remote changes.
        allow_non_default_branch: When True, skip the issue #82 branch
            check that refuses to commit uncommitted changes while HEAD
            is on a non-default branch. The check is only relevant when
            sync_repo will actually create a commit (uncommitted changes
            exist and pull_only is False).

    Returns:
        SyncResult with change details and validation warnings.

    Raises:
        BranchMismatchError: If HEAD is on a non-default branch, there
            are uncommitted changes to commit, and allow_non_default_branch
            is False.
        RuntimeError: If the path is not a git repository or git fails.
    """
    if not git_is_repo(repo_path):
        raise RuntimeError(
            f"Not a git repository: {repo_path}. "
            f"Run 'memory clone' first."
        )

    pre_sha = git_rev_parse_head(repo_path)
    pushed = False
    pulled = False

    # Step 1: Commit any uncommitted local changes
    has_local = git_has_uncommitted(repo_path)
    if has_local and not pull_only:
        # Issue #82 guard: refuse to commit on a non-default branch unless
        # the caller explicitly opted in. Only fires when we'd actually
        # create a commit (otherwise sync is read-only and harmless).
        assert_on_default_branch(
            repo_path,
            allow_non_default=allow_non_default_branch,
        )
        git_add_all(repo_path)
        git_commit(
            f"memory({agent_id}): sync uncommitted changes",
            repo_path,
        )
        pushed = True

    # Step 2: Pull latest changes (merge, not rebase)
    if not push_only:
        pulled = True
        try:
            git_pull_merge(repo_path)
        except RuntimeError as e:
            err_msg = str(e)
            if "CONFLICT" in err_msg.upper():
                raise RuntimeError(
                    f"Merge conflict during sync. Resolve manually in "
                    f"{repo_path} then run 'memory sync' again. "
                    f"Error: {err_msg}"
                ) from e
            raise

    # Step 3: Push if we had local commits
    if pushed and not pull_only:
        try:
            git_push(repo_path)
        except RuntimeError:
            # Retry once after pull
            try:
                git_pull_merge(repo_path)
                git_push(repo_path)
            except RuntimeError as retry_err:
                raise RuntimeError(
                    f"Push failed after retry. Local commits exist but "
                    f"were not pushed to remote: {retry_err}"
                ) from retry_err

    post_sha = git_rev_parse_head(repo_path)

    # Step 4: Detect changes
    changes: list[FileChange] = []
    if pre_sha != post_sha:
        diff_lines = git_diff_names(repo_path, pre_sha, post_sha)
        changes = _collect_changes(repo_path, diff_lines)

    # Step 5: Validate changed files (non-blocking)
    validation_warnings = _validate_changed_files(repo_path, changes)

    # Build summary message
    if not changes:
        message = "Already up to date."
    else:
        added = sum(1 for c in changes if c.status == "added")
        modified = sum(1 for c in changes if c.status == "modified")
        deleted = sum(1 for c in changes if c.status == "deleted")
        parts = []
        if added:
            parts.append(f"{added} added")
        if modified:
            parts.append(f"{modified} modified")
        if deleted:
            parts.append(f"{deleted} deleted")
        message = f"Synced: {', '.join(parts)}."

    return SyncResult(
        pulled=pulled,
        pushed=pushed,
        changes=changes,
        validation_warnings=validation_warnings,
        pre_sync_sha=pre_sha,
        post_sync_sha=post_sha,
        message=message,
    )
