"""Isolated real Git and cooperative concurrency evidence for RSI durability."""
import fcntl
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from agent_memory.contracts_store import ContractError
from agent_memory.policy import PolicyError, PolicyStore
from agent_memory.rsi import execute_request
from agent_memory.rsi_store import ContainedPolicyStore, RSIStore


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], check=check, text=True,
                          capture_output=True, timeout=15)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MEMORY_DEFAULT_BRANCH", "main")
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "RSI Tests")
    git(tmp_path, "config", "user.email", "tests@example.invalid")
    return tmp_path


def proposal(base, entry_id="candidate", **opts):
    current = execute_request({"action": "context", "plan_ids": []}, base, no_git=True)
    return execute_request({"action": "propose", "proposal_id": entry_id, "body": "# Revised policy\nPermissions remain binding.\n",
                            "reason": "Scoped test improvement", "expected_revision": current["policy"]["revision"], "plan_ids": []}, base, **opts)


def evaluation(base, artifact, **opts):
    return execute_request({"action": "record", "kind": "evaluation", "bindings": {**artifact["bindings"], "proposal_id": artifact["id"]},
                            "data": {"status": "assessed", "interpretation": {"automatic_promotion": False, "recommendation": "reject"}}}, base, **opts)


def promote_request(proposed, evaluated):
    return {"action": "promote", "proposal_id": proposed["id"], "evaluation_id": evaluated["id"], "expected_revision": proposed["bindings"]["policy_revision"],
            "review_note": "Explicit self-reported context, not authorization", "actor": "agent", "apply": True}


def test_scoped_first_commit_preserves_unrelated_staged_files(repo):
    (repo / "staged.txt").write_text("User's staged change")
    (repo / "untracked.txt").write_text("Leave untouched")
    git(repo, "add", "staged.txt")
    before = git(repo, "ls-files", "--stage", "staged.txt").stdout
    result = proposal(repo / "memory")
    assert result["persistence"]["saved"]
    assert result["persistence"]["git"]["status"] == "committed_no_remote"
    assert git(repo, "show", "--format=", "--name-only", "HEAD").stdout.strip() == "memory/shared/efforts/rsi-candidate.md"
    assert git(repo, "diff", "--cached", "--name-only").stdout.strip() == "staged.txt"
    assert git(repo, "ls-files", "--stage", "staged.txt").stdout == before
    assert (repo / "untracked.txt").read_text() == "Leave untouched"
    assert not git(repo, "ls-files", "memory/.contracts.lock").stdout


def test_no_git_and_not_repository_are_distinct(repo, tmp_path):
    result = proposal(repo / "memory", no_git=True)
    assert result["persistence"]["git"]["status"] == "disabled"
    assert not git(repo, "ls-files").stdout
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    result = proposal(outside)
    assert result["persistence"]["git"]["status"] == "not_a_repository"
    assert result["persistence"]["saved"]


def test_failed_commit_does_not_undo_or_misreport_local_save(repo):
    git(repo, "config", "user.name", "")
    git(repo, "config", "user.email", "")
    result = proposal(repo / "memory")
    assert result["persistence"]["saved"] and result["persistence"]["git"]["status"] == "git_failed"
    assert not result["persistence"]["git"]["committed"]
    assert execute_request({"action": "read", "id": "candidate"}, repo / "memory")["artifact"]["revision"] == result["artifact"]["revision"]


def test_nondefault_branch_and_detached_head_prevent_local_write(repo):
    proposal(repo / "memory")
    git(repo, "checkout", "-b", "feature")
    with pytest.raises(PolicyError, match="non-default"):
        proposal(repo / "memory", "another")
    assert not (repo / "memory/shared/efforts/rsi-another.md").exists()
    assert proposal(repo / "memory", "another", allow_non_main_branch=True)["persistence"]["git"]["committed"]
    git(repo, "checkout", "--detach")
    with pytest.raises(PolicyError, match="detached"):
        proposal(repo / "memory", "detached", allow_non_main_branch=True)


def test_promotion_commits_only_policy_and_exact_history(repo):
    base = repo / "memory"
    proposed = proposal(base)["artifact"]
    evaluated = evaluation(base, proposed)["artifact"]
    (repo / "staged.txt").write_text("Unrelated staged work")
    git(repo, "add", "staged.txt")
    result = execute_request(promote_request(proposed, evaluated), base)
    assert result["applied"] and result["persistence"]["git"]["committed"]
    changed = git(repo, "show", "--format=", "--name-only", "HEAD").stdout.strip().splitlines()
    assert "memory/shared/policies/agent-policy.md" in changed
    assert len(changed) == 4  # canonical + before/after snapshots + operation event
    assert all(path.startswith("memory/shared/policies/") for path in changed)
    assert git(repo, "diff", "--cached", "--name-only").stdout.strip() == "staged.txt"


