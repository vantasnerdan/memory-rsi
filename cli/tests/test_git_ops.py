"""Tests for git operations (git_ops.py) with mocked subprocess."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from agent_memory.git_ops import (
    BranchMismatchError,
    _run_git,
    assert_on_default_branch,
    commit_and_push,
    git_add,
    git_commit,
    git_current_branch,
    git_default_branch,
    git_pull,
    git_push,
    git_repo_root,
)


def _make_completed_process(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    """Helper to build a CompletedProcess for mocking."""
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestRunGit:
    @patch("agent_memory.git_ops.subprocess.run")
    def test_success_path(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _make_completed_process(returncode=0, stdout="ok\n")
        result = _run_git(["status"], tmp_path, "test context")
        assert result.returncode == 0
        mock_run.assert_called_once_with(
            ["git", "-C", str(tmp_path), "status"],
            capture_output=True,
            text=True,
            timeout=30,
        )

    @patch("agent_memory.git_ops.subprocess.run")
    def test_nonzero_exit_raises_runtime_error(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = _make_completed_process(
            returncode=1, stderr="fatal: not a git repo"
        )
        with pytest.raises(RuntimeError, match="test context.*fatal: not a git repo"):
            _run_git(["status"], tmp_path, "test context")

    @patch("agent_memory.git_ops.subprocess.run")
    def test_timeout_raises_runtime_error(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        with pytest.raises(RuntimeError, match="timed out after 30s"):
            _run_git(["fetch"], tmp_path, "git fetch failed")


class TestGitAdd:
    @patch("agent_memory.git_ops.subprocess.run")
    def test_correct_git_command_args(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = _make_completed_process()
        file_path = tmp_path / "entry.md"
        git_add(file_path, tmp_path)
        mock_run.assert_called_once_with(
            ["git", "-C", str(tmp_path), "add", str(file_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )


class TestGitCommit:
    @patch("agent_memory.git_ops.subprocess.run")
    def test_commit_message_passed(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        # First call: git commit, second call: git rev-parse HEAD
        mock_run.side_effect = [
            _make_completed_process(returncode=0),
            _make_completed_process(
                returncode=0, stdout="abc123def456789012345678901234567890abcd\n"
            ),
        ]
        sha = git_commit("test commit message", tmp_path)
        # Check commit call includes the message
        commit_call = mock_run.call_args_list[0]
        assert commit_call == call(
            ["git", "-C", str(tmp_path), "commit", "-m", "test commit message"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert sha == "abc123def456789012345678901234567890abcd"

    @patch("agent_memory.git_ops.subprocess.run")
    def test_returns_sha(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.side_effect = [
            _make_completed_process(),
            _make_completed_process(stdout="deadbeef" * 5 + "\n"),
        ]
        sha = git_commit("msg", tmp_path)
        assert sha == "deadbeef" * 5

    @patch("agent_memory.git_ops.subprocess.run")
    def test_empty_sha_raises(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.side_effect = [
            _make_completed_process(),
            _make_completed_process(stdout=""),
        ]
        with pytest.raises(RuntimeError, match="empty output"):
            git_commit("msg", tmp_path)


class TestGitPull:
    @patch("agent_memory.git_ops.subprocess.run")
    def test_rebase_flag(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _make_completed_process()
        git_pull(tmp_path)
        mock_run.assert_called_once_with(
            ["git", "-C", str(tmp_path), "pull", "--rebase"],
            capture_output=True,
            text=True,
            timeout=30,
        )


class TestGitPush:
    @patch("agent_memory.git_ops.subprocess.run")
    def test_push_command(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _make_completed_process()
        git_push(tmp_path)
        mock_run.assert_called_once_with(
            ["git", "-C", str(tmp_path), "push"],
            capture_output=True,
            text=True,
            timeout=30,
        )


class TestCommitAndPush:
    """Tests for commit_and_push which delegates to push_retry module.

    The branch-check guard (issue #82) is patched out for these tests so
    they continue to assert the add/commit/pull/push sequence in isolation.
    Branch-check behavior is covered by TestCommitAndPushBranchGuard below.
    """

    @patch("agent_memory.git_ops.assert_on_default_branch")
    @patch("agent_memory.push_retry.push_with_retry")
    @patch("agent_memory.push_retry._pull_rebase_with_cache_resolution")
    @patch("agent_memory.git_ops.subprocess.run")
    def test_full_workflow_add_commit_pull_push(
        self,
        mock_run: MagicMock,
        mock_pull_rebase: MagicMock,
        mock_push_retry: MagicMock,
        mock_assert_branch: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify add -> commit -> rev-parse -> pull-rebase -> push-retry."""
        file_path = tmp_path / "entry.md"
        sha_value = "a" * 40

        mock_run.side_effect = [
            _make_completed_process(),  # git add
            _make_completed_process(),  # git commit
            _make_completed_process(stdout=sha_value + "\n"),  # git rev-parse HEAD
        ]
        mock_pull_rebase.return_value = True

        sha = commit_and_push(file_path, "agent-1", "add", "my-entry", tmp_path)
        assert sha == sha_value
        assert mock_run.call_count == 3
        mock_pull_rebase.assert_called_once_with(tmp_path)
        mock_push_retry.assert_called_once_with(tmp_path)
        mock_assert_branch.assert_called_once_with(
            tmp_path, allow_non_default=False
        )

    @patch("agent_memory.git_ops.assert_on_default_branch")
    @patch("agent_memory.push_retry.push_with_retry")
    @patch("agent_memory.push_retry._pull_rebase_with_cache_resolution")
    @patch("agent_memory.git_ops.subprocess.run")
    def test_commit_message_format(
        self,
        mock_run: MagicMock,
        mock_pull_rebase: MagicMock,
        mock_push_retry: MagicMock,
        mock_assert_branch: MagicMock,
        tmp_path: Path,
    ) -> None:
        file_path = tmp_path / "entry.md"
        sha_value = "b" * 40

        mock_run.side_effect = [
            _make_completed_process(),  # add
            _make_completed_process(),  # commit
            _make_completed_process(stdout=sha_value + "\n"),  # rev-parse
        ]
        mock_pull_rebase.return_value = True

        commit_and_push(file_path, "my-agent", "update", "shift-policy", tmp_path)

        commit_call = mock_run.call_args_list[1]
        message = commit_call[0][0][5]
        assert message == "memory(my-agent): update shift-policy"

    @patch("agent_memory.git_ops.assert_on_default_branch")
    @patch("agent_memory.push_retry.push_with_retry")
    @patch("agent_memory.push_retry._pull_rebase_with_cache_resolution")
    @patch("agent_memory.git_ops.subprocess.run")
    def test_push_retry_called_on_success(
        self,
        mock_run: MagicMock,
        mock_pull_rebase: MagicMock,
        mock_push_retry: MagicMock,
        mock_assert_branch: MagicMock,
        tmp_path: Path,
    ) -> None:
        """push_with_retry is always called after successful pull."""
        file_path = tmp_path / "entry.md"
        sha_value = "c" * 40

        mock_run.side_effect = [
            _make_completed_process(),  # add
            _make_completed_process(),  # commit
            _make_completed_process(stdout=sha_value + "\n"),  # rev-parse
        ]
        mock_pull_rebase.return_value = True

        sha = commit_and_push(file_path, "agent-1", "add", "entry", tmp_path)
        assert sha == sha_value
        mock_push_retry.assert_called_once()

    @patch("agent_memory.git_ops.assert_on_default_branch")
    @patch("agent_memory.push_retry.push_with_retry")
    @patch("agent_memory.push_retry._pull_rebase_with_cache_resolution")
    @patch("agent_memory.git_ops.subprocess.run")
    def test_push_retry_failure_propagates(
        self,
        mock_run: MagicMock,
        mock_pull_rebase: MagicMock,
        mock_push_retry: MagicMock,
        mock_assert_branch: MagicMock,
        tmp_path: Path,
    ) -> None:
        """RuntimeError from push_with_retry propagates to caller."""
        file_path = tmp_path / "entry.md"
        sha_value = "d" * 40

        mock_run.side_effect = [
            _make_completed_process(),  # add
            _make_completed_process(),  # commit
            _make_completed_process(stdout=sha_value + "\n"),  # rev-parse
        ]
        mock_pull_rebase.return_value = True
        mock_push_retry.side_effect = RuntimeError("push failed after 3 attempts")

        with pytest.raises(RuntimeError, match="push failed after 3 attempts"):
            commit_and_push(file_path, "agent-1", "add", "entry", tmp_path)

    @patch("agent_memory.git_ops.assert_on_default_branch")
    @patch("agent_memory.push_retry._pull_rebase_with_cache_resolution")
    @patch("agent_memory.git_ops.subprocess.run")
    def test_pull_failure_raises(
        self,
        mock_run: MagicMock,
        mock_pull_rebase: MagicMock,
        mock_assert_branch: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Pull --rebase fails after commit -- raises RuntimeError."""
        file_path = tmp_path / "entry.md"
        sha_value = "e" * 40

        mock_run.side_effect = [
            _make_completed_process(),  # add
            _make_completed_process(),  # commit
            _make_completed_process(stdout=sha_value + "\n"),  # rev-parse
        ]
        mock_pull_rebase.return_value = False

        with pytest.raises(RuntimeError, match="pull --rebase failed"):
            commit_and_push(file_path, "agent-1", "add", "entry", tmp_path)


class TestGitRepoRoot:
    """Tests for git_repo_root which resolves repo root from any path."""

    @patch("agent_memory.git_ops.subprocess.run")
    def test_returns_repo_root_from_directory(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = _make_completed_process(
            stdout="/home/user/my-repo\n"
        )
        result = git_repo_root(tmp_path)
        assert result == Path("/home/user/my-repo")
        mock_run.assert_called_once_with(
            ["git", "-C", str(tmp_path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=30,
        )

    @patch("agent_memory.git_ops.subprocess.run")
    def test_returns_repo_root_from_file(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        file_path = tmp_path / "subdir" / "file.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.touch()
        mock_run.return_value = _make_completed_process(
            stdout="/home/user/my-repo\n"
        )
        result = git_repo_root(file_path)
        assert result == Path("/home/user/my-repo")
        # Should use the parent directory (since file_path is a file, not dir)
        mock_run.assert_called_once_with(
            ["git", "-C", str(file_path.parent), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=30,
        )

    @patch("agent_memory.git_ops.subprocess.run")
    def test_raises_runtime_error_if_not_a_repo(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = _make_completed_process(
            returncode=128, stderr="fatal: not a git repository"
        )
        with pytest.raises(RuntimeError, match="Not a git repository"):
            git_repo_root(tmp_path)


# ---------------------------------------------------------------------------
# Issue #82 -- branch check tests
#
# These tests use real git repositories rather than mocking subprocess so
# the default-branch resolution chain (env > origin/HEAD > "main") and the
# current-branch detection are exercised end-to-end. Real git is fast and
# the chain is too many moving parts to mock cleanly.
# ---------------------------------------------------------------------------


def _init_real_repo(
    repo_dir: Path,
    default_branch: str = "main",
    set_origin_head: bool = True,
) -> Path:
    """Create a real git repo with one commit on a configurable default branch.

    Optionally creates a bare 'remote' alongside it and configures origin/HEAD
    so git_default_branch's symbolic-ref path can be exercised.
    """
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", default_branch, str(repo_dir)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "test@test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.name", "test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "--allow-empty", "-q", "-m", "init"],
        check=True,
        capture_output=True,
    )

    if set_origin_head:
        bare_dir = repo_dir.parent / f"{repo_dir.name}-bare.git"
        subprocess.run(
            ["git", "init", "--bare", "-q", "-b", default_branch, str(bare_dir)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "add", "origin", str(bare_dir)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "push", "-q", "-u", "origin", default_branch],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "set-head", "origin", "--auto"],
            check=True,
            capture_output=True,
        )

    return repo_dir


class TestGitCurrentBranch:
    """Tests for git_current_branch -- thin wrapper over rev-parse."""

    def test_returns_initial_branch_name(self, tmp_path: Path) -> None:
        repo = _init_real_repo(
            tmp_path / "repo", default_branch="main", set_origin_head=False
        )
        assert git_current_branch(repo) == "main"

    def test_returns_master_for_legacy_repo(self, tmp_path: Path) -> None:
        repo = _init_real_repo(
            tmp_path / "repo", default_branch="master", set_origin_head=False
        )
        assert git_current_branch(repo) == "master"

    def test_returns_feature_branch_after_checkout(self, tmp_path: Path) -> None:
        repo = _init_real_repo(
            tmp_path / "repo", default_branch="main", set_origin_head=False
        )
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-q", "-b", "fix/some-feature"],
            check=True,
            capture_output=True,
        )
        assert git_current_branch(repo) == "fix/some-feature"


class TestGitDefaultBranch:
    """Tests for git_default_branch resolution chain (env > origin/HEAD > main)."""

    def test_origin_head_main(self, tmp_path: Path) -> None:
        repo = _init_real_repo(tmp_path / "repo", default_branch="main")
        assert git_default_branch(repo) == "main"

    def test_origin_head_master(self, tmp_path: Path) -> None:
        repo = _init_real_repo(tmp_path / "repo", default_branch="master")
        assert git_default_branch(repo) == "master"

    def test_falls_back_to_main_when_origin_head_unset(
        self, tmp_path: Path
    ) -> None:
        # No remote configured at all -- symbolic-ref will fail.
        repo = _init_real_repo(
            tmp_path / "repo", default_branch="main", set_origin_head=False
        )
        assert git_default_branch(repo) == "main"

    def test_env_var_overrides_origin_head(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # origin/HEAD says "main" but the env var should win.
        repo = _init_real_repo(tmp_path / "repo", default_branch="main")
        monkeypatch.setenv("AGENT_MEMORY_DEFAULT_BRANCH", "production")
        assert git_default_branch(repo) == "production"

    def test_env_var_empty_string_is_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Empty env var should not short-circuit the chain.
        repo = _init_real_repo(tmp_path / "repo", default_branch="master")
        monkeypatch.setenv("AGENT_MEMORY_DEFAULT_BRANCH", "")
        assert git_default_branch(repo) == "master"

    def test_falls_back_when_origin_head_legacy_master(
        self, tmp_path: Path
    ) -> None:
        # Legacy repo with origin/HEAD set to master should resolve to master.
        repo = _init_real_repo(tmp_path / "repo", default_branch="master")
        assert git_default_branch(repo) == "master"


class TestAssertOnDefaultBranch:
    """Tests for assert_on_default_branch -- the issue #82 guard."""

    def test_passes_on_default_branch(self, tmp_path: Path) -> None:
        repo = _init_real_repo(tmp_path / "repo", default_branch="main")
        # Should not raise.
        assert_on_default_branch(repo)

    def test_passes_on_legacy_master_default(self, tmp_path: Path) -> None:
        repo = _init_real_repo(tmp_path / "repo", default_branch="master")
        # Should not raise -- master is the configured default.
        assert_on_default_branch(repo)

    def test_raises_on_feature_branch(self, tmp_path: Path) -> None:
        repo = _init_real_repo(tmp_path / "repo", default_branch="main")
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-q", "-b", "fix/some-feature"],
            check=True,
            capture_output=True,
        )
        with pytest.raises(BranchMismatchError) as exc_info:
            assert_on_default_branch(repo)

        err = exc_info.value
        assert err.current_branch == "fix/some-feature"
        assert err.default_branch == "main"
        assert err.repo_path == repo

    def test_error_message_contains_recovery_command(
        self, tmp_path: Path
    ) -> None:
        repo = _init_real_repo(tmp_path / "repo", default_branch="main")
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-q", "-b", "fix/some-feature"],
            check=True,
            capture_output=True,
        )
        with pytest.raises(BranchMismatchError) as exc_info:
            assert_on_default_branch(repo)

        message = str(exc_info.value)
        # Branch names
        assert "fix/some-feature" in message
        assert "main" in message
        # Recovery command
        assert f"git -C {repo} checkout main" in message
        # Override hint
        assert "--allow-non-main-branch" in message

    def test_error_message_contains_persistent_default_hints(
        self, tmp_path: Path
    ) -> None:
        """The error message must guide operators on legacy or fork branches.

        Two persistent fixes for the case where the current branch IS the
        operator's genuine default (a fork using a custom name, or a legacy
        repo without origin/HEAD set):
            1. `git remote set-head origin --auto`
            2. `export AGENT_MEMORY_DEFAULT_BRANCH=<branch>`

        Without these hints, an operator on such a fork hits the override
        flag repeatedly when they should be configuring the CLI once.
        """
        repo = _init_real_repo(tmp_path / "repo", default_branch="main")
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-q", "-b", "production"],
            check=True,
            capture_output=True,
        )
        with pytest.raises(BranchMismatchError) as exc_info:
            assert_on_default_branch(repo)

        message = str(exc_info.value)
        assert "remote set-head origin --auto" in message
        assert "AGENT_MEMORY_DEFAULT_BRANCH=production" in message

    def test_error_message_uses_legacy_master_default(
        self, tmp_path: Path
    ) -> None:
        repo = _init_real_repo(tmp_path / "repo", default_branch="master")
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-q", "-b", "fix/some-feature"],
            check=True,
            capture_output=True,
        )
        with pytest.raises(BranchMismatchError) as exc_info:
            assert_on_default_branch(repo)

        message = str(exc_info.value)
        assert f"git -C {repo} checkout master" in message
        assert "expected branch: 'master'" in message

    def test_allow_non_default_skips_check(self, tmp_path: Path) -> None:
        repo = _init_real_repo(tmp_path / "repo", default_branch="main")
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-q", "-b", "fix/some-feature"],
            check=True,
            capture_output=True,
        )
        # Should NOT raise when override is set.
        assert_on_default_branch(repo, allow_non_default=True)

    def test_env_var_default_makes_legacy_repo_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # If a custom default is configured via env, the check honors it.
        repo = _init_real_repo(
            tmp_path / "repo", default_branch="production", set_origin_head=False
        )
        monkeypatch.setenv("AGENT_MEMORY_DEFAULT_BRANCH", "production")
        assert_on_default_branch(repo)


