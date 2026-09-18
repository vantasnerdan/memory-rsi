"""Tests for push retry strategy (push_retry.py)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_memory.push_retry import (
    CACHE_FILE,
    _has_dirty_tree,
    _is_push_rejection,
    _pull_rebase_with_cache_resolution,
    _resolve_cache_conflict,
    _stash_pop,
    _stash_save,
    push_with_retry,
)


def _cp(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    """Helper to build a CompletedProcess for mocking."""
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestIsPushRejection:
    def test_rejected_message(self) -> None:
        assert _is_push_rejection("! [rejected] main -> main (fetch first)")

    def test_fetch_first(self) -> None:
        assert _is_push_rejection("Updates were rejected because fetch first")

    def test_non_fast_forward(self) -> None:
        assert _is_push_rejection("non-fast-forward")

    def test_failed_to_push(self) -> None:
        assert _is_push_rejection("error: failed to push some refs")

    def test_network_error_not_rejection(self) -> None:
        assert not _is_push_rejection("fatal: Could not read from remote")

    def test_auth_error_not_rejection(self) -> None:
        assert not _is_push_rejection("Permission denied (publickey)")

    def test_empty_string(self) -> None:
        assert not _is_push_rejection("")


class TestHasDirtyTree:
    @patch("agent_memory.push_retry.subprocess.run")
    def test_clean_tree(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _cp(stdout="")
        assert not _has_dirty_tree(tmp_path)

    @patch("agent_memory.push_retry.subprocess.run")
    def test_dirty_tree(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _cp(stdout=" M .agent-memory-cache\n")
        assert _has_dirty_tree(tmp_path)


class TestStashSave:
    @patch("agent_memory.push_retry._has_dirty_tree")
    @patch("agent_memory.push_retry.subprocess.run")
    def test_stash_created(
        self, mock_run: MagicMock, mock_dirty: MagicMock, tmp_path: Path
    ) -> None:
        mock_dirty.return_value = True
        mock_run.return_value = _cp(stdout="Saved working directory\n")
        assert _stash_save(tmp_path) is True

    @patch("agent_memory.push_retry._has_dirty_tree")
    def test_clean_tree_no_stash(
        self, mock_dirty: MagicMock, tmp_path: Path
    ) -> None:
        mock_dirty.return_value = False
        assert _stash_save(tmp_path) is False

    @patch("agent_memory.push_retry._has_dirty_tree")
    @patch("agent_memory.push_retry.subprocess.run")
    def test_no_local_changes_message(
        self, mock_run: MagicMock, mock_dirty: MagicMock, tmp_path: Path
    ) -> None:
        mock_dirty.return_value = True
        mock_run.return_value = _cp(stdout="No local changes to save\n")
        assert _stash_save(tmp_path) is False

    @patch("agent_memory.push_retry._has_dirty_tree")
    @patch("agent_memory.push_retry.subprocess.run")
    def test_stash_failure(
        self, mock_run: MagicMock, mock_dirty: MagicMock, tmp_path: Path
    ) -> None:
        mock_dirty.return_value = True
        mock_run.return_value = _cp(returncode=1, stderr="stash error")
        assert _stash_save(tmp_path) is False


class TestStashPop:
    @patch("agent_memory.push_retry.subprocess.run")
    def test_pop_success(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _cp()
        assert _stash_pop(tmp_path) is True

    @patch("agent_memory.push_retry.subprocess.run")
    def test_pop_failure(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _cp(returncode=1, stderr="conflict")
        assert _stash_pop(tmp_path) is False


class TestResolveCacheConflict:
    @patch("agent_memory.push_retry.subprocess.run")
    def test_resolves_conflict(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        # Create the cache file so exists() check passes
        (tmp_path / CACHE_FILE).touch()
        mock_run.side_effect = [
            _cp(stdout=f"{CACHE_FILE}\n"),  # diff --diff-filter=U
            _cp(),  # checkout --theirs
            _cp(),  # add
        ]
        assert _resolve_cache_conflict(tmp_path) is True

    @patch("agent_memory.push_retry.subprocess.run")
    def test_no_conflict(self, mock_run: MagicMock, tmp_path: Path) -> None:
        (tmp_path / CACHE_FILE).touch()
        mock_run.return_value = _cp(stdout="")  # No conflicted files
        assert _resolve_cache_conflict(tmp_path) is False

    def test_no_cache_file(self, tmp_path: Path) -> None:
        assert _resolve_cache_conflict(tmp_path) is False

    @patch("agent_memory.push_retry.subprocess.run")
    def test_other_file_conflict_not_cache(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        (tmp_path / CACHE_FILE).touch()
        mock_run.return_value = _cp(stdout="some-other-file.md\n")
        assert _resolve_cache_conflict(tmp_path) is False


class TestPullRebaseWithCacheResolution:
    @patch("agent_memory.push_retry._run_git_check")
    def test_clean_pull(self, mock_git: MagicMock, tmp_path: Path) -> None:
        mock_git.return_value = _cp()
        assert _pull_rebase_with_cache_resolution(tmp_path) is True
        mock_git.assert_called_once_with(["pull", "--rebase"], tmp_path)

    @patch("agent_memory.push_retry._abort_rebase_if_active")
    @patch("agent_memory.push_retry._resolve_cache_conflict")
    @patch("agent_memory.push_retry._run_git_check")
    def test_cache_conflict_resolved(
        self,
        mock_git: MagicMock,
        mock_resolve: MagicMock,
        mock_abort: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_git.side_effect = [
            _cp(returncode=1, stderr="conflict"),  # pull fails
            _cp(),  # rebase --continue
        ]
        mock_resolve.return_value = True
        assert _pull_rebase_with_cache_resolution(tmp_path) is True
        mock_resolve.assert_called_once()

    @patch("agent_memory.push_retry._abort_rebase_if_active")
    @patch("agent_memory.push_retry._resolve_cache_conflict")
    @patch("agent_memory.push_retry._run_git_check")
    def test_unresolvable_conflict(
        self,
        mock_git: MagicMock,
        mock_resolve: MagicMock,
        mock_abort: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_git.return_value = _cp(returncode=1, stderr="conflict")
        mock_resolve.return_value = False
        assert _pull_rebase_with_cache_resolution(tmp_path) is False
        mock_abort.assert_called_once()


class TestPushWithRetry:
    @patch("agent_memory.push_retry._run_git_check")
    def test_succeeds_first_try(
        self, mock_git: MagicMock, tmp_path: Path
    ) -> None:
        mock_git.return_value = _cp()
        push_with_retry(tmp_path, backoff_delays=(0.01, 0.01, 0.01))
        mock_git.assert_called_once_with(["push"], tmp_path)

    @patch("agent_memory.push_retry._stash_pop")
    @patch("agent_memory.push_retry._stash_save")
    @patch("agent_memory.push_retry._pull_rebase_with_cache_resolution")
    @patch("agent_memory.push_retry._run_git_check")
    def test_succeeds_after_one_retry(
        self,
        mock_git: MagicMock,
        mock_pull: MagicMock,
        mock_stash: MagicMock,
        mock_pop: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_git.side_effect = [
            _cp(returncode=1, stderr="rejected"),  # first push
            _cp(),  # retry push
        ]
        mock_pull.return_value = True
        mock_stash.return_value = True
        mock_pop.return_value = True

        push_with_retry(tmp_path, backoff_delays=(0.01, 0.01, 0.01))
        assert mock_git.call_count == 2
        mock_stash.assert_called_once()
        mock_pull.assert_called_once()
        mock_pop.assert_called_once()

    @patch("agent_memory.push_retry._stash_pop")
    @patch("agent_memory.push_retry._stash_save")
    @patch("agent_memory.push_retry._pull_rebase_with_cache_resolution")
    @patch("agent_memory.push_retry._run_git_check")
    def test_no_stash_when_tree_clean(
        self,
        mock_git: MagicMock,
        mock_pull: MagicMock,
        mock_stash: MagicMock,
        mock_pop: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_git.side_effect = [
            _cp(returncode=1, stderr="rejected"),
            _cp(),  # retry succeeds
        ]
        mock_pull.return_value = True
        mock_stash.return_value = False  # clean tree

        push_with_retry(tmp_path, backoff_delays=(0.01, 0.01, 0.01))
        mock_pop.assert_not_called()

    @patch("agent_memory.push_retry._stash_save")
    @patch("agent_memory.push_retry._pull_rebase_with_cache_resolution")
    @patch("agent_memory.push_retry._run_git_check")
    def test_all_retries_exhausted(
        self,
        mock_git: MagicMock,
        mock_pull: MagicMock,
        mock_stash: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_git.return_value = _cp(returncode=1, stderr="rejected")
        mock_pull.return_value = True
        mock_stash.return_value = False

        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            push_with_retry(tmp_path, backoff_delays=(0.01, 0.01, 0.01))

    @patch("agent_memory.push_retry._run_git_check")
    def test_non_retryable_error_fails_immediately(
        self, mock_git: MagicMock, tmp_path: Path
    ) -> None:
        mock_git.return_value = _cp(
            returncode=1, stderr="Permission denied (publickey)"
        )
        with pytest.raises(RuntimeError, match="not a push rejection"):
            push_with_retry(tmp_path, backoff_delays=(0.01, 0.01, 0.01))
        mock_git.assert_called_once()

    @patch("agent_memory.push_retry._stash_save")
    @patch("agent_memory.push_retry._pull_rebase_with_cache_resolution")
    @patch("agent_memory.push_retry._run_git_check")
    def test_error_message_includes_manual_steps(
        self,
        mock_git: MagicMock,
        mock_pull: MagicMock,
        mock_stash: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_git.return_value = _cp(returncode=1, stderr="rejected")
        mock_pull.return_value = True
        mock_stash.return_value = False

        with pytest.raises(RuntimeError) as exc_info:
            push_with_retry(tmp_path, backoff_delays=(0.01, 0.01, 0.01))
        msg = str(exc_info.value)
        assert "Manual resolution steps" in msg
        assert "git stash" in msg
        assert "git pull --rebase" in msg
        assert "git checkout --theirs" in msg
        assert CACHE_FILE in msg

    @patch("agent_memory.push_retry._resolve_cache_conflict")
    @patch("agent_memory.push_retry._stash_pop")
    @patch("agent_memory.push_retry._stash_save")
    @patch("agent_memory.push_retry._pull_rebase_with_cache_resolution")
    @patch("agent_memory.push_retry._run_git_check")
    def test_stash_pop_conflict_resolves_cache(
        self,
        mock_git: MagicMock,
        mock_pull: MagicMock,
        mock_stash: MagicMock,
        mock_pop: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When stash pop fails (cache conflict), resolve it."""
        mock_git.side_effect = [
            _cp(returncode=1, stderr="rejected"),
            _cp(),  # retry push succeeds
        ]
        mock_pull.return_value = True
        mock_stash.return_value = True
        mock_pop.return_value = False  # stash pop fails
        mock_resolve.return_value = True

        push_with_retry(tmp_path, backoff_delays=(0.01, 0.01, 0.01))
        mock_resolve.assert_called_once()

    @patch("agent_memory.push_retry._stash_save")
    @patch("agent_memory.push_retry._pull_rebase_with_cache_resolution")
    @patch("agent_memory.push_retry._run_git_check")
    def test_custom_max_retries(
        self,
        mock_git: MagicMock,
        mock_pull: MagicMock,
        mock_stash: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_git.return_value = _cp(returncode=1, stderr="rejected")
        mock_pull.return_value = True
        mock_stash.return_value = False

        with pytest.raises(RuntimeError, match="failed after 5 attempts"):
            push_with_retry(
                tmp_path,
                max_retries=5,
                backoff_delays=(0.01,),
            )
        assert mock_git.call_count == 5

    @patch("agent_memory.push_retry._stash_pop")
    @patch("agent_memory.push_retry._stash_save")
    @patch("agent_memory.push_retry._pull_rebase_with_cache_resolution")
    @patch("agent_memory.push_retry._run_git_check")
    def test_succeeds_on_third_attempt(
        self,
        mock_git: MagicMock,
        mock_pull: MagicMock,
        mock_stash: MagicMock,
        mock_pop: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_git.side_effect = [
            _cp(returncode=1, stderr="rejected"),  # attempt 1
            _cp(returncode=1, stderr="rejected"),  # attempt 2
            _cp(),  # attempt 3 succeeds
        ]
        mock_pull.return_value = True
        mock_stash.return_value = False

        push_with_retry(tmp_path, backoff_delays=(0.01, 0.01, 0.01))
        assert mock_git.call_count == 3


class TestCommitAndPushIntegration:
    """Test that commit_and_push in git_ops uses push_with_retry."""

    @patch("agent_memory.git_ops.assert_on_default_branch")
    @patch("agent_memory.push_retry.push_with_retry")
    @patch("agent_memory.push_retry._pull_rebase_with_cache_resolution")
    @patch("agent_memory.git_ops.subprocess.run")
    def test_commit_and_push_uses_retry(
        self,
        mock_git_ops_run: MagicMock,
        mock_pull_rebase: MagicMock,
        mock_push: MagicMock,
        mock_assert_branch: MagicMock,
        tmp_path: Path,
    ) -> None:
        """commit_and_push delegates push to push_with_retry."""
        from agent_memory.git_ops import commit_and_push

        file_path = tmp_path / "entry.md"
        sha_value = "a" * 40

        # git_ops._run_git calls: add, commit, rev-parse
        mock_git_ops_run.side_effect = [
            _cp(),  # git add
            _cp(),  # git commit
            _cp(stdout=sha_value + "\n"),  # git rev-parse HEAD
        ]
        mock_pull_rebase.return_value = True

        sha = commit_and_push(file_path, "agent-1", "add", "entry", tmp_path)
        assert sha == sha_value
        mock_pull_rebase.assert_called_once_with(tmp_path)
        mock_push.assert_called_once_with(tmp_path)
        mock_assert_branch.assert_called_once_with(
            tmp_path, allow_non_default=False
        )
