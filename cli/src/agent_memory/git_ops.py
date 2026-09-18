"""Git operations for memory entry commits.

Handles the git add/commit/pull/push workflow after memory writes.
All subprocess calls use timeout=30 to prevent hangs (clone uses 120s).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class BranchMismatchError(RuntimeError):
    """Raised when a write is attempted while HEAD is not on the default branch.

    This is the issue #82 guard: the agent-memory CLI commits to whatever
    branch is currently checked out in the working tree. If a subagent left
    HEAD on a feature branch, memory writes silently land on the wrong
    branch and may be lost when the branch is later deleted.

    The error message includes the current branch, the expected default
    branch, an exact recovery command, and a hint about the override flag.
    """

    def __init__(
        self,
        current_branch: str,
        default_branch: str,
        repo_path: Path,
    ) -> None:
        self.current_branch = current_branch
        self.default_branch = default_branch
        self.repo_path = repo_path
        message = (
            f"refusing to write memory entry on branch '{current_branch}'\n"
            f"       expected branch: '{default_branch}'\n"
            f"\n"
            f"The agent-memory CLI commits to whatever branch is currently\n"
            f"checked out. If HEAD is on a non-default branch (likely because\n"
            f"a subagent left it that way), memory writes will silently land\n"
            f"on the wrong branch and may be lost.\n"
            f"\n"
            f"To fix (one-time, switch back to the default):\n"
            f"    git -C {repo_path} checkout {default_branch}\n"
            f"\n"
            f"If '{current_branch}' is genuinely your default branch (e.g.,\n"
            f"a fork using a custom name), teach the CLI once and forget:\n"
            f"    git -C {repo_path} remote set-head origin --auto\n"
            f"  or\n"
            f"    export AGENT_MEMORY_DEFAULT_BRANCH={current_branch}\n"
            f"\n"
            f"To override for one intentional non-default write:\n"
            f"    memory new --allow-non-main-branch ...\n"
            f"    memory update --allow-non-main-branch ...\n"
            f"    memory sync --allow-non-main-branch"
        )
        super().__init__(message)


def _run_git(
    args: list[str],
    repo_path: Path,
    error_context: str,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run a git command with standard safety settings.

    Args:
        args: Git command arguments (e.g. ["add", "file.md"]).
        repo_path: Repository root directory.
        error_context: Human-readable description for error messages.
        timeout: Command timeout in seconds (default 30).

    Returns:
        CompletedProcess result.

    Raises:
        RuntimeError: If the command fails (non-zero exit code).
    """
    cmd = ["git", "-C", str(repo_path)] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"{error_context}: command timed out after {timeout}s: {' '.join(cmd)}"
        ) from e

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"{error_context}: {stderr}")

    return result


def _run_git_raw(
    args: list[str],
    error_context: str,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """Run a git command without -C (for clone where repo doesn't exist yet).

    Args:
        args: Full git command arguments (e.g. ["clone", url, path]).
        error_context: Human-readable description for error messages.
        timeout: Command timeout in seconds (default 120 for clone).

    Returns:
        CompletedProcess result.

    Raises:
        RuntimeError: If the command fails (non-zero exit code).
    """
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"{error_context}: command timed out after {timeout}s: {' '.join(cmd)}"
        ) from e

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"{error_context}: {stderr}")

    return result


def git_current_branch(repo_path: Path) -> str:
    """Return the name of the currently checked-out branch.

    Wraps `git rev-parse --abbrev-ref HEAD`. Returns "HEAD" when the
    repository is in a detached-HEAD state.

    Args:
        repo_path: Repository root directory.

    Returns:
        The branch name (e.g., "main") or "HEAD" if detached.

    Raises:
        RuntimeError: If git fails (e.g., not a git repository).
    """
    result = _run_git(
        ["rev-parse", "--abbrev-ref", "HEAD"],
        repo_path,
        "git rev-parse --abbrev-ref HEAD failed",
    )
    return result.stdout.strip()


def git_default_branch(repo_path: Path) -> str:
    """Resolve the configured default branch for a repository.

    Resolution chain (first hit wins):
        1. AGENT_MEMORY_DEFAULT_BRANCH environment variable (escape hatch
           for unusual setups where neither origin/HEAD nor "main" is
           appropriate, e.g., legacy mirrors with a custom default).
        2. `git symbolic-ref refs/remotes/origin/HEAD` parsed to its
           branch name component (e.g., "refs/remotes/origin/main" -> "main").
           This is the authoritative answer when the remote is configured.
           Set it on a fresh clone with `git remote set-head origin --auto`.
        3. Literal fallback to "main".

    Args:
        repo_path: Repository root directory.

    Returns:
        The default branch name (e.g., "main", "master").
    """
    env_override = os.environ.get("AGENT_MEMORY_DEFAULT_BRANCH", "").strip()
    if env_override:
        return env_override

    try:
        result = _run_git(
            ["symbolic-ref", "refs/remotes/origin/HEAD"],
            repo_path,
            "git symbolic-ref refs/remotes/origin/HEAD failed",
        )
        ref = result.stdout.strip()
        # ref looks like "refs/remotes/origin/main" -- take the last segment
        if ref.startswith("refs/remotes/origin/"):
            return ref[len("refs/remotes/origin/"):]
    except RuntimeError:
        # origin/HEAD not set on this repo -- fall through to default
        pass

    return "main"


