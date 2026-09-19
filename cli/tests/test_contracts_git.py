"""Real isolated repositories: own-file commits and truthful save/sync results."""
import subprocess
from pathlib import Path

import pytest

from agent_memory.contracts_cli import execute_request
from agent_memory.contracts_store import ContractError, ContractStore


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], check=check, text=True,
                          capture_output=True, timeout=15)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MEMORY_DEFAULT_BRANCH", "main")
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "tests@example.invalid")
    git(tmp_path, "config", "user.name", "Contract Tests")
    return tmp_path


def create(repo, *, plan_id="example", **options):
    base = repo / "memory"
    review = execute_request({"action": "review", "template_id": "general"}, base, no_git=True)
    req = review["create_example"]
    req["plan_id"] = plan_id
    return execute_request(req, base, actor="test-agent", **options)


def update(repo, current, **options):
    return execute_request({"action": "update", "plan_id": "example", "revision": current["revision"],
                            "work_item_id": "work", "status": "in_progress"}, repo / "memory", **options)


def test_local_only_first_commit_and_no_remote_pull(repo, monkeypatch):
    calls = []
    original = ContractStore._git
    def capture(self, cwd, *args, **kwargs):
        calls.append(args)
        return original(self, cwd, *args, **kwargs)
    monkeypatch.setattr(ContractStore, "_git", capture)
    result = create(repo)
    assert result["persistence"]["saved"] is True
    assert result["persistence"]["commit"] == "committed"
    assert result["persistence"]["sync"] == "local-only"
    assert git(repo, "show", "--format=", "--name-only", "HEAD").stdout.strip() == "memory/shared/plans/example.md"
    assert not any(call[0] in ("pull", "push", "fetch", "rebase") for call in calls)


def test_commit_only_owned_file_preserves_staged_and_unstaged(repo):
    (repo / "staged.txt").write_text("user staged change")
    (repo / "untracked.txt").write_text("leave alone")
    git(repo, "add", "staged.txt")
    staged_before = git(repo, "ls-files", "--stage", "staged.txt").stdout
    result = create(repo)
    assert result["persistence"]["commit"] == "committed"
    assert git(repo, "show", "--format=", "--name-only", "HEAD").stdout.strip() == "memory/shared/plans/example.md"
    assert git(repo, "ls-files", "--stage", "staged.txt").stdout == staged_before
    assert git(repo, "diff", "--cached", "--name-only").stdout.strip() == "staged.txt"
    assert (repo / "untracked.txt").read_text() == "leave alone"
    assert git(repo, "ls-files", "memory/.contracts.lock").stdout == ""
    second = update(repo, result)
    assert second["persistence"]["commit"] == "committed"
    assert git(repo, "diff", "--cached", "--name-only").stdout.strip() == "staged.txt"


def test_no_git_saves_without_staging(repo):
    before = git(repo, "ls-files", "--stage").stdout
    result = create(repo, no_git=True)
    assert result["persistence"] == {"saved": True, "commit": "skipped", "sync": "skipped", "reason": "no_git"}
    assert git(repo, "ls-files", "--stage").stdout == before
    assert git(repo, "rev-parse", "HEAD", check=False).returncode != 0


def test_not_a_repository_is_reported(tmp_path):
    result = create(tmp_path)
    assert result["persistence"]["saved"]
    assert result["persistence"]["reason"] == "not a Git repository"


def test_non_default_branch_preflight_prevents_save(repo):
    plan = create(repo)
    git(repo, "checkout", "-b", "feature")
    before = Path(plan["path"]).read_bytes()
    with pytest.raises(ContractError, match="default branch"):
        update(repo, plan)
    assert Path(plan["path"]).read_bytes() == before
    overridden = update(repo, plan, allow_non_main_branch=True)
    assert overridden["persistence"]["commit"] == "committed"


def test_staged_contract_is_not_overwritten(repo):
    plan = create(repo)
    path = Path(plan["path"])
    path.write_text(path.read_text() + "\nUser-staged note\n")
    git(repo, "add", str(path))
    current = execute_request({"action": "read", "plan_id": "example"}, repo / "memory")
    before = path.read_bytes()
    with pytest.raises(ContractError, match="staged changes"):
        update(repo, current)
    assert path.read_bytes() == before


def test_commit_failure_distinguishes_saved_file(repo):
    git(repo, "config", "user.name", "")
    git(repo, "config", "user.email", "")
    result = create(repo)
    assert Path(result["path"]).is_file()
    assert result["persistence"]["saved"] is True
    assert result["persistence"]["commit"] == "failed"
    assert "error" in result["persistence"]
    assert result["revision"] == execute_request({"action": "read", "plan_id": "example"}, repo / "memory")["revision"]


def test_remote_without_upstream_is_not_claimed_synced(repo, tmp_path):
    git(repo, "remote", "add", "origin", str(tmp_path / "not-a-repo"))
    result = create(repo)
    assert result["persistence"]["commit"] == "committed"
    assert result["persistence"]["sync"] == "not-configured"


def test_push_success_and_rejection_keep_local_save(repo, tmp_path):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "-b", "main")
    first = create(repo)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    second = update(repo, first)
    assert second["persistence"]["sync"] == "pushed"
    # Refuse subsequent updates without any network or fake remote behavior.
    hook = remote / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    third = update(repo, second)
    assert third["persistence"]["saved"]
    assert third["persistence"]["commit"] == "committed"
    assert third["persistence"]["sync"] == "failed"
    assert "sync_error" in third["persistence"]
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == third["persistence"]["commit_sha"]


def test_template_commit_contains_only_template(repo):
    review = execute_request({"action": "review", "template_id": "general"}, repo / "memory", no_git=True)
    result = execute_request({"action": "save_template", "template_id": "team", "revision": None,
                              "template": review["template"]}, repo / "memory")
    assert result["persistence"]["commit"] == "committed"
    assert git(repo, "show", "--format=", "--name-only", "HEAD").stdout.strip() == "memory/shared/templates/team.md"