class TestCommitAndPushBranchGuard:
    """End-to-end tests proving commit_and_push enforces the branch check."""

    def test_refuses_to_commit_on_feature_branch(self, tmp_path: Path) -> None:
        repo = _init_real_repo(tmp_path / "repo", default_branch="main")
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-q", "-b", "fix/some-feature"],
            check=True,
            capture_output=True,
        )
        entry_file = repo / "entry.md"
        entry_file.write_text("---\ndescription: test\n---\n# test\n")

        with pytest.raises(BranchMismatchError) as exc_info:
            commit_and_push(entry_file, "test-agent", "add", "entry", repo)

        # The commit must NOT have happened -- HEAD should still point at
        # the same commit it was on before the call.
        result = subprocess.run(
            ["git", "-C", str(repo), "log", "--oneline"],
            check=True,
            capture_output=True,
            text=True,
        )
        # Only the initial commit should exist.
        assert len(result.stdout.strip().split("\n")) == 1
        assert "fix/some-feature" in str(exc_info.value)

    def test_allow_non_default_branch_lets_commit_through(
        self, tmp_path: Path
    ) -> None:
        repo = _init_real_repo(tmp_path / "repo", default_branch="main")
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-q", "-b", "feat/intentional"],
            check=True,
            capture_output=True,
        )
        entry_file = repo / "entry.md"
        entry_file.write_text("---\ndescription: test\n---\n# test\n")

        # Mock out the network-dependent push step but let the commit happen.
        with patch("agent_memory.push_retry.push_with_retry"), patch(
            "agent_memory.push_retry._pull_rebase_with_cache_resolution"
        ) as mock_pull:
            mock_pull.return_value = True
            sha = commit_and_push(
                entry_file,
                "test-agent",
                "add",
                "entry",
                repo,
                allow_non_default_branch=True,
            )

        assert sha  # SHA was returned -- commit succeeded
        # Two commits now exist on the feature branch.
        result = subprocess.run(
            ["git", "-C", str(repo), "log", "--oneline"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert len(result.stdout.strip().split("\n")) == 2

    def test_commit_succeeds_on_default_branch_without_override(
        self, tmp_path: Path
    ) -> None:
        repo = _init_real_repo(tmp_path / "repo", default_branch="main")
        entry_file = repo / "entry.md"
        entry_file.write_text("---\ndescription: test\n---\n# test\n")

        with patch("agent_memory.push_retry.push_with_retry"), patch(
            "agent_memory.push_retry._pull_rebase_with_cache_resolution"
        ) as mock_pull:
            mock_pull.return_value = True
            sha = commit_and_push(
                entry_file, "test-agent", "add", "entry", repo
            )

        assert sha
        result = subprocess.run(
            ["git", "-C", str(repo), "log", "--oneline"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert len(result.stdout.strip().split("\n")) == 2