def assert_on_default_branch(
    repo_path: Path,
    allow_non_default: bool = False,
) -> None:
    """Verify HEAD is on the default branch before a write operation.

    This is the issue #82 guard: prevents memory writes from landing on
    orphaned feature branches when a subagent leaves HEAD in the wrong
    state. The check is a single point of enforcement called by every
    write path (commit_and_push for new/update, sync_repo for sync).

    Args:
        repo_path: Repository root directory.
        allow_non_default: When True, the check is skipped entirely.
            Wired through the CLI as `--allow-non-main-branch` for
            subagents that genuinely need to write while on a feature
            branch (e.g., a test fixture committed alongside code changes).

    Raises:
        BranchMismatchError: If HEAD is not on the default branch and
            allow_non_default is False. The exception message includes
            the current branch, the expected branch, the recovery command,
            and the override flag hint.
    """
    if allow_non_default:
        return

    current = git_current_branch(repo_path)
    default = git_default_branch(repo_path)

    if current != default:
        raise BranchMismatchError(
            current_branch=current,
            default_branch=default,
            repo_path=repo_path,
        )


def git_add(file_path: Path, repo_path: Path) -> None:
    """Stage a file for commit.

    Args:
        file_path: Path to the file to stage (absolute or relative to repo).
        repo_path: Repository root directory.

    Raises:
        RuntimeError: If git add fails.
    """
    _run_git(["add", str(file_path)], repo_path, "git add failed")


def git_commit(message: str, repo_path: Path) -> str:
    """Commit staged changes with the given message.

    Args:
        message: Commit message.
        repo_path: Repository root directory.

    Returns:
        The commit SHA (full 40-character hash).

    Raises:
        RuntimeError: If commit fails or SHA cannot be retrieved.
    """
    _run_git(["commit", "-m", message], repo_path, "git commit failed")

    result = _run_git(
        ["rev-parse", "HEAD"], repo_path, "git rev-parse failed"
    )
    sha = result.stdout.strip()
    if not sha:
        raise RuntimeError("git rev-parse HEAD returned empty output")
    return sha


def git_pull(repo_path: Path) -> None:
    """Pull latest changes from remote using rebase.

    Uses --rebase to keep history linear and avoid merge commits.

    Args:
        repo_path: Repository root directory.

    Raises:
        RuntimeError: If pull fails.
    """
    _run_git(["pull", "--rebase"], repo_path, "git pull --rebase failed")


def git_push(repo_path: Path) -> None:
    """Push commits to remote.

    Args:
        repo_path: Repository root directory.

    Raises:
        RuntimeError: If push fails.
    """
    _run_git(["push"], repo_path, "git push failed")


def git_clone(remote_url: str, local_path: Path) -> None:
    """Clone a remote repository to a local path.

    Args:
        remote_url: Git remote URL (HTTPS or SSH).
        local_path: Target directory for the clone.

    Raises:
        RuntimeError: If clone fails (bad URL, no access, path exists).
    """
    _run_git_raw(
        ["clone", remote_url, str(local_path)],
        "git clone failed",
        timeout=120,
    )


def git_repo_root(path: Path) -> Path:
    """Find the git repository root for a given path.

    Uses `git rev-parse --show-toplevel` to resolve the repository
    root directory. This is essential when the CLI is invoked from
    a different working directory than the memory repository.

    Args:
        path: Any path inside a git repository (file or directory).

    Returns:
        The repository root as an absolute Path.

    Raises:
        RuntimeError: If the path is not inside a git repository.
    """
    target = path if path.is_dir() else path.parent
    result = _run_git(
        ["rev-parse", "--show-toplevel"],
        target,
        f"Not a git repository: {path}",
    )
    return Path(result.stdout.strip())


def git_is_repo(path: Path) -> bool:
    """Check if a path is inside a git repository.

    Args:
        path: Directory to check.

    Returns:
        True if path is a git working tree, False otherwise.
    """
    if not path.exists():
        return False
    try:
        _run_git(
            ["rev-parse", "--is-inside-work-tree"],
            path,
            "git check",
        )
        return True
    except RuntimeError:
        return False


