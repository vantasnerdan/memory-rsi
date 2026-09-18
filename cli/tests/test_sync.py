"""Tests for clone and sync operations (sync.py and CLI commands)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from agent_memory.cli import cli
from agent_memory.git_ops import (
    _run_git_raw,
    git_add_all,
    git_clone,
    git_diff_names,
    git_has_uncommitted,
    git_is_repo,
    git_pull_merge,
    git_remote_url,
    git_rev_parse_head,
)
from agent_memory.sync import (
    CloneResult,
    FileChange,
    SyncResult,
    _collect_changes,
    _parse_diff_status,
    _read_description,
    _validate_changed_files,
    clone_repo,
    sync_repo,
)


def _make_completed(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


# === git_ops new functions ===


class TestRunGitRaw:
    @patch("agent_memory.git_ops.subprocess.run")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _make_completed(stdout="done\n")
        result = _run_git_raw(["clone", "url", "/tmp/x"], "clone failed")
        assert result.returncode == 0
        mock_run.assert_called_once_with(
            ["git", "clone", "url", "/tmp/x"],
            capture_output=True,
            text=True,
            timeout=120,
        )

    @patch("agent_memory.git_ops.subprocess.run")
    def test_failure_raises(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _make_completed(
            returncode=128, stderr="fatal: repo not found"
        )
        with pytest.raises(RuntimeError, match="clone failed.*repo not found"):
            _run_git_raw(["clone", "url", "/tmp/x"], "clone failed")

    @patch("agent_memory.git_ops.subprocess.run")
    def test_timeout_raises(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=120)
        with pytest.raises(RuntimeError, match="timed out after 120s"):
            _run_git_raw(["clone", "url", "/tmp/x"], "clone failed")


class TestGitClone:
    @patch("agent_memory.git_ops.subprocess.run")
    def test_clone_args(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _make_completed()
        git_clone("https://github.com/org/repo.git", Path("/tmp/repo"))
        mock_run.assert_called_once_with(
            ["git", "clone", "https://github.com/org/repo.git", "/tmp/repo"],
            capture_output=True,
            text=True,
            timeout=120,
        )


class TestGitIsRepo:
    @patch("agent_memory.git_ops.subprocess.run")
    def test_is_repo(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _make_completed(stdout="true\n")
        assert git_is_repo(tmp_path) is True

    def test_nonexistent_path(self) -> None:
        assert git_is_repo(Path("/nonexistent/path")) is False

    @patch("agent_memory.git_ops.subprocess.run")
    def test_not_a_repo(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _make_completed(
            returncode=128, stderr="not a git repo"
        )
        assert git_is_repo(tmp_path) is False


class TestGitRemoteUrl:
    @patch("agent_memory.git_ops.subprocess.run")
    def test_returns_url(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _make_completed(
            stdout="https://github.com/org/repo.git\n"
        )
        assert git_remote_url(tmp_path) == "https://github.com/org/repo.git"

    @patch("agent_memory.git_ops.subprocess.run")
    def test_no_remote_returns_empty(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = _make_completed(
            returncode=2, stderr="No such remote"
        )
        assert git_remote_url(tmp_path) == ""


class TestGitPullMerge:
    @patch("agent_memory.git_ops.subprocess.run")
    def test_no_rebase_flag(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _make_completed(stdout="Already up to date.\n")
        git_pull_merge(tmp_path)
        mock_run.assert_called_once_with(
            ["git", "-C", str(tmp_path), "pull", "--no-rebase"],
            capture_output=True,
            text=True,
            timeout=30,
        )


class TestGitDiffNames:
    @patch("agent_memory.git_ops.subprocess.run")
    def test_parses_output(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _make_completed(
            stdout="A\tnew.md\nM\tupdated.md\nD\tdeleted.md\n"
        )
        result = git_diff_names(tmp_path, "abc123", "def456")
        assert len(result) == 3
        assert "A\tnew.md" in result

    @patch("agent_memory.git_ops.subprocess.run")
    def test_empty_diff(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _make_completed(stdout="")
        result = git_diff_names(tmp_path, "abc", "abc")
        assert result == []


class TestGitRevParseHead:
    @patch("agent_memory.git_ops.subprocess.run")
    def test_returns_sha(self, mock_run: MagicMock, tmp_path: Path) -> None:
        sha = "a" * 40
        mock_run.return_value = _make_completed(stdout=sha + "\n")
        assert git_rev_parse_head(tmp_path) == sha

    @patch("agent_memory.git_ops.subprocess.run")
    def test_empty_raises(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _make_completed(stdout="")
        with pytest.raises(RuntimeError, match="empty output"):
            git_rev_parse_head(tmp_path)


class TestGitHasUncommitted:
    @patch("agent_memory.git_ops.subprocess.run")
    def test_clean(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _make_completed(stdout="")
        assert git_has_uncommitted(tmp_path) is False

    @patch("agent_memory.git_ops.subprocess.run")
    def test_dirty(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _make_completed(stdout="M  file.md\n")
        assert git_has_uncommitted(tmp_path) is True


class TestGitAddAll:
    @patch("agent_memory.git_ops.subprocess.run")
    def test_add_all_args(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _make_completed()
        git_add_all(tmp_path)
        mock_run.assert_called_once_with(
            ["git", "-C", str(tmp_path), "add", "-A"],
            capture_output=True,
            text=True,
            timeout=30,
        )


# === sync.py unit tests ===


class TestParseDiffStatus:
    def test_all_statuses(self) -> None:
        assert _parse_diff_status("A") == "added"
        assert _parse_diff_status("M") == "modified"
        assert _parse_diff_status("D") == "deleted"
        assert _parse_diff_status("R") == "renamed"
        assert _parse_diff_status("C") == "copied"
        assert _parse_diff_status("T") == "type-changed"

    def test_unknown(self) -> None:
        assert _parse_diff_status("X") == "unknown"


class TestReadDescription:
    def test_reads_valid_entry(self, tmp_path: Path) -> None:
        md = tmp_path / "entry.md"
        md.write_text(
            "---\ndescription: Test entry\nauthor: agent\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n"
        )
        assert _read_description(tmp_path, "entry.md") == "Test entry"

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        assert _read_description(tmp_path, "missing.md") == ""

    def test_non_markdown(self, tmp_path: Path) -> None:
        txt = tmp_path / "notes.txt"
        txt.write_text("hello")
        assert _read_description(tmp_path, "notes.txt") == ""

    def test_bad_frontmatter(self, tmp_path: Path) -> None:
        md = tmp_path / "bad.md"
        md.write_text("no frontmatter here")
        assert _read_description(tmp_path, "bad.md") == ""


class TestCollectChanges:
    def test_filters_non_markdown(self, tmp_path: Path) -> None:
        diff_lines = ["A\tfile.txt", "A\tentry.md"]
        # Create the md file with frontmatter
        md = tmp_path / "entry.md"
        md.write_text(
            "---\ndescription: New entry\nauthor: a\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n"
        )
        changes = _collect_changes(tmp_path, diff_lines)
        assert len(changes) == 1
        assert changes[0].path == "entry.md"
        assert changes[0].description == "New entry"

    def test_deleted_has_no_description(self, tmp_path: Path) -> None:
        diff_lines = ["D\tremoved.md"]
        changes = _collect_changes(tmp_path, diff_lines)
        assert len(changes) == 1
        assert changes[0].status == "deleted"
        assert changes[0].description == ""

    def test_handles_rename_status(self, tmp_path: Path) -> None:
        # R100 format: STATUS\told-path\tnew-path -- use new path
        md = tmp_path / "new.md"
        md.write_text(
            "---\ndescription: Renamed entry\nauthor: a\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n"
        )
        diff_lines = ["R100\told.md\tnew.md"]
        changes = _collect_changes(tmp_path, diff_lines)
        assert len(changes) == 1
        assert changes[0].status == "renamed"
        assert changes[0].path == "new.md"
        assert changes[0].description == "Renamed entry"

    def test_empty_lines_skipped(self, tmp_path: Path) -> None:
        diff_lines = ["", "  ", "A\tentry.md"]
        md = tmp_path / "entry.md"
        md.write_text(
            "---\ndescription: X\nauthor: a\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n"
        )
        changes = _collect_changes(tmp_path, diff_lines)
        assert len(changes) == 1


class TestValidateChangedFiles:
    def test_validates_added_files(self, tmp_path: Path) -> None:
        md = tmp_path / "good.md"
        md.write_text(
            "---\ndescription: Good entry\nauthor: agent\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n"
        )
        changes = [FileChange(status="added", path="good.md")]
        warnings = _validate_changed_files(tmp_path, changes)
        # Should have optional field warnings but no errors
        assert any("Optional" in w for w in warnings)

    def test_skips_deleted_files(self, tmp_path: Path) -> None:
        changes = [FileChange(status="deleted", path="gone.md")]
        warnings = _validate_changed_files(tmp_path, changes)
        assert warnings == []

    def test_skips_nonexistent(self, tmp_path: Path) -> None:
        changes = [FileChange(status="modified", path="missing.md")]
        warnings = _validate_changed_files(tmp_path, changes)
        assert warnings == []


# === clone_repo tests ===


class TestCloneRepo:
    @patch("agent_memory.sync.git_clone")
    def test_fresh_clone(self, mock_clone: MagicMock, tmp_path: Path) -> None:
        target = tmp_path / "new-clone"
        result = clone_repo("https://github.com/org/repo.git", target)
        assert result.already_existed is False
        assert result.local_path == str(target)
        mock_clone.assert_called_once_with(
            "https://github.com/org/repo.git", target
        )

    @patch("agent_memory.sync.git_remote_url")
    @patch("agent_memory.sync.git_is_repo")
    def test_already_cloned_same_remote(
        self, mock_is_repo: MagicMock, mock_url: MagicMock, tmp_path: Path
    ) -> None:
        mock_is_repo.return_value = True
        mock_url.return_value = "https://github.com/org/repo.git"
        result = clone_repo("https://github.com/org/repo.git", tmp_path)
        assert result.already_existed is True
        assert "already cloned" in result.message.lower()

    @patch("agent_memory.sync.git_remote_url")
    @patch("agent_memory.sync.git_is_repo")
    def test_different_remote_raises(
        self, mock_is_repo: MagicMock, mock_url: MagicMock, tmp_path: Path
    ) -> None:
        mock_is_repo.return_value = True
        mock_url.return_value = "https://github.com/other/repo.git"
        with pytest.raises(RuntimeError, match="different remote"):
            clone_repo("https://github.com/org/repo.git", tmp_path)

    @patch("agent_memory.sync.git_is_repo")
    def test_exists_not_git_raises(
        self, mock_is_repo: MagicMock, tmp_path: Path
    ) -> None:
        mock_is_repo.return_value = False
        with pytest.raises(RuntimeError, match="not a git repository"):
            clone_repo("https://github.com/org/repo.git", tmp_path)


# === sync_repo tests ===


class TestSyncRepo:
    @patch("agent_memory.sync.git_rev_parse_head")
    @patch("agent_memory.sync.git_pull_merge")
    @patch("agent_memory.sync.git_has_uncommitted")
    @patch("agent_memory.sync.git_is_repo")
    def test_clean_no_changes(
        self,
        mock_is_repo: MagicMock,
        mock_uncommitted: MagicMock,
        mock_pull: MagicMock,
        mock_head: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_is_repo.return_value = True
        mock_uncommitted.return_value = False
        mock_head.return_value = "a" * 40
        mock_pull.return_value = ""

        result = sync_repo(tmp_path, "test-agent")
        assert result.pulled is True
        assert result.pushed is False
        assert result.message == "Already up to date."
        assert result.changes == []

    def test_not_a_repo_raises(self, tmp_path: Path) -> None:
        # tmp_path exists but is not a git repo
        with patch("agent_memory.sync.git_is_repo", return_value=False):
            with pytest.raises(RuntimeError, match="Not a git repository"):
                sync_repo(tmp_path, "agent")

    @patch("agent_memory.sync._validate_changed_files")
    @patch("agent_memory.sync._collect_changes")
    @patch("agent_memory.sync.git_diff_names")
    @patch("agent_memory.sync.git_rev_parse_head")
    @patch("agent_memory.sync.git_pull_merge")
    @patch("agent_memory.sync.git_has_uncommitted")
    @patch("agent_memory.sync.git_is_repo")
    def test_pull_with_changes(
        self,
        mock_is_repo: MagicMock,
        mock_uncommitted: MagicMock,
        mock_pull: MagicMock,
        mock_head: MagicMock,
        mock_diff: MagicMock,
        mock_collect: MagicMock,
        mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_is_repo.return_value = True
        mock_uncommitted.return_value = False
        mock_head.side_effect = ["a" * 40, "b" * 40]
        mock_pull.return_value = ""
        mock_diff.return_value = ["A\tnew-entry.md"]
        mock_collect.return_value = [
            FileChange(status="added", path="new-entry.md", description="New")
        ]
        mock_validate.return_value = []

        result = sync_repo(tmp_path, "agent")
        assert result.pulled is True
        assert len(result.changes) == 1
        assert result.changes[0].status == "added"
        assert "1 added" in result.message

    @patch("agent_memory.sync.assert_on_default_branch")
    @patch("agent_memory.sync.git_push")
    @patch("agent_memory.sync.git_commit")
    @patch("agent_memory.sync.git_add_all")
    @patch("agent_memory.sync.git_rev_parse_head")
    @patch("agent_memory.sync.git_pull_merge")
    @patch("agent_memory.sync.git_has_uncommitted")
    @patch("agent_memory.sync.git_is_repo")
    def test_push_uncommitted_changes(
        self,
        mock_is_repo: MagicMock,
        mock_uncommitted: MagicMock,
        mock_pull: MagicMock,
        mock_head: MagicMock,
        mock_add: MagicMock,
        mock_commit: MagicMock,
        mock_push: MagicMock,
        mock_assert_branch: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_is_repo.return_value = True
        mock_uncommitted.return_value = True
        mock_head.return_value = "a" * 40
        mock_pull.return_value = ""
        mock_commit.return_value = "b" * 40

        result = sync_repo(tmp_path, "my-agent")
        assert result.pushed is True
        mock_add.assert_called_once_with(tmp_path)
        mock_commit.assert_called_once_with(
            "memory(my-agent): sync uncommitted changes", tmp_path
        )
        mock_push.assert_called_once_with(tmp_path)
        mock_assert_branch.assert_called_once_with(
            tmp_path, allow_non_default=False
        )

    @patch("agent_memory.sync.git_rev_parse_head")
    @patch("agent_memory.sync.git_pull_merge")
    @patch("agent_memory.sync.git_has_uncommitted")
    @patch("agent_memory.sync.git_is_repo")
    def test_conflict_raises_clear_message(
        self,
        mock_is_repo: MagicMock,
        mock_uncommitted: MagicMock,
        mock_pull: MagicMock,
        mock_head: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_is_repo.return_value = True
        mock_uncommitted.return_value = False
        mock_head.return_value = "a" * 40
        mock_pull.side_effect = RuntimeError(
            "git pull --no-rebase failed: CONFLICT in shared/decisions.md"
        )

        with pytest.raises(RuntimeError, match="Merge conflict during sync"):
            sync_repo(tmp_path, "agent")

    @patch("agent_memory.sync.git_rev_parse_head")
    @patch("agent_memory.sync.git_pull_merge")
    @patch("agent_memory.sync.git_has_uncommitted")
    @patch("agent_memory.sync.git_is_repo")
    def test_pull_only_skips_push(
        self,
        mock_is_repo: MagicMock,
        mock_uncommitted: MagicMock,
        mock_pull: MagicMock,
        mock_head: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_is_repo.return_value = True
        mock_uncommitted.return_value = True  # has changes
        mock_head.return_value = "a" * 40
        mock_pull.return_value = ""

        result = sync_repo(tmp_path, "agent", pull_only=True)
        assert result.pulled is True
        assert result.pushed is False

    @patch("agent_memory.sync.assert_on_default_branch")
    @patch("agent_memory.sync.git_push")
    @patch("agent_memory.sync.git_commit")
    @patch("agent_memory.sync.git_add_all")
    @patch("agent_memory.sync.git_rev_parse_head")
    @patch("agent_memory.sync.git_has_uncommitted")
    @patch("agent_memory.sync.git_is_repo")
    def test_push_only_skips_pull(
        self,
        mock_is_repo: MagicMock,
        mock_uncommitted: MagicMock,
        mock_head: MagicMock,
        mock_add: MagicMock,
        mock_commit: MagicMock,
        mock_push: MagicMock,
        mock_assert_branch: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_is_repo.return_value = True
        mock_uncommitted.return_value = True
        mock_head.return_value = "a" * 40
        mock_commit.return_value = "b" * 40

        result = sync_repo(tmp_path, "agent", push_only=True)
        assert result.pulled is False
        assert result.pushed is True
        mock_push.assert_called_once()
        mock_assert_branch.assert_called_once_with(
            tmp_path, allow_non_default=False
        )

    @patch("agent_memory.sync._validate_changed_files")
    @patch("agent_memory.sync._collect_changes")
    @patch("agent_memory.sync.git_diff_names")
    @patch("agent_memory.sync.git_rev_parse_head")
    @patch("agent_memory.sync.git_pull_merge")
    @patch("agent_memory.sync.git_has_uncommitted")
    @patch("agent_memory.sync.git_is_repo")
    def test_validation_warnings_non_blocking(
        self,
        mock_is_repo: MagicMock,
        mock_uncommitted: MagicMock,
        mock_pull: MagicMock,
        mock_head: MagicMock,
        mock_diff: MagicMock,
        mock_collect: MagicMock,
        mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_is_repo.return_value = True
        mock_uncommitted.return_value = False
        mock_head.side_effect = ["a" * 40, "b" * 40]
        mock_pull.return_value = ""
        mock_diff.return_value = ["M\tentry.md"]
        mock_collect.return_value = [
            FileChange(status="modified", path="entry.md")
        ]
        mock_validate.return_value = [
            "entry.md: Optional field missing: tags"
        ]

        result = sync_repo(tmp_path, "agent")
        # Validation warnings should be present but not cause failure
        assert len(result.validation_warnings) == 1
        assert "tags" in result.validation_warnings[0]

    @patch("agent_memory.sync._validate_changed_files")
    @patch("agent_memory.sync._collect_changes")
    @patch("agent_memory.sync.git_diff_names")
    @patch("agent_memory.sync.git_rev_parse_head")
    @patch("agent_memory.sync.git_pull_merge")
    @patch("agent_memory.sync.git_has_uncommitted")
    @patch("agent_memory.sync.git_is_repo")
    def test_message_counts_multiple_types(
        self,
        mock_is_repo: MagicMock,
        mock_uncommitted: MagicMock,
        mock_pull: MagicMock,
        mock_head: MagicMock,
        mock_diff: MagicMock,
        mock_collect: MagicMock,
        mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_is_repo.return_value = True
        mock_uncommitted.return_value = False
        mock_head.side_effect = ["a" * 40, "b" * 40]
        mock_pull.return_value = ""
        mock_diff.return_value = []
        mock_collect.return_value = [
            FileChange(status="added", path="new.md"),
            FileChange(status="added", path="new2.md"),
            FileChange(status="modified", path="old.md"),
            FileChange(status="deleted", path="gone.md"),
        ]
        mock_validate.return_value = []

        result = sync_repo(tmp_path, "agent")
        assert "2 added" in result.message
        assert "1 modified" in result.message
        assert "1 deleted" in result.message


class TestSyncRepoBranchGuard:
    """Tests for the issue #82 branch guard in sync_repo.

    The guard only fires when sync_repo would actually create a commit:
    uncommitted changes exist AND pull_only is False. Pull-only sync is
    read-only and is exempt.
    """

    @patch("agent_memory.sync.assert_on_default_branch")
    @patch("agent_memory.sync.git_push")
    @patch("agent_memory.sync.git_commit")
    @patch("agent_memory.sync.git_add_all")
    @patch("agent_memory.sync.git_rev_parse_head")
    @patch("agent_memory.sync.git_pull_merge")
    @patch("agent_memory.sync.git_has_uncommitted")
    @patch("agent_memory.sync.git_is_repo")
    def test_branch_guard_called_when_committing(
        self,
        mock_is_repo: MagicMock,
        mock_uncommitted: MagicMock,
        mock_pull: MagicMock,
        mock_head: MagicMock,
        mock_add: MagicMock,
        mock_commit: MagicMock,
        mock_push: MagicMock,
        mock_assert_branch: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_is_repo.return_value = True
        mock_uncommitted.return_value = True
        mock_head.return_value = "a" * 40
        mock_pull.return_value = ""
        mock_commit.return_value = "b" * 40

        sync_repo(tmp_path, "agent")
        mock_assert_branch.assert_called_once_with(
            tmp_path, allow_non_default=False
        )

    @patch("agent_memory.sync.assert_on_default_branch")
    @patch("agent_memory.sync.git_rev_parse_head")
    @patch("agent_memory.sync.git_pull_merge")
    @patch("agent_memory.sync.git_has_uncommitted")
    @patch("agent_memory.sync.git_is_repo")
    def test_branch_guard_skipped_when_no_uncommitted(
        self,
        mock_is_repo: MagicMock,
        mock_uncommitted: MagicMock,
        mock_pull: MagicMock,
        mock_head: MagicMock,
        mock_assert_branch: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_is_repo.return_value = True
        mock_uncommitted.return_value = False
        mock_head.return_value = "a" * 40
        mock_pull.return_value = ""

        sync_repo(tmp_path, "agent")
        mock_assert_branch.assert_not_called()

    @patch("agent_memory.sync.assert_on_default_branch")
    @patch("agent_memory.sync.git_rev_parse_head")
    @patch("agent_memory.sync.git_pull_merge")
    @patch("agent_memory.sync.git_has_uncommitted")
    @patch("agent_memory.sync.git_is_repo")
    def test_branch_guard_skipped_in_pull_only_mode(
        self,
        mock_is_repo: MagicMock,
        mock_uncommitted: MagicMock,
        mock_pull: MagicMock,
        mock_head: MagicMock,
        mock_assert_branch: MagicMock,
        tmp_path: Path,
    ) -> None:
        # Even with uncommitted changes, pull_only does not commit so the
        # branch guard should not fire.
        mock_is_repo.return_value = True
        mock_uncommitted.return_value = True
        mock_head.return_value = "a" * 40
        mock_pull.return_value = ""

        sync_repo(tmp_path, "agent", pull_only=True)
        mock_assert_branch.assert_not_called()

    @patch("agent_memory.sync.assert_on_default_branch")
    @patch("agent_memory.sync.git_push")
    @patch("agent_memory.sync.git_commit")
    @patch("agent_memory.sync.git_add_all")
    @patch("agent_memory.sync.git_rev_parse_head")
    @patch("agent_memory.sync.git_pull_merge")
    @patch("agent_memory.sync.git_has_uncommitted")
    @patch("agent_memory.sync.git_is_repo")
    def test_allow_non_default_branch_passed_through(
        self,
        mock_is_repo: MagicMock,
        mock_uncommitted: MagicMock,
        mock_pull: MagicMock,
        mock_head: MagicMock,
        mock_add: MagicMock,
        mock_commit: MagicMock,
        mock_push: MagicMock,
        mock_assert_branch: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_is_repo.return_value = True
        mock_uncommitted.return_value = True
        mock_head.return_value = "a" * 40
        mock_pull.return_value = ""
        mock_commit.return_value = "b" * 40

        sync_repo(tmp_path, "agent", allow_non_default_branch=True)
        mock_assert_branch.assert_called_once_with(
            tmp_path, allow_non_default=True
        )

    @patch("agent_memory.sync.git_push")
    @patch("agent_memory.sync.git_commit")
    @patch("agent_memory.sync.git_add_all")
    @patch("agent_memory.sync.git_rev_parse_head")
    @patch("agent_memory.sync.git_pull_merge")
    @patch("agent_memory.sync.git_has_uncommitted")
    @patch("agent_memory.sync.git_is_repo")
    @patch("agent_memory.sync.assert_on_default_branch")
    def test_branch_guard_failure_aborts_before_commit(
        self,
        mock_assert_branch: MagicMock,
        mock_is_repo: MagicMock,
        mock_uncommitted: MagicMock,
        mock_pull: MagicMock,
        mock_head: MagicMock,
        mock_add: MagicMock,
        mock_commit: MagicMock,
        mock_push: MagicMock,
        tmp_path: Path,
    ) -> None:
        from agent_memory.git_ops import BranchMismatchError

        mock_is_repo.return_value = True
        mock_uncommitted.return_value = True
        mock_head.return_value = "a" * 40
        mock_assert_branch.side_effect = BranchMismatchError(
            current_branch="fix/x",
            default_branch="main",
            repo_path=tmp_path,
        )

        with pytest.raises(BranchMismatchError):
            sync_repo(tmp_path, "agent")

        # No commit, no add, no push happened.
        mock_add.assert_not_called()
        mock_commit.assert_not_called()
        mock_push.assert_not_called()


# === CLI tests ===


class TestCloneCLI:
    def test_missing_repo_env(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["clone"], env={"AGENT_MEMORY_PATH": "/tmp/x"})
        assert result.exit_code == 1
        assert "AGENT_MEMORY_REPO" in result.output

    def test_missing_path_env(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["clone"],
            env={"AGENT_MEMORY_REPO": "https://github.com/org/repo.git"},
        )
        assert result.exit_code == 1
        assert "AGENT_MEMORY_PATH" in result.output

    @patch("agent_memory.sync.clone_repo")
    def test_successful_clone(self, mock_clone: MagicMock) -> None:
        mock_clone.return_value = CloneResult(
            already_existed=False,
            local_path="/tmp/mem",
            remote_url="https://github.com/org/repo.git",
            message="Cloned https://github.com/org/repo.git to /tmp/mem",
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["clone"],
            env={
                "AGENT_MEMORY_REPO": "https://github.com/org/repo.git",
                "AGENT_MEMORY_PATH": "/tmp/mem",
            },
        )
        assert result.exit_code == 0
        assert "Cloned" in result.output

    @patch("agent_memory.sync.clone_repo")
    def test_already_cloned(self, mock_clone: MagicMock) -> None:
        mock_clone.return_value = CloneResult(
            already_existed=True,
            local_path="/tmp/mem",
            remote_url="https://github.com/org/repo.git",
            message="Repository already cloned at /tmp/mem",
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["clone"],
            env={
                "AGENT_MEMORY_REPO": "https://github.com/org/repo.git",
                "AGENT_MEMORY_PATH": "/tmp/mem",
            },
        )
        assert result.exit_code == 0
        assert "already cloned" in result.output.lower()

    @patch("agent_memory.sync.clone_repo")
    def test_clone_json_output(self, mock_clone: MagicMock) -> None:
        mock_clone.return_value = CloneResult(
            already_existed=False,
            local_path="/tmp/mem",
            remote_url="https://github.com/org/repo.git",
            message="Cloned",
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "clone"],
            env={
                "AGENT_MEMORY_REPO": "https://github.com/org/repo.git",
                "AGENT_MEMORY_PATH": "/tmp/mem",
            },
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["already_existed"] is False
        assert data["local_path"] == "/tmp/mem"
        assert data["remote_url"] == "https://github.com/org/repo.git"

    @patch("agent_memory.sync.clone_repo")
    def test_clone_failure(self, mock_clone: MagicMock) -> None:
        mock_clone.side_effect = RuntimeError("Authentication failed")
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["clone"],
            env={
                "AGENT_MEMORY_REPO": "https://github.com/org/repo.git",
                "AGENT_MEMORY_PATH": "/tmp/mem",
            },
        )
        assert result.exit_code == 1
        assert "Authentication failed" in result.output


class TestSyncCLI:
    def test_missing_agent_id(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["sync"],
            env={"AGENT_MEMORY_PATH": "/tmp/mem"},
        )
        assert result.exit_code == 1
        assert "AGENT_ID" in result.output

    def test_missing_path_env(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["sync"],
            env={"AGENT_ID": "test-agent"},
        )
        assert result.exit_code == 1
        assert "AGENT_MEMORY_PATH" in result.output

    def test_pull_and_push_only_conflict(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["sync", "--pull-only", "--push-only"],
            env={
                "AGENT_ID": "agent",
                "AGENT_MEMORY_PATH": "/tmp/mem",
            },
        )
        assert result.exit_code == 1
        assert "Cannot use" in result.output

    @patch("agent_memory.sync.sync_repo")
    def test_successful_sync_no_changes(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = SyncResult(
            pulled=True,
            pushed=False,
            pre_sync_sha="a" * 40,
            post_sync_sha="a" * 40,
            message="Already up to date.",
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["sync"],
            env={
                "AGENT_ID": "test-agent",
                "AGENT_MEMORY_PATH": "/tmp/mem",
            },
        )
        assert result.exit_code == 0
        assert "Already up to date" in result.output

    @patch("agent_memory.sync.sync_repo")
    def test_sync_with_changes(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = SyncResult(
            pulled=True,
            pushed=False,
            pre_sync_sha="a" * 40,
            post_sync_sha="b" * 40,
            message="Synced: 2 added, 1 modified.",
            changes=[
                FileChange(
                    status="added",
                    path="memory/agent/atlas/new.md",
                    description="New entry",
                ),
                FileChange(
                    status="added",
                    path="memory/agent/atlas/other.md",
                    description="Other",
                ),
                FileChange(
                    status="modified",
                    path="memory/shared/decisions.md",
                    description="Team decisions",
                ),
            ],
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["sync"],
            env={
                "AGENT_ID": "test-agent",
                "AGENT_MEMORY_PATH": "/tmp/mem",
            },
        )
        assert result.exit_code == 0
        assert "2 added" in result.output
        assert "1 modified" in result.output
        assert "New entry" in result.output
        assert "[added]" in result.output
        assert "[modified]" in result.output

    @patch("agent_memory.sync.sync_repo")
    def test_sync_with_validation_warnings(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = SyncResult(
            pulled=True,
            pushed=False,
            pre_sync_sha="a" * 40,
            post_sync_sha="b" * 40,
            message="Synced: 1 added.",
            changes=[
                FileChange(status="added", path="entry.md"),
            ],
            validation_warnings=["entry.md: Optional field missing: tags"],
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["sync"],
            env={
                "AGENT_ID": "test-agent",
                "AGENT_MEMORY_PATH": "/tmp/mem",
            },
        )
        assert result.exit_code == 0
        assert "Validation warnings" in result.output
        assert "[warn]" in result.output

    @patch("agent_memory.sync.sync_repo")
    def test_sync_json_output(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = SyncResult(
            pulled=True,
            pushed=True,
            pre_sync_sha="a" * 40,
            post_sync_sha="b" * 40,
            message="Synced: 1 added.",
            changes=[
                FileChange(
                    status="added",
                    path="memory/agent/atlas/new.md",
                    description="A new entry",
                ),
            ],
            validation_warnings=[
                "memory/agent/atlas/new.md: Optional field missing: tags"
            ],
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "sync"],
            env={
                "AGENT_ID": "test-agent",
                "AGENT_MEMORY_PATH": "/tmp/mem",
            },
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["pulled"] is True
        assert data["pushed"] is True
        assert len(data["changes"]) == 1
        assert data["changes"][0]["status"] == "added"
        assert data["changes"][0]["description"] == "A new entry"
        assert len(data["validation_warnings"]) == 1

    @patch("agent_memory.sync.sync_repo")
    def test_sync_failure(self, mock_sync: MagicMock) -> None:
        mock_sync.side_effect = RuntimeError("Not a git repository")
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["sync"],
            env={
                "AGENT_ID": "test-agent",
                "AGENT_MEMORY_PATH": "/tmp/mem",
            },
        )
        assert result.exit_code == 1
        assert "Not a git repository" in result.output

    @patch("agent_memory.sync.sync_repo")
    def test_sync_passes_pull_only(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = SyncResult(
            pulled=True, pushed=False, message="Already up to date."
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["sync", "--pull-only"],
            env={
                "AGENT_ID": "test-agent",
                "AGENT_MEMORY_PATH": "/tmp/mem",
            },
        )
        assert result.exit_code == 0
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args
        assert call_kwargs.kwargs.get("pull_only") is True

    @patch("agent_memory.sync.sync_repo")
    def test_sync_passes_push_only(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = SyncResult(
            pulled=False, pushed=True, message="Pushed."
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["sync", "--push-only"],
            env={
                "AGENT_ID": "test-agent",
                "AGENT_MEMORY_PATH": "/tmp/mem",
            },
        )
        assert result.exit_code == 0
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args
        assert call_kwargs.kwargs.get("push_only") is True
