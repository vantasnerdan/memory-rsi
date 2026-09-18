"""Retry strategy for concurrent git push operations.

Implements exponential backoff with stash/pop for dirty working trees
and auto-resolution of .agent-memory-cache binary conflicts during rebase.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_FILE = ".agent-memory-cache"
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_DELAYS = (0.5, 1.0, 2.0)


def _run_git_check(
    args: list[str], repo_path: Path, timeout: int = 30
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result without raising on failure."""
    cmd = ["git", "-C", str(repo_path)] + args
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=cmd, returncode=128, stdout="", stderr="timed out"
        )


def _is_push_rejection(stderr: str) -> bool:
    """Detect if a push failure is due to remote being ahead (not network)."""
    rejection_markers = [
        "rejected",
        "fetch first",
        "non-fast-forward",
        "failed to push",
        "stale info",
    ]
    lower = stderr.lower()
    return any(marker in lower for marker in rejection_markers)


def _has_dirty_tree(repo_path: Path) -> bool:
    """Check if working tree has uncommitted changes."""
    result = _run_git_check(["status", "--porcelain"], repo_path)
    return bool(result.stdout.strip())


def _stash_save(repo_path: Path) -> bool:
    """Stash dirty working tree. Returns True if stash was created."""
    if not _has_dirty_tree(repo_path):
        return False
    result = _run_git_check(["stash", "push", "-m", "push-retry"], repo_path)
    if result.returncode != 0:
        logger.warning("git stash failed: %s", result.stderr.strip())
        return False
    # "No local changes to save" means nothing was stashed
    if "No local changes" in result.stdout:
        return False
    return True


def _stash_pop(repo_path: Path) -> bool:
    """Pop the most recent stash. Returns True on success."""
    result = _run_git_check(["stash", "pop"], repo_path)
    if result.returncode != 0:
        logger.warning("git stash pop failed: %s", result.stderr.strip())
        return False
    return True


def _resolve_cache_conflict(repo_path: Path) -> bool:
    """Resolve .agent-memory-cache binary conflict by taking theirs.

    During rebase, binary files like .agent-memory-cache cannot be
    merged. We always take the remote version since the cache is
    derived data that can be rebuilt.

    Returns True if conflict was resolved, False if no conflict found.
    """
    cache_path = repo_path / CACHE_FILE
    if not cache_path.exists():
        return False

    # Check if cache file is in conflict state
    result = _run_git_check(
        ["diff", "--name-only", "--diff-filter=U"], repo_path
    )
    conflicted = result.stdout.strip().split("\n") if result.stdout.strip() else []
    if CACHE_FILE not in conflicted:
        return False

    logger.info("Resolving %s conflict with --theirs", CACHE_FILE)
    checkout = _run_git_check(
        ["checkout", "--theirs", CACHE_FILE], repo_path
    )
    if checkout.returncode != 0:
        logger.warning(
            "checkout --theirs %s failed: %s",
            CACHE_FILE,
            checkout.stderr.strip(),
        )
        return False

    add = _run_git_check(["add", CACHE_FILE], repo_path)
    return add.returncode == 0


def _abort_rebase_if_active(repo_path: Path) -> None:
    """Abort an in-progress rebase if one exists."""
    rebase_dir = repo_path / ".git" / "rebase-merge"
    rebase_apply = repo_path / ".git" / "rebase-apply"
    if rebase_dir.exists() or rebase_apply.exists():
        _run_git_check(["rebase", "--abort"], repo_path)


def _pull_rebase_with_cache_resolution(repo_path: Path) -> bool:
    """Pull with rebase, auto-resolving cache conflicts.

    Returns True on success, False on unresolvable conflict.
    """
    result = _run_git_check(["pull", "--rebase"], repo_path)
    if result.returncode == 0:
        return True

    # Check if failure is due to cache conflict
    if _resolve_cache_conflict(repo_path):
        # Continue the rebase after resolving
        cont = _run_git_check(["rebase", "--continue"], repo_path)
        if cont.returncode == 0:
            return True
        logger.warning("rebase --continue failed: %s", cont.stderr.strip())

    # Unresolvable conflict -- abort rebase to restore clean state
    _abort_rebase_if_active(repo_path)
    return False


def push_with_retry(
    repo_path: Path,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_delays: tuple[float, ...] = DEFAULT_BACKOFF_DELAYS,
) -> None:
    """Push to remote with retry on rejection.

    Strategy per attempt:
    1. git push
    2. If rejected (remote ahead):
       a. Stash dirty files (e.g., .agent-memory-cache)
       b. git pull --rebase (with cache conflict auto-resolution)
       c. Stash pop (if stash was created)
       d. Retry push
    3. Exponential backoff between retries

    Args:
        repo_path: Repository root directory.
        max_retries: Maximum push attempts (default 3).
        backoff_delays: Seconds to wait before each retry.

    Raises:
        RuntimeError: If push fails due to network/auth error (not retryable).
        RuntimeError: If all retries exhausted with manual resolution steps.
    """
    last_error = ""

    for attempt in range(max_retries):
        # Try push
        push_result = _run_git_check(["push"], repo_path)
        if push_result.returncode == 0:
            if attempt > 0:
                logger.info("Push succeeded on attempt %d", attempt + 1)
            return

        stderr = push_result.stderr.strip()
        last_error = stderr

        # Non-retryable errors (network, auth, etc.)
        if not _is_push_rejection(stderr):
            raise RuntimeError(
                f"git push failed (not a push rejection): {stderr}"
            )

        logger.info(
            "Push rejected (attempt %d/%d), retrying...",
            attempt + 1,
            max_retries,
        )

        # Backoff before retry
        delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
        time.sleep(delay)

        # Stash dirty tree (cache file changes during operations)
        stashed = _stash_save(repo_path)

        # Pull with rebase + cache conflict resolution
        pull_ok = _pull_rebase_with_cache_resolution(repo_path)

        # Restore stashed changes
        if stashed:
            if not _stash_pop(repo_path):
                # Stash pop failed -- cache conflict in stash
                _resolve_cache_conflict(repo_path)

        if not pull_ok:
            last_error = "pull --rebase failed with unresolvable conflicts"
            logger.warning(
                "Pull rebase failed on attempt %d, will retry push anyway",
                attempt + 1,
            )

    # All retries exhausted
    raise RuntimeError(
        f"git push failed after {max_retries} attempts. "
        f"Last error: {last_error}\n"
        f"Manual resolution steps:\n"
        f"  1. cd {repo_path}\n"
        f"  2. git stash  (if dirty tree)\n"
        f"  3. git pull --rebase\n"
        f"  4. git checkout --theirs {CACHE_FILE}  (if cache conflict)\n"
        f"  5. git add {CACHE_FILE} && git rebase --continue\n"
        f"  6. git stash pop  (if stashed)\n"
        f"  7. git push"
    )