def test_staged_policy_owned_file_prevents_promotion_before_write(repo):
    base = repo / "memory"
    proposed = proposal(base)["artifact"]
    evaluated = evaluation(base, proposed)["artifact"]
    path = PolicyStore(base).path()
    path.write_text(proposed["data"]["parent_policy"]["body"])
    git(repo, "add", str(path))
    before = path.read_bytes()
    with pytest.raises(PolicyError, match="staged changes"):
        execute_request(promote_request(proposed, evaluated), base)
    assert path.read_bytes() == before
    assert not (path.parent / ".agent-policy-history").exists()


def test_push_success_and_failure_preserve_local_artifacts(repo):
    remote = repo / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "-b", "main")
    proposal(repo / "memory")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    second = proposal(repo / "memory", "second")
    assert second["persistence"]["git"]["status"] == "pushed"
    hook = remote / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    third = proposal(repo / "memory", "third")
    assert third["persistence"]["saved"]
    assert third["persistence"]["git"]["committed"]
    assert third["persistence"]["git"]["status"] == "push_failed"
    assert Path(third["artifact"]["path"]).is_file()
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == third["persistence"]["git"]["commit"]


def _assert_locks_available(base):
    for path in [base / ".contracts.lock", base / "shared/policies/.agent-policy.lock"]:
        fd = os.open(path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def test_no_snapshot_or_policy_lock_held_during_publication(tmp_path, monkeypatch):
    calls = []
    def publish(self, repo, paths):
        _assert_locks_available(tmp_path)
        calls.append(paths)
        return {"status": "test", "committed": False, "pushed": False}
    monkeypatch.setattr(RSIStore, "publish", publish)
    proposed = proposal(tmp_path, no_git=True)["artifact"]
    evaluated = evaluation(tmp_path, proposed, no_git=True)["artifact"]
    result = execute_request(promote_request(proposed, evaluated), tmp_path, no_git=True)
    assert result["applied"] and len(calls) == 3


def test_concurrent_duplicate_proposals_have_exactly_one_immutable_winner(tmp_path):
    barrier = Barrier(2)
    def run():
        barrier.wait(timeout=5)
        try:
            return proposal(tmp_path, no_git=True)["artifact"]
        except ContractError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=10) for future in [pool.submit(run), pool.submit(run)]]
    winners = [r for r in results if isinstance(r, dict)]
    assert len(winners) == 1
    assert "immutable" in next(r for r in results if isinstance(r, str))
    assert execute_request({"action": "read", "id": "candidate"}, tmp_path, no_git=True)["artifact"]["revision"] == winners[0]["revision"]


def test_concurrent_promotions_cas_one_winner_without_deadlock(tmp_path):
    proposed = proposal(tmp_path, no_git=True)["artifact"]
    evaluated = evaluation(tmp_path, proposed, no_git=True)["artifact"]
    payload = promote_request(proposed, evaluated)
    barrier = Barrier(2)
    def run():
        barrier.wait(timeout=5)
        try:
            return execute_request(payload, tmp_path, no_git=True)
        except (ContractError, PolicyError) as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=10) for future in [pool.submit(run), pool.submit(run)]]
    assert sum(isinstance(r, dict) and r["applied"] for r in results) == 1
    assert "stale" in next(r for r in results if isinstance(r, str))
    assert len(PolicyStore(tmp_path).execute({"action": "history"})["events"]) == 1


def test_policy_cas_closes_snapshot_to_update_race(tmp_path, monkeypatch):
    proposed = proposal(tmp_path, no_git=True)["artifact"]
    evaluated = evaluation(tmp_path, proposed, no_git=True)["artifact"]
    original = ContainedPolicyStore.execute
    def racing_execute(self, request, **kwargs):
        PolicyStore(tmp_path).execute({"action": "update", "body": "Concurrent canonical change", "expected_revision": request["expected_revision"],
                                       "actor": "human", "reason": "Racing policy update"}, no_git=True)
        return original(self, request, **kwargs)
    monkeypatch.setattr(ContainedPolicyStore, "execute", racing_execute)
    with pytest.raises(PolicyError, match="conflict"):
        execute_request(promote_request(proposed, evaluated), tmp_path, no_git=True)
    current = PolicyStore(tmp_path).execute({"action": "read"})
    assert current["body"] == "Concurrent canonical change"
    assert len(PolicyStore(tmp_path).execute({"action": "history"})["events"]) == 1


def _remote(repo):
    remote = repo / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "-b", "main")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    return remote