def git_remote_url(repo_path: Path) -> str:
    """Get the origin remote URL of a repository.

    Args:
        repo_path: Repository root directory.

    Returns:
        Remote URL string, or empty string if no origin remote.
    """
    try:
        result = _run_git(
            ["remote", "get-url", "origin"],
            repo_path,
            "git remote get-url failed",
        )
        return result.stdout.strip()
    except RuntimeError:
        return ""


def git_pull_merge(repo_path: Path) -> str:
    """Pull latest changes from remote using merge strategy.

    Uses --no-rebase for shared directories where merge commits
    are preferred over rebase to preserve both sides of conflicts.

    Args:
        repo_path: Repository root directory.

    Returns:
        Pull output text (for parsing changed files).

    Raises:
        RuntimeError: If pull fails.
    """
    result = _run_git(
        ["pull", "--no-rebase"],
        repo_path,
        "git pull --no-rebase failed",
    )
    return result.stdout + result.stderr


def git_diff_names(repo_path: Path, from_ref: str, to_ref: str) -> list[str]:
    """Get list of changed file paths between two refs.

    Uses --name-status for efficient change detection at 100k file scale.

    Args:
        repo_path: Repository root directory.
        from_ref: Starting ref (commit SHA or branch name).
        to_ref: Ending ref (commit SHA or branch name).

    Returns:
        List of "STATUS\\tFILENAME" lines (e.g. ["A\\tnew.md", "M\\tupdated.md"]).
    """
    result = _run_git(
        ["diff", "--name-status", from_ref, to_ref],
        repo_path,
        "git diff --name-status failed",
    )
    output = result.stdout.strip()
    if not output:
        return []
    return output.split("\n")


def git_rev_parse_head(repo_path: Path) -> str:
    """Get the current HEAD commit SHA.

    Args:
        repo_path: Repository root directory.

    Returns:
        Full 40-character commit SHA.

    Raises:
        RuntimeError: If not in a git repo or HEAD is unset.
    """
    result = _run_git(
        ["rev-parse", "HEAD"], repo_path, "git rev-parse HEAD failed"
    )
    sha = result.stdout.strip()
    if not sha:
        raise RuntimeError("git rev-parse HEAD returned empty output")
    return sha


def git_has_uncommitted(repo_path: Path) -> bool:
    """Check if there are uncommitted changes (staged or unstaged).

    Args:
        repo_path: Repository root directory.

    Returns:
        True if working tree is dirty, False if clean.
    """
    result = _run_git(
        ["status", "--porcelain"],
        repo_path,
        "git status failed",
    )
    return bool(result.stdout.strip())


def git_add_all(repo_path: Path) -> None:
    """Stage all changes (new, modified, deleted).

    Args:
        repo_path: Repository root directory.

    Raises:
        RuntimeError: If git add fails.
    """
    _run_git(["add", "-A"], repo_path, "git add -A failed")


def commit_and_push(
    file_path: Path,
    agent_id: str,
    action: str,
    entry_name: str,
    repo_path: Path,
    allow_non_default_branch: bool = False,
) -> str:
    """Full git workflow: branch check, add, commit, pull --rebase, push with retry.

    Commit message format: memory({agent_id}): {action} {entry_name}

    Strategy:
        0. assert_on_default_branch (issue #82 guard)
        1. git add {file_path}
        2. git commit with formatted message
        3. git pull --rebase (with cache conflict auto-resolution)
        4. git push with retry on rejection (exponential backoff)

    Handles concurrent push scenarios where multiple agents write
    to the same repo. The .agent-memory-cache binary file is
    auto-resolved with --theirs during rebase conflicts.

    Args:
        file_path: Path to the file to commit.
        agent_id: Agent identifier for commit message.
        action: Action performed ("add" or "update").
        entry_name: Name of the memory entry.
        repo_path: Repository root directory.
        allow_non_default_branch: When True, skip the issue #82 branch
            check that refuses to write while HEAD is on a non-default
            branch. Wired through the CLI as `--allow-non-main-branch`.

    Returns:
        The commit SHA.

    Raises:
        BranchMismatchError: If HEAD is on a non-default branch and
            allow_non_default_branch is False.
        RuntimeError: If any step fails after retries are exhausted.
    """
    from agent_memory.push_retry import (
        _pull_rebase_with_cache_resolution,
        push_with_retry,
    )

    assert_on_default_branch(repo_path, allow_non_default=allow_non_default_branch)

    message = f"memory({agent_id}): {action} {entry_name}"

    git_add(file_path, repo_path)
    sha = git_commit(message, repo_path)

    if not _pull_rebase_with_cache_resolution(repo_path):
        raise RuntimeError(
            f"git pull --rebase failed after commit {sha} with "
            f"unresolvable conflicts. Resolve manually in {repo_path}."
        )

    push_with_retry(repo_path)

    return sha
