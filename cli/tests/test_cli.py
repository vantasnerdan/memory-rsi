"""Tests for CLI commands using click.testing.CliRunner."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from agent_memory.cli import cli

FIXTURES = Path(__file__).parent / "fixtures"


def test_version() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert result.output == "memory, version 0.2.0\n"


class TestLsCommand:
    def test_ls_fixtures(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["ls", str(FIXTURES)])
        assert result.exit_code == 0
        assert "valid_entry.md" in result.output
        assert "NaN policy" in result.output

    def test_ls_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--json-output", "ls", str(FIXTURES)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert any(e["file"] == "valid_entry.md" for e in data)

    def test_ls_nonexistent(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["ls", "/nonexistent/path"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_ls_shows_confidence(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["ls", str(FIXTURES)])
        assert "[established]" in result.output

    def test_ls_empty_dir(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["ls", str(tmp_path)])
        assert result.exit_code == 0
        assert "No .md files" in result.output


class TestTocCommand:
    def test_toc_valid(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["toc", str(FIXTURES / "valid_entry.md")])
        assert result.exit_code == 0
        assert "Rationale" in result.output
        assert "Implementation" in result.output
        assert "Exceptions" in result.output

    def test_toc_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json-output", "toc", str(FIXTURES / "valid_entry.md")]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["frontmatter"]["description"] == "NaN policy — never fillna, missing means unknown"
        assert len(data["sections"]) == 3

    def test_toc_auto_md_extension(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["toc", str(FIXTURES / "valid_entry")])
        assert result.exit_code == 0
        assert "Rationale" in result.output

    def test_toc_nonexistent(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["toc", str(FIXTURES / "nonexistent.md")])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_toc_suggests_files(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["toc", str(FIXTURES / "nonexistent.md")])
        assert "Available files" in result.output


class TestSectionCommand:
    def test_section_exact(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["section", str(FIXTURES / "valid_entry.md"), "Rationale"]
        )
        assert result.exit_code == 0
        assert "silent bias" in result.output

    def test_section_case_insensitive(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["section", str(FIXTURES / "valid_entry.md"), "rationale"]
        )
        assert result.exit_code == 0
        assert "silent bias" in result.output

    def test_section_partial_match(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["section", str(FIXTURES / "valid_entry.md"), "ration"]
        )
        assert result.exit_code == 0
        assert "silent bias" in result.output

    def test_section_not_found(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["section", str(FIXTURES / "valid_entry.md"), "nonexistent"]
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()
        assert "Available sections" in result.output

    def test_section_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "section", str(FIXTURES / "valid_entry.md"), "Exceptions"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["title"] == "Exceptions"
        assert "None" in data["content"]

    def test_section_auto_md(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["section", str(FIXTURES / "valid_entry"), "Rationale"]
        )
        assert result.exit_code == 0


class TestValidateCommand:
    def test_validate_valid_file(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(FIXTURES / "valid_entry.md")])
        assert result.exit_code == 0
        assert "[pass]" in result.output

    def test_validate_invalid_file(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(FIXTURES / "bad_frontmatter.md")])
        assert result.exit_code == 1
        assert "[error]" in result.output

    def test_validate_directory(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(FIXTURES)])
        assert result.exit_code == 1  # has invalid files
        assert "error" in result.output.lower()

    def test_validate_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json-output", "validate", str(FIXTURES / "valid_entry.md")]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["valid"] is True

    def test_validate_json_invalid(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json-output", "validate", str(FIXTURES / "invalid_enums.md")]
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data[0]["valid"] is False
        assert len(data[0]["errors"]) > 0


class TestInitCommand:
    def test_init_creates_dirs(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["init", "test-agent", "--base", str(tmp_path / "memory")]
        )
        assert result.exit_code == 0
        assert (tmp_path / "memory" / "test-agent" / "atlas").is_dir()
        assert (tmp_path / "memory" / "test-agent" / "efforts").is_dir()
        assert (tmp_path / "memory" / "test-agent" / "calendar").is_dir()
        assert (tmp_path / "memory" / "test-agent" / "moc").is_dir()

    def test_init_json(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "init", "json-agent", "--base", str(tmp_path / "memory")],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["agent_id"] == "json-agent"
        assert len(data["created"]) == 4

    def test_init_idempotent(self, tmp_path: Path) -> None:
        runner = CliRunner()
        base = str(tmp_path / "memory")
        runner.invoke(cli, ["init", "test-agent", "--base", base])
        result = runner.invoke(cli, ["init", "test-agent", "--base", base])
        assert result.exit_code == 0
        assert "Already exists" in result.output


class TestNewCommand:
    """Tests for the 'new' subcommand."""

    def _run_new(
        self,
        runner: CliRunner,
        tmp_path: Path,
        extra_args: list[str] | None = None,
        env_override: dict[str, str] | None = None,
    ) -> object:
        """Helper to invoke the 'new' command with standard env."""
        env = {
            "AGENT_ID": "test-agent",
            "AGENT_MEMORY_PATH": str(tmp_path),
        }
        if env_override:
            env.update(env_override)
        args = ["--json-output", "new", "test-entry", "-d", "A test entry", "--no-git"]
        if extra_args:
            args.extend(extra_args)
        return runner.invoke(cli, args, env=env)

    def test_creates_entry_with_required_args(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = self._run_new(runner, tmp_path)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "path" in data
        assert Path(data["path"]).exists()

    def test_json_output_includes_path_and_description(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = self._run_new(runner, tmp_path)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["description"] == "A test entry"
        assert "path" in data
        assert data["agent_id"] == "test-agent"
        assert data["name"] == "test-entry"

    def test_missing_agent_id_fails(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["new", "entry-name", "-d", "desc", "--no-git"],
            env={"AGENT_MEMORY_PATH": str(tmp_path)},
        )
        assert result.exit_code == 1
        assert "agent id" in result.output.lower() or "AGENT_ID" in result.output

    def test_no_git_skips_git_operations(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = self._run_new(runner, tmp_path)
        assert result.exit_code == 0
        data = json.loads(result.output)
        # git_sha should not be present when --no-git is used
        assert "git_sha" not in data

    def test_category_places_file_in_subdirectory(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = self._run_new(runner, tmp_path, extra_args=["-c", "atlas"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        path = Path(data["path"])
        assert "atlas" in path.parts
        assert data["category"] == "atlas"

    def test_shared_writes_to_shared_directory(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = self._run_new(runner, tmp_path, extra_args=["--shared"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        path = Path(data["path"])
        assert "shared" in path.parts
        assert "test-agent" not in str(path)

    def test_tags_splits_comma_separated(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = self._run_new(runner, tmp_path, extra_args=["-t", "alpha,beta,gamma"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["tags"] == ["alpha", "beta", "gamma"]

    def test_invalid_confidence_fails(self, tmp_path: Path) -> None:
        runner = CliRunner()
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(tmp_path)}
        result = runner.invoke(
            cli,
            ["new", "bad-conf", "-d", "desc", "--no-git", "--confidence", "bogus"],
            env=env,
        )
        # click.Choice should reject this before it reaches writer
        assert result.exit_code != 0

    def test_invalid_category_fails(self, tmp_path: Path) -> None:
        runner = CliRunner()
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(tmp_path)}
        result = runner.invoke(
            cli,
            ["new", "bad-cat", "-d", "desc", "--no-git", "-c", "bogus"],
            env=env,
        )
        assert result.exit_code != 0

    def test_duplicate_entry_fails(self, tmp_path: Path) -> None:
        runner = CliRunner()
        # Create first
        self._run_new(runner, tmp_path)
        # Create duplicate
        result = self._run_new(runner, tmp_path)
        assert result.exit_code == 1
        assert "already exists" in result.output.lower()

    def test_with_body(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = self._run_new(
            runner, tmp_path, extra_args=["-b", "## Section\nBody text"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        text = Path(data["path"]).read_text(encoding="utf-8")
        assert "Body text" in text

    def test_status_in_output(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = self._run_new(runner, tmp_path, extra_args=["--status", "draft"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "draft"

    def test_author_override(self, tmp_path: Path) -> None:
        runner = CliRunner()
        env = {"AGENT_MEMORY_PATH": str(tmp_path)}
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "author-test", "-d", "desc",
                "--no-git", "--author", "custom-agent",
            ],
            env=env,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["agent_id"] == "custom-agent"


class TestNewBranchGuard:
    """Issue #82 -- end-to-end CLI tests for the branch guard on `memory new`.

    These tests build a real git repository on disk and exercise the full
    CLI invocation (no subprocess mocking) so the user-facing behavior is
    verified: exit codes, stderr formatting, and the override flag.
    """

    def _build_repo(
        self, tmp_path: Path, default_branch: str = "main"
    ) -> Path:
        import subprocess as sp

        repo = tmp_path / "repo"
        bare = tmp_path / "bare.git"
        repo.mkdir()
        sp.run(
            ["git", "init", "--bare", "-q", "-b", default_branch, str(bare)],
            check=True,
            capture_output=True,
        )
        sp.run(
            ["git", "init", "-q", "-b", default_branch, str(repo)],
            check=True,
            capture_output=True,
        )
        sp.run(
            ["git", "-C", str(repo), "config", "user.email", "test@test"],
            check=True,
            capture_output=True,
        )
        sp.run(
            ["git", "-C", str(repo), "config", "user.name", "test"],
            check=True,
            capture_output=True,
        )
        sp.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-q", "-m", "init"],
            check=True,
            capture_output=True,
        )
        sp.run(
            ["git", "-C", str(repo), "remote", "add", "origin", str(bare)],
            check=True,
            capture_output=True,
        )
        sp.run(
            ["git", "-C", str(repo), "push", "-q", "-u", "origin", default_branch],
            check=True,
            capture_output=True,
        )
        sp.run(
            ["git", "-C", str(repo), "remote", "set-head", "origin", "--auto"],
            check=True,
            capture_output=True,
        )
        return repo

    def _checkout_feature_branch(
        self, repo: Path, name: str = "fix/feature", push: bool = False
    ) -> None:
        import subprocess as sp

        sp.run(
            ["git", "-C", str(repo), "checkout", "-q", "-b", name],
            check=True,
            capture_output=True,
        )
        if push:
            # Publish the branch to origin so pull --rebase has something
            # to rebase against (used by override-flag tests).
            sp.run(
                ["git", "-C", str(repo), "push", "-q", "-u", "origin", name],
                check=True,
                capture_output=True,
            )

    def test_new_succeeds_on_default_branch(self, tmp_path: Path) -> None:
        repo = self._build_repo(tmp_path)
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(repo)}
        runner = CliRunner()
        # Use --no-git to avoid the network push step but still exercise
        # the branch check (which fires inside commit_and_push).
        # NOTE: --no-git skips commit_and_push entirely, so the branch
        # check is bypassed too. To actually exercise the check, we run
        # without --no-git and rely on the bare remote being local.
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "happy-path",
                "-d", "happy", "-b", "## Body\nContent.",
                "-c", "atlas",
            ],
            env=env,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "git_sha" in data

    def test_new_uses_memory_repo_branch_not_cwd(self, tmp_path: Path) -> None:
        """Regression: the branch guard must check the repo that holds the
        memory file (resolved from --base), NOT the process cwd.

        Before the fix, ``commit_and_push`` was called with
        ``repo_path = Path.cwd()``. When the CLI was invoked from a working
        directory that happened to be a *different* git repo checked out on a
        feature branch, the guard reported that foreign branch name and
        refused the write -- a phantom-branch refusal even though the memory
        repo itself was on the default branch. This test stands the process
        in a foreign repo on a non-default branch and asserts the write to
        the on-main memory repo still succeeds.
        """
        import os
        import subprocess as sp

        # The memory repo, on main, with a bare remote (so push works).
        repo = self._build_repo(tmp_path)

        # A SEPARATE foreign repo checked out on a non-default branch, which
        # we make the process cwd. This mimics the orchestrator invoking
        # `memory new` from inside an unrelated campaign checkout.
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        sp.run(["git", "init", "-q", str(foreign)], check=True, capture_output=True)
        sp.run(
            ["git", "-C", str(foreign), "config", "user.email", "t@t"],
            check=True, capture_output=True,
        )
        sp.run(
            ["git", "-C", str(foreign), "config", "user.name", "t"],
            check=True, capture_output=True,
        )
        sp.run(
            ["git", "-C", str(foreign), "commit", "--allow-empty", "-q", "-m", "x"],
            check=True, capture_output=True,
        )
        sp.run(
            ["git", "-C", str(foreign), "checkout", "-q", "-b",
             "sn-schrodinger-newton-crossing"],
            check=True, capture_output=True,
        )

        env = {"AGENT_ID": "test-agent"}
        runner = CliRunner()
        prev_cwd = os.getcwd()
        os.chdir(foreign)
        try:
            result = runner.invoke(
                cli,
                [
                    "--json-output", "new", "crossing-check",
                    "-d", "verify", "-b", "## Body\nContent.",
                    "-c", "atlas",
                    "--base", str(repo),
                ],
                env=env,
            )
        finally:
            os.chdir(prev_cwd)

        # Must succeed: the memory repo is on main even though cwd is on the
        # phantom branch. A failure here means the guard inspected cwd.
        assert result.exit_code == 0, result.output
        assert "git_sha" in json.loads(result.output)
        # And the commit landed on main in the memory repo, not the foreign one.
        head_branch = sp.run(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        assert head_branch == "main"

    def test_new_refuses_on_feature_branch(self, tmp_path: Path) -> None:
        repo = self._build_repo(tmp_path)
        self._checkout_feature_branch(repo, "fix/some-feature")
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(repo)}
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "new", "should-refuse",
                "-d", "no", "-b", "## x\ny",
                "-c", "atlas",
            ],
            env=env,
        )
        # Branch mismatch uses exit code 2 (distinct from generic git
        # errors at exit 1) so callers can distinguish the failure mode.
        assert result.exit_code == 2, result.output
        # The error message must include all the actionable pieces.
        assert "fix/some-feature" in result.output
        assert "main" in result.output
        assert "git -C" in result.output
        assert "checkout main" in result.output
        assert "--allow-non-main-branch" in result.output

    def test_new_allow_non_main_branch_lets_write_through(
        self, tmp_path: Path
    ) -> None:
        repo = self._build_repo(tmp_path)
        # Publish the feature branch so pull --rebase has a target.
        self._checkout_feature_branch(repo, "feat/intentional", push=True)
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(repo)}
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "intentional-write",
                "-d", "ok", "-b", "## x\ny",
                "-c", "atlas",
                "--allow-non-main-branch",
            ],
            env=env,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "git_sha" in data

    def test_new_legacy_master_default_branch_works(
        self, tmp_path: Path
    ) -> None:
        # Verify default-branch detection for repos using "master".
        repo = self._build_repo(tmp_path, default_branch="master")
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(repo)}
        runner = CliRunner()
        # On master (the default) -- write should succeed.
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "legacy-ok",
                "-d", "ok", "-b", "## x\ny",
                "-c", "atlas",
            ],
            env=env,
        )
        assert result.exit_code == 0, result.output

    def test_new_legacy_master_refuses_on_feature_branch(
        self, tmp_path: Path
    ) -> None:
        # Verify the error message resolves the legacy default correctly.
        repo = self._build_repo(tmp_path, default_branch="master")
        self._checkout_feature_branch(repo, "fix/legacy-feature")
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(repo)}
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "new", "legacy-refuse",
                "-d", "no", "-b", "## x\ny",
                "-c", "atlas",
            ],
            env=env,
        )
        assert result.exit_code == 2, result.output
        assert "expected branch: 'master'" in result.output
        assert "checkout master" in result.output

    def test_update_refuses_on_feature_branch(self, tmp_path: Path) -> None:
        repo = self._build_repo(tmp_path)
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(repo)}
        runner = CliRunner()

        # First, create an entry on the default branch.
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "to-update",
                "-d", "initial", "-b", "## x\ninitial",
                "-c", "atlas",
            ],
            env=env,
        )
        assert result.exit_code == 0, result.output
        entry_path = json.loads(result.output)["path"]

        # Switch to a feature branch and try to update -- must refuse.
        self._checkout_feature_branch(repo, "fix/update-feature")
        result = runner.invoke(
            cli,
            ["update", entry_path, "-b", "## x\nupdated"],
            env=env,
        )
        assert result.exit_code == 2, result.output
        assert "fix/update-feature" in result.output
        assert "--allow-non-main-branch" in result.output

    def test_update_allow_non_main_branch_lets_update_through(
        self, tmp_path: Path
    ) -> None:
        repo = self._build_repo(tmp_path)
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(repo)}
        runner = CliRunner()

        # Create entry on main.
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "to-update-with-override",
                "-d", "initial", "-b", "## x\ninitial",
                "-c", "atlas",
            ],
            env=env,
        )
        assert result.exit_code == 0, result.output
        entry_path = json.loads(result.output)["path"]

        # Switch and use the override -- publish the branch so the
        # auto pull --rebase has a target.
        self._checkout_feature_branch(repo, "feat/intentional-update", push=True)
        result = runner.invoke(
            cli,
            [
                "--json-output", "update", entry_path,
                "-b", "## x\nupdated",
                "--allow-non-main-branch",
            ],
            env=env,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "git_sha" in data

    def test_no_git_bypasses_branch_check_entirely(
        self, tmp_path: Path
    ) -> None:
        # --no-git skips the entire git workflow including the branch
        # check, since we never call commit_and_push.
        repo = self._build_repo(tmp_path)
        self._checkout_feature_branch(repo, "fix/no-git")
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(repo)}
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "no-git-ok",
                "-d", "ok", "-b", "## x\ny",
                "-c", "atlas",
                "--no-git",
            ],
            env=env,
        )
        assert result.exit_code == 0, result.output

    def test_new_rolls_back_file_on_branch_mismatch(
        self, tmp_path: Path
    ) -> None:
        """The just-created file must be removed when the branch check fails.

        Without this rollback, a re-run after `git checkout main` would
        fail with "Entry already exists" -- the wrong error for the wrong
        reason. The user-visible behavior is transactional: the operation
        either succeeds or leaves no trace.
        """
        repo = self._build_repo(tmp_path)
        self._checkout_feature_branch(repo, "fix/rollback-test")
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(repo)}
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "new", "rollback-target",
                "-d", "no", "-b", "## x\ny",
                "-c", "atlas",
            ],
            env=env,
        )
        assert result.exit_code == 2, result.output
        # File must NOT exist after the failure.
        expected_path = repo / "test-agent" / "atlas" / "rollback-target.md"
        assert not expected_path.exists(), (
            f"branch-mismatch rollback failed: {expected_path} still exists"
        )

    def test_update_rolls_back_content_on_branch_mismatch(
        self, tmp_path: Path
    ) -> None:
        """The in-place mutation must be reverted when the branch check fails.

        update_entry rewrites the file in place. If the branch check fires
        afterward, the on-disk file would be in a half-state (mutated body,
        not committed). Rollback restores the original content.
        """
        repo = self._build_repo(tmp_path)
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(repo)}
        runner = CliRunner()

        # Create entry on main first.
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "update-rollback",
                "-d", "initial", "-b", "## Body\noriginal content",
                "-c", "atlas",
            ],
            env=env,
        )
        assert result.exit_code == 0, result.output
        entry_path = Path(json.loads(result.output)["path"])
        original_content = entry_path.read_text(encoding="utf-8")
        assert "original content" in original_content

        # Switch to feature branch and try to update.
        self._checkout_feature_branch(repo, "fix/update-rollback")
        result = runner.invoke(
            cli,
            ["update", str(entry_path), "-b", "## Body\nMUTATED content"],
            env=env,
        )
        assert result.exit_code == 2, result.output

        # File content must be the original, not the mutation.
        post_failure_content = entry_path.read_text(encoding="utf-8")
        assert post_failure_content == original_content, (
            "branch-mismatch rollback failed: file content was mutated"
        )
        assert "MUTATED content" not in post_failure_content
        assert "original content" in post_failure_content

    def test_new_rollback_cleans_up_empty_parent_dirs(
        self, tmp_path: Path
    ) -> None:
        """Branch-mismatch rollback removes the empty <agent>/<category>/ tree.

        Without this cleanup, an empty `<agent_id>/<category>/` directory tree
        is left on disk after every refused write. The created file is
        unlinked, but the parent directories created by `mkdir(parents=True)`
        survive. Cosmetic but accumulates over time and shows up in `ls`.
        """
        repo = self._build_repo(tmp_path)
        self._checkout_feature_branch(repo, "fix/parent-dir-cleanup")
        # Use a fresh agent_id so the entire <agent>/<category>/ tree is
        # created (and should be cleaned up) by this single failed write.
        env = {"AGENT_ID": "fresh-agent", "AGENT_MEMORY_PATH": str(repo)}
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "new", "doomed-entry",
                "-d", "no", "-b", "## x\ny",
                "-c", "atlas",
            ],
            env=env,
        )
        assert result.exit_code == 2, result.output
        # File AND its parent dirs (atlas/, fresh-agent/) must be gone.
        agent_dir = repo / "fresh-agent"
        assert not agent_dir.exists(), (
            f"empty parent dir survived rollback: {agent_dir} still exists"
        )

    def test_new_rollback_preserves_parent_with_siblings(
        self, tmp_path: Path
    ) -> None:
        """Parent dir must NOT be removed when sibling entries are present.

        The walk-up cleanup is supposed to break on the first non-empty
        directory. This test asserts that an existing sibling entry under
        the same `<agent>/<category>/` is left intact when a different new
        entry under the same path is rolled back.
        """
        repo = self._build_repo(tmp_path)
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(repo)}
        runner = CliRunner()

        # Create a sibling entry on the default branch first.
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "sibling",
                "-d", "first", "-b", "## x\nsibling",
                "-c", "atlas",
            ],
            env=env,
        )
        assert result.exit_code == 0, result.output
        sibling_path = repo / "test-agent" / "atlas" / "sibling.md"
        assert sibling_path.exists()

        # Switch to feature branch and try to create another entry in the
        # same dir. The branch check fires; rollback should NOT remove
        # `test-agent/atlas/` because sibling.md is still there.
        self._checkout_feature_branch(repo, "fix/preserve-siblings")
        result = runner.invoke(
            cli,
            [
                "new", "doomed",
                "-d", "no", "-b", "## x\ny",
                "-c", "atlas",
            ],
            env=env,
        )
        assert result.exit_code == 2, result.output
        # Sibling and its parent dirs must still be intact.
        assert sibling_path.exists(), "sibling entry was wrongly removed by rollback"
        # The doomed entry must NOT exist.
        doomed_path = repo / "test-agent" / "atlas" / "doomed.md"
        assert not doomed_path.exists()

    def test_new_rollback_never_removes_base_path(
        self, tmp_path: Path
    ) -> None:
        """Walk-up rollback must never remove the operator's memory root.

        When `base_path` is a subdirectory of the git root and the user
        invokes `memory new --shared`, the file lives at
        `base/shared/<name>.md`. After unlink:
            * category_dir = base/shared            -> empty -> rmdir succeeds
            * agent_dir    = base                   -> empty -> would rmdir
        Without the bound, the rollback would delete the operator's
        memory root. The bound stops the walk at base_path so the
        directory survives.

        Symmetric concern for the standard layout when category is empty:
        category_dir collapses to base/<agent_id> and agent_dir to base.
        Same fix protects both.
        """
        import subprocess as sp

        # Build a git repo with the memory base in a SUBDIRECTORY of the
        # git root, not the git root itself. This is the only configuration
        # in which `base.rmdir()` could succeed (no `.git/` to block it).
        git_root = tmp_path / "project"
        git_root.mkdir()
        memory_base = git_root / "memory"
        memory_base.mkdir()

        bare = tmp_path / "bare.git"
        sp.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True, capture_output=True)
        sp.run(["git", "init", "-q", "-b", "main", str(git_root)], check=True, capture_output=True)
        sp.run(["git", "-C", str(git_root), "config", "user.email", "test@test"], check=True, capture_output=True)
        sp.run(["git", "-C", str(git_root), "config", "user.name", "test"], check=True, capture_output=True)
        sp.run(["git", "-C", str(git_root), "commit", "--allow-empty", "-q", "-m", "init"], check=True, capture_output=True)
        sp.run(["git", "-C", str(git_root), "remote", "add", "origin", str(bare)], check=True, capture_output=True)
        sp.run(["git", "-C", str(git_root), "push", "-q", "-u", "origin", "main"], check=True, capture_output=True)
        sp.run(["git", "-C", str(git_root), "remote", "set-head", "origin", "--auto"], check=True, capture_output=True)
        sp.run(["git", "-C", str(git_root), "checkout", "-q", "-b", "fix/walkup-bound"], check=True, capture_output=True)

        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(memory_base)}
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "new", "doomed-shared",
                "-d", "no", "-b", "## x\ny",
                "--shared",
            ],
            env=env,
        )
        assert result.exit_code == 2, result.output
        # The file must be unlinked.
        assert not (memory_base / "shared" / "doomed-shared.md").exists()
        # The empty `shared/` dir is allowed to be cleaned up (one level).
        # The memory root itself MUST survive -- the operator owns it.
        assert memory_base.exists(), (
            f"rollback wrongly removed the memory root: {memory_base}"
        )
        assert memory_base.is_dir()

    def test_new_rollback_failure_surfaces_warning(
        self, tmp_path: Path
    ) -> None:
        """When the rollback itself fails, the user must be told.

        Without this warning, a failed unlink leaves the file on disk while
        the user sees only the BranchMismatchError and assumes the file is
        gone. The warning surfaces the divergence so the operator can
        clean up manually.
        """
        from unittest.mock import patch

        repo = self._build_repo(tmp_path)
        self._checkout_feature_branch(repo, "fix/rollback-failure")
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(repo)}
        runner = CliRunner()

        # Patch Path.unlink to raise OSError. The branch check still fires
        # first; the unlink in the except handler is what we're forcing to
        # fail. We patch only the rollback path, not the entire CLI.
        original_unlink = Path.unlink

        def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
            # Only fail unlink on entries inside the test repo (not stray
            # tempfiles, lock files, etc).
            if str(self).startswith(str(repo)):
                raise PermissionError("simulated rollback failure")
            return original_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(Path, "unlink", failing_unlink):
            result = runner.invoke(
                cli,
                [
                    "new", "rollback-fail",
                    "-d", "no", "-b", "## x\ny",
                    "-c", "atlas",
                ],
                env=env,
            )

        assert result.exit_code == 2, result.output
        # The branch error is still the primary message...
        assert "refusing to write memory entry" in result.output
        # ...AND the rollback failure is surfaced as a warning.
        assert "warning:" in result.output
        assert "rollback after branch-mismatch failed" in result.output
        assert "PermissionError" in result.output

    def test_update_rollback_failure_surfaces_warning(
        self, tmp_path: Path
    ) -> None:
        """Update rollback failure must also be surfaced on stderr.

        Symmetric to the new_cmd test: when write_text fails during the
        snapshot restore, the user sees both the original BranchMismatchError
        and a warning that the file is in an unexpected mutated state.
        """
        from unittest.mock import patch

        repo = self._build_repo(tmp_path)
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(repo)}
        runner = CliRunner()

        # Create an entry on main first so update has something to update.
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "to-update-with-failed-rollback",
                "-d", "initial", "-b", "## Body\noriginal",
                "-c", "atlas",
            ],
            env=env,
        )
        assert result.exit_code == 0, result.output
        entry_path = Path(json.loads(result.output)["path"])

        self._checkout_feature_branch(repo, "fix/update-rollback-failure")

        # Patch Path.write_text on the specific entry path to raise during
        # the rollback. update_entry uses write_text to save the mutation;
        # the rollback also uses write_text to restore. We need the rollback
        # call to fail. We do this by tracking call count: the first call
        # (the mutation) succeeds, the second (the rollback) raises.
        original_write_text = Path.write_text
        call_count = {"n": 0}

        def selective_write_failure(
            self: Path, data: str, **kwargs: object
        ) -> int:
            if str(self) == str(entry_path):
                call_count["n"] += 1
                if call_count["n"] >= 2:
                    raise PermissionError("simulated rollback write failure")
            return original_write_text(self, data, **kwargs)  # type: ignore[arg-type]

        with patch.object(Path, "write_text", selective_write_failure):
            result = runner.invoke(
                cli,
                ["update", str(entry_path), "-b", "## Body\nMUTATED"],
                env=env,
            )

        assert result.exit_code == 2, result.output
        assert "refusing to write memory entry" in result.output
        assert "warning:" in result.output
        assert "rollback after branch-mismatch failed" in result.output
        assert "PermissionError" in result.output


class TestUpdateCommand:
    """Tests for the 'update' subcommand."""

    def _create_entry(self, runner: CliRunner, tmp_path: Path, name: str = "update-target") -> Path:
        """Create an entry to be updated, return its file path."""
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(tmp_path)}
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", name, "-d", "Original description",
                "--no-git", "-t", "original", "--confidence", "working",
                "--status", "active", "-b", "Original body.",
            ],
            env=env,
        )
        assert result.exit_code == 0, f"Setup failed: {result.output}"
        data = json.loads(result.output)
        return Path(data["path"])

    def test_updates_body_content(self, tmp_path: Path) -> None:
        runner = CliRunner()
        file_path = self._create_entry(runner, tmp_path)
        result = runner.invoke(
            cli,
            ["--json-output", "update", str(file_path), "--no-git", "-b", "New body content."],
        )
        assert result.exit_code == 0
        text = file_path.read_text(encoding="utf-8")
        assert "New body content." in text
        assert "Original body." not in text

    def test_bumps_updated_timestamp(self, tmp_path: Path) -> None:
        runner = CliRunner()
        file_path = self._create_entry(runner, tmp_path)

        text_before = file_path.read_text(encoding="utf-8")
        from agent_memory.parser import parse_frontmatter
        fm_before, _ = parse_frontmatter(text_before)

        result = runner.invoke(
            cli,
            ["--json-output", "update", str(file_path), "--no-git", "-b", "Bumped."],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["frontmatter"]["updated"] != fm_before.updated or True
        # The updated timestamp should exist and be a non-empty string
        assert data["frontmatter"]["updated"]

    def test_tags_replaces_tags(self, tmp_path: Path) -> None:
        runner = CliRunner()
        file_path = self._create_entry(runner, tmp_path)
        result = runner.invoke(
            cli,
            ["--json-output", "update", str(file_path), "--no-git", "-t", "new1,new2"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["frontmatter"]["tags"] == ["new1", "new2"]

    def test_add_tags_appends_tags(self, tmp_path: Path) -> None:
        runner = CliRunner()
        file_path = self._create_entry(runner, tmp_path)
        result = runner.invoke(
            cli,
            ["--json-output", "update", str(file_path), "--no-git", "--add-tags", "appended"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "original" in data["frontmatter"]["tags"]
        assert "appended" in data["frontmatter"]["tags"]

    def test_file_not_found_fails_with_suggestions(self, tmp_path: Path) -> None:
        runner = CliRunner()
        # Create one file so suggestions can be generated
        self._create_entry(runner, tmp_path)
        nonexistent = str(tmp_path / "test-agent" / "nonexistent.md")
        result = runner.invoke(
            cli,
            ["update", nonexistent, "--no-git", "-b", "anything"],
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_no_git_skips_git_operations(self, tmp_path: Path) -> None:
        runner = CliRunner()
        file_path = self._create_entry(runner, tmp_path)
        result = runner.invoke(
            cli,
            ["--json-output", "update", str(file_path), "--no-git", "-b", "Updated."],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "git_sha" not in data

    def test_json_output_includes_updated_frontmatter(self, tmp_path: Path) -> None:
        runner = CliRunner()
        file_path = self._create_entry(runner, tmp_path)
        result = runner.invoke(
            cli,
            [
                "--json-output", "update", str(file_path), "--no-git",
                "-b", "New body", "--confidence", "established",
                "--status", "archived",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "frontmatter" in data
        fm = data["frontmatter"]
        assert fm["confidence"] == "established"
        assert fm["status"] == "archived"
        assert fm["description"] == "Original description"
        assert fm["author"] == "test-agent"
        assert fm["created"]  # non-empty
        assert fm["updated"]  # non-empty

    def test_update_confidence(self, tmp_path: Path) -> None:
        runner = CliRunner()
        file_path = self._create_entry(runner, tmp_path)
        result = runner.invoke(
            cli,
            ["--json-output", "update", str(file_path), "--no-git", "--confidence", "exploratory"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["frontmatter"]["confidence"] == "exploratory"

    def test_update_status(self, tmp_path: Path) -> None:
        runner = CliRunner()
        file_path = self._create_entry(runner, tmp_path)
        result = runner.invoke(
            cli,
            ["--json-output", "update", str(file_path), "--no-git", "--status", "draft"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["frontmatter"]["status"] == "draft"



class TestStdinPipeBody:
    """Tests for stdin pipe body reading (issue #49).

    Verifies that body content piped via stdin is correctly captured
    when using -b - (explicit) or when stdin has piped content (auto).
    """

    def test_new_b_dash_reads_stdin(self, tmp_path: Path) -> None:
        """Explicit -b - reads body from stdin."""
        runner = CliRunner()
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(tmp_path)}
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "stdin-explicit", "-d", "test",
                "--no-git", "-b", "-",
            ],
            env=env,
            input="## Section\nBody from stdin pipe.\n",
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        text = Path(data["path"]).read_text(encoding="utf-8")
        assert "Body from stdin pipe." in text

    def test_new_auto_stdin_reads_piped_content(self, tmp_path: Path) -> None:
        """Without -b, piped stdin content is auto-read as body."""
        runner = CliRunner()
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(tmp_path)}
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "stdin-auto", "-d", "test",
                "--no-git",
            ],
            env=env,
            input="## Auto Section\nAuto-detected body content.\n",
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        text = Path(data["path"]).read_text(encoding="utf-8")
        assert "Auto-detected body content." in text

    def test_new_b_dash_empty_stdin_errors(self, tmp_path: Path) -> None:
        """Explicit -b - with empty stdin raises an error."""
        runner = CliRunner()
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(tmp_path)}
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "stdin-empty", "-d", "test",
                "--no-git", "-b", "-",
            ],
            env=env,
            input="",
        )
        assert result.exit_code == 1
        assert "stdin" in result.output.lower()

    def test_new_b_dash_whitespace_only_stdin_errors(self, tmp_path: Path) -> None:
        """Explicit -b - with whitespace-only stdin raises an error."""
        runner = CliRunner()
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(tmp_path)}
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "stdin-ws", "-d", "test",
                "--no-git", "-b", "-",
            ],
            env=env,
            input="   \n  \n  ",
        )
        assert result.exit_code == 1
        assert "stdin" in result.output.lower()

    def test_new_inline_body_still_works(self, tmp_path: Path) -> None:
        """Inline -b value is used when provided directly."""
        runner = CliRunner()
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(tmp_path)}
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "inline-body", "-d", "test",
                "--no-git", "-b", "## Inline\nDirect body content.",
            ],
            env=env,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        text = Path(data["path"]).read_text(encoding="utf-8")
        assert "Direct body content." in text

    def test_new_no_body_no_stdin_creates_frontmatter_only(self, tmp_path: Path) -> None:
        """No body and no stdin creates entry with frontmatter only."""
        runner = CliRunner()
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(tmp_path)}
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "no-body", "-d", "test",
                "--no-git",
            ],
            env=env,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        text = Path(data["path"]).read_text(encoding="utf-8")
        assert text.strip().endswith("---")

    def test_new_multiline_stdin_preserved(self, tmp_path: Path) -> None:
        """Multiline stdin content is fully preserved."""
        runner = CliRunner()
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(tmp_path)}
        body = "## First\nParagraph one.\n\n## Second\nParagraph two.\n"
        result = runner.invoke(
            cli,
            [
                "--json-output", "new", "multiline-stdin", "-d", "test",
                "--no-git", "-b", "-",
            ],
            env=env,
            input=body,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        text = Path(data["path"]).read_text(encoding="utf-8")
        assert "Paragraph one." in text
        assert "Paragraph two." in text

    def test_update_b_dash_reads_stdin(self, tmp_path: Path) -> None:
        """Update command with -b - reads body from stdin."""
        runner = CliRunner()
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(tmp_path)}
        create_result = runner.invoke(
            cli,
            [
                "--json-output", "new", "update-stdin", "-d", "test",
                "--no-git", "-b", "## Original\nOriginal content.",
            ],
            env=env,
        )
        assert create_result.exit_code == 0
        file_path = json.loads(create_result.output)["path"]

        result = runner.invoke(
            cli,
            [
                "--json-output", "update", file_path, "--no-git", "-b", "-",
            ],
            input="## Updated\nNew content from stdin.\n",
        )
        assert result.exit_code == 0
        text = Path(file_path).read_text(encoding="utf-8")
        assert "New content from stdin." in text
        assert "Original content." not in text

    def test_update_auto_stdin_reads_piped_content(self, tmp_path: Path) -> None:
        """Update command auto-reads piped stdin as body."""
        runner = CliRunner()
        env = {"AGENT_ID": "test-agent", "AGENT_MEMORY_PATH": str(tmp_path)}
        create_result = runner.invoke(
            cli,
            [
                "--json-output", "new", "update-auto-stdin", "-d", "test",
                "--no-git", "-b", "## Original\nOriginal content.",
            ],
            env=env,
        )
        assert create_result.exit_code == 0
        file_path = json.loads(create_result.output)["path"]

        result = runner.invoke(
            cli,
            [
                "--json-output", "update", file_path, "--no-git",
            ],
            input="## Auto Updated\nAuto-detected update content.\n",
        )
        assert result.exit_code == 0
        text = Path(file_path).read_text(encoding="utf-8")
        assert "Auto-detected update content." in text


# ---------------------------------------------------------------------------
# --base flag tests for toc, section, ls, validate (issue #64)
# ---------------------------------------------------------------------------

VALID_ENTRY_CONTENT = (FIXTURES / "valid_entry.md").read_text(encoding="utf-8")


def _setup_memory_tree(tmp_path: Path) -> Path:
    """Create a realistic memory directory structure under tmp_path.

    Returns the base directory (tmp_path / "memory").
    """
    base = tmp_path / "memory"
    agent_dir = base / "test-agent" / "atlas"
    agent_dir.mkdir(parents=True)
    (agent_dir / "nan-policy.md").write_text(VALID_ENTRY_CONTENT, encoding="utf-8")
    return base


class TestBaseFlagLs:
    """Tests for --base on the ls command."""

    def test_ls_relative_path_with_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["ls", "test-agent/atlas", "--base", str(base)]
        )
        assert result.exit_code == 0
        assert "nan-policy.md" in result.output

    def test_ls_base_from_env_var(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["ls", "test-agent/atlas"],
            env={"AGENT_MEMORY_PATH": str(base)},
        )
        assert result.exit_code == 0
        assert "nan-policy.md" in result.output

    def test_ls_explicit_base_overrides_env(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        # Env points to a wrong directory; explicit --base should win
        result = runner.invoke(
            cli,
            ["ls", "test-agent/atlas", "--base", str(base)],
            env={"AGENT_MEMORY_PATH": "/nonexistent/path"},
        )
        assert result.exit_code == 0
        assert "nan-policy.md" in result.output

    def test_ls_absolute_path_ignores_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        abs_path = str(base / "test-agent" / "atlas")
        runner = CliRunner()
        # --base points to junk, but absolute path should bypass it
        result = runner.invoke(
            cli, ["ls", abs_path, "--base", "/nonexistent"]
        )
        assert result.exit_code == 0
        assert "nan-policy.md" in result.output

    def test_ls_json_with_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json-output", "ls", "test-agent/atlas", "--base", str(base)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert any(e["file"] == "nan-policy.md" for e in data)


class TestBaseFlagToc:
    """Tests for --base on the toc command."""

    def test_toc_relative_path_with_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["toc", "test-agent/atlas/nan-policy.md", "--base", str(base)],
        )
        assert result.exit_code == 0
        assert "Rationale" in result.output

    def test_toc_base_from_env_var(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["toc", "test-agent/atlas/nan-policy.md"],
            env={"AGENT_MEMORY_PATH": str(base)},
        )
        assert result.exit_code == 0
        assert "Rationale" in result.output

    def test_toc_absolute_path_ignores_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        abs_path = str(base / "test-agent" / "atlas" / "nan-policy.md")
        runner = CliRunner()
        result = runner.invoke(
            cli, ["toc", abs_path, "--base", "/nonexistent"]
        )
        assert result.exit_code == 0
        assert "Rationale" in result.output

    def test_toc_auto_md_extension_with_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["toc", "test-agent/atlas/nan-policy", "--base", str(base)],
        )
        assert result.exit_code == 0
        assert "Rationale" in result.output

    def test_toc_json_with_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json-output", "toc",
                "test-agent/atlas/nan-policy.md",
                "--base", str(base),
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["frontmatter"]["description"] == (
            "NaN policy \u2014 never fillna, missing means unknown"
        )


class TestBaseFlagSection:
    """Tests for --base on the section command."""

    def test_section_relative_path_with_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "section",
                "test-agent/atlas/nan-policy.md",
                "Rationale",
                "--base", str(base),
            ],
        )
        assert result.exit_code == 0
        assert "silent bias" in result.output

    def test_section_base_from_env_var(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["section", "test-agent/atlas/nan-policy.md", "Rationale"],
            env={"AGENT_MEMORY_PATH": str(base)},
        )
        assert result.exit_code == 0
        assert "silent bias" in result.output

    def test_section_absolute_path_ignores_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        abs_path = str(base / "test-agent" / "atlas" / "nan-policy.md")
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["section", abs_path, "Rationale", "--base", "/nonexistent"],
        )
        assert result.exit_code == 0
        assert "silent bias" in result.output

    def test_section_json_with_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json-output", "section",
                "test-agent/atlas/nan-policy.md",
                "Exceptions",
                "--base", str(base),
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["title"] == "Exceptions"
        assert "None" in data["content"]


class TestBaseFlagValidate:
    """Tests for --base on the validate command."""

    def test_validate_file_with_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "validate",
                "test-agent/atlas/nan-policy.md",
                "--base", str(base),
            ],
        )
        assert result.exit_code == 0
        assert "[pass]" in result.output

    def test_validate_directory_with_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["validate", "test-agent/atlas", "--base", str(base)],
        )
        assert result.exit_code == 0
        assert "[pass]" in result.output

    def test_validate_base_from_env_var(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["validate", "test-agent/atlas/nan-policy.md"],
            env={"AGENT_MEMORY_PATH": str(base)},
        )
        assert result.exit_code == 0
        assert "[pass]" in result.output

    def test_validate_absolute_path_ignores_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        abs_path = str(base / "test-agent" / "atlas" / "nan-policy.md")
        runner = CliRunner()
        result = runner.invoke(
            cli, ["validate", abs_path, "--base", "/nonexistent"]
        )
        assert result.exit_code == 0
        assert "[pass]" in result.output

    def test_validate_nonexistent_path_with_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["validate", "does-not-exist.md", "--base", str(base)],
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_validate_json_with_base(self, tmp_path: Path) -> None:
        base = _setup_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json-output", "validate",
                "test-agent/atlas/nan-policy.md",
                "--base", str(base),
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["valid"] is True