def test_later_no_git_policy_cannot_be_committed_or_pushed_by_promotion(repo, monkeypatch):
    base = repo / "memory"
    proposed = proposal(base)["artifact"]
    evaluated = evaluation(base, proposed)["artifact"]
    remote = _remote(repo)
    original = RSIStore.publish
    intervened = []
    def later_no_git(self, root, paths):
        _assert_locks_available(base)
        policy = PolicyStore(base)
        revision_a = policy.execute({"action": "read"})["revision"]
        intervened.append(policy.execute({"action": "update", "body": "PRIVATE-NO-GIT-B\n",
                                          "expected_revision": revision_a, "actor": "human",
                                          "reason": "Explicitly local-only newer policy"}, no_git=True))
        return original(self, root, paths)
    monkeypatch.setattr(RSIStore, "publish", later_no_git)
    result = execute_request(promote_request(proposed, evaluated), base)
    assert result["applied"] and result["persistence"]["git"]["status"] == "pushed"
    committed_a = result["persistence"]["git"]["commit"]
    assert git(remote, "rev-parse", "refs/heads/main").stdout.strip() == committed_a
    assert git(repo, "show", f"{committed_a}:memory/shared/policies/agent-policy.md").stdout == proposed["data"]["body"]
    assert result["policy"]["revision"] == proposed["data"]["body_hash"]
    assert PolicyStore(base).execute({"action": "read"})["body"] == "PRIVATE-NO-GIT-B\n"
    assert intervened[0]["git"]["status"] == "disabled"
    assert "memory/shared/policies/agent-policy.md" in git(repo, "diff", "--name-only").stdout
    assert "PRIVATE-NO-GIT-B" not in git(repo, "log", "--all", "-p", "--", "memory/shared/policies").stdout
    assert not git(repo, "diff", "--cached", "--name-only").stdout


def test_network_phase_pushes_exact_commit_not_later_head(repo, monkeypatch):
    base = repo / "memory"
    proposed = proposal(base)["artifact"]
    evaluated = evaluation(base, proposed)["artifact"]
    remote = _remote(repo)
    original = RSIStore.publish
    def moved_head(self, root, paths):
        _assert_locks_available(base)
        (repo / "private.txt").write_text("Do not publish later unrelated commit")
        git(repo, "add", "private.txt")
        git(repo, "commit", "-m", "Unrelated local-only work")
        return original(self, root, paths)
    monkeypatch.setattr(RSIStore, "publish", moved_head)
    result = execute_request(promote_request(proposed, evaluated), base)
    commit_a = result["persistence"]["git"]["commit"]
    assert result["persistence"]["git"]["status"] == "pushed"
    assert git(repo, "rev-parse", "HEAD").stdout.strip() != commit_a
    assert git(remote, "rev-parse", "refs/heads/main").stdout.strip() == commit_a
    assert "private.txt" not in git(remote, "ls-tree", "--name-only", "refs/heads/main").stdout


def test_local_commit_holds_writer_locks_and_actual_push_releases_them(repo, monkeypatch):
    import agent_memory.policy_git as publication
    base = repo / "memory"
    proposed = proposal(base)["artifact"]
    evaluated = evaluation(base, proposed)["artifact"]
    _remote(repo)
    original = publication.run_git
    calls = []
    def checked(root, args):
        if "commit" in args and "--only" in args:
            for path in [base / ".contracts.lock", base / "shared/policies/.agent-policy.lock"]:
                fd = os.open(path, os.O_RDWR)
                try:
                    with pytest.raises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
            calls.append("local-commit-locked")
        if args[0] == "push":
            _assert_locks_available(base)
            assert not args[-1].startswith("HEAD:")
            calls.append("exact-push-unlocked")
        return original(root, args)
    monkeypatch.setattr(publication, "run_git", checked)
    result = execute_request(promote_request(proposed, evaluated), base)
    assert result["persistence"]["git"]["status"] == "pushed"
    assert calls == ["local-commit-locked", "exact-push-unlocked"]


def test_commit_capture_does_not_read_moving_head(repo, monkeypatch):
    import agent_memory.policy_git as publication
    base = repo / "memory"
    proposed = proposal(base)["artifact"]
    evaluated = evaluation(base, proposed)["artifact"]
    remote = _remote(repo)
    original = publication.run_git
    def race_after_command(root, args):
        result = original(root, args)
        if "commit" in args and "--only" in args and result.returncode == 0:
            (repo / "private.txt").write_text("Later commit before SHA capture")
            git(repo, "add", "private.txt")
            git(repo, "commit", "-m", "Later private commit before capture")
        return result
    monkeypatch.setattr(publication, "run_git", race_after_command)
    result = execute_request(promote_request(proposed, evaluated), base)
    commit_a = result["persistence"]["git"]["commit"]
    assert result["persistence"]["git"]["status"] == "pushed"
    assert git(repo, "rev-parse", "HEAD").stdout.strip() != commit_a
    assert git(remote, "rev-parse", "refs/heads/main").stdout.strip() == commit_a
    assert "private.txt" not in git(remote, "ls-tree", "--name-only", "refs/heads/main").stdout
