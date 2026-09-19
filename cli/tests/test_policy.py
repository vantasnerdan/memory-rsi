"""Focused policy persistence, safety, CLI, and Git regression tests."""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from click.testing import CliRunner

from agent_memory.policy import (
    BEGIN_MARKER,
    DEFAULT_POLICY,
    END_MARKER,
    POLICY_RELATIVE_PATH,
    PolicyError,
    PolicyStore,
    content_revision,
    managed_instructions,
)
from agent_memory.policy_cli import policy_cmd


@pytest.fixture
def store(tmp_path):
    return PolicyStore(tmp_path / "memory")


def read(store):
    return store.execute({"action": "read"}, no_git=True)


def change(store, body="# Rewards > gates\n\nRevised policy.\n", **extra):
    request = {"action": "update", "body": body, "expected_revision": read(store)["revision"], "actor": "agent", "reason": "Explicit focused test change"}
    request.update(extra)
    return store.execute(request, no_git=True)


def test_virtual_default_and_asset_contract(store):
    result = read(store)
    assert result["body"] == DEFAULT_POLICY
    assert DEFAULT_POLICY.startswith("# Rewards > gates")
    assert result["revision"] == content_revision(DEFAULT_POLICY)
    assert not result["exists"]
    assert not store.path().exists()
    assert "template" in DEFAULT_POLICY and "human review" in DEFAULT_POLICY
    assert "higher-priority" in DEFAULT_POLICY and "work-item IDs" in DEFAULT_POLICY


def test_update_history_and_rollback_survive_new_store(store):
    initial = read(store)["revision"]
    first = change(store)
    second = change(store, "# Another explicit version\n")
    fresh = PolicyStore(store.root)
    history = fresh.execute({"action": "history"}, no_git=True)
    assert len(history["snapshots"]) == 3
    assert len(history["events"]) == 2
    assert history["current_recorded"]
    snapshot = fresh.execute({"action": "history", "revision": first["revision"]}, no_git=True)
    assert snapshot["snapshot"]["body"].startswith("# Rewards")
    result = fresh.execute({"action": "rollback", "revision": initial, "expected_revision": second["revision"], "actor": "human", "reason": "Restore baseline"}, no_git=True)
    assert result["revision"] == initial
    assert result["write_success"]
    assert read(fresh)["body"] == DEFAULT_POLICY
    events = fresh.execute({"action": "history"}, no_git=True)["events"]
    assert events[-1]["action"] == "rollback"
    assert events[-1]["actor"] == "human"


def test_stale_updates_and_rollbacks_do_not_overwrite(store):
    original = read(store)["revision"]
    change(store)
    for action in ("update", "rollback"):
        request = {"action": action, "expected_revision": original, "actor": "agent", "reason": "Stale change"}
        request["body" if action == "update" else "revision"] = "new content" if action == "update" else original
        with pytest.raises(PolicyError, match="revision conflict"):
            store.execute(request, no_git=True)
    assert "Revised policy" in read(store)["body"]


def test_direct_human_edit_detected_and_preserved_in_history(store):
    change(store)
    direct = "Human direct edit\r\nwith exact newlines\r\n"
    store.path().write_bytes(direct.encode())
    assert read(store)["body"] == direct
    revision = read(store)["revision"]
    assert revision == content_revision(direct)
    change(store, "Successor\n")
    snapshot = store.execute({"action": "history", "revision": revision}, no_git=True)["snapshot"]
    assert snapshot["body"] == direct
    assert snapshot["actor"] == "external"  # do not pretend provenance is authenticated


def test_bounded_read_hashes_complete_direct_edit(store):
    store.path().parent.mkdir(parents=True)
    body = "a" * 40000
    store.path().write_text(body)
    result = store.execute({"action": "read", "max_chars": 123}, no_git=True)
    assert result["body"] == "a" * 123
    assert result["truncated"]
    assert result["revision"] == content_revision(body)


@pytest.mark.parametrize("invalid_request", [
    None, [], "read", {"action": []}, {"action": "unknown"},
    {"action": "read", "max_chars": True},
    {"action": "read", "max_chars": 0},
    {"action": "read", "max_chars": 32769},
    {"action": "read", "max_chars": "12"},
    {"action": "read", "human_approved": True},
    {"action": "history", "revision": 123},
])
def test_invalid_request_types(store, invalid_request):
    with pytest.raises(PolicyError):
        store.execute(invalid_request, no_git=True)


@pytest.mark.parametrize("field,value", [
    ("body", None), ("body", []), ("body", ""), ("body", "x" * 32769),
    ("body", "\ud800"), ("body", BEGIN_MARKER),
    ("actor", True), ("actor", "admin"), ("actor", []),
    ("reason", 123), ("reason", ""),
    ("expected_revision", None), ("expected_revision", True),
])
def test_invalid_update_fields(store, field, value):
    with pytest.raises(PolicyError):
        change(store, **{field: value})
    assert not store.path().exists()


def test_snapshot_tampering_is_detected(store):
    result = change(store)
    path = store._snapshot_path(result["revision"])
    data = json.loads(path.read_text())
    data["body"] = "tampered"
    path.write_text(json.dumps(data))
    for request in ({"action": "history"}, {"action": "rollback", "revision": result["revision"], "expected_revision": result["revision"], "actor": "agent", "reason": "Invalid restore"}):
        with pytest.raises(PolicyError, match="hash mismatch"):
            store.execute(request, no_git=True)


def test_unknown_rollback_revision_does_not_write(store):
    with pytest.raises(PolicyError, match="not found"):
        store.execute({"action": "rollback", "revision": content_revision("unknown"), "expected_revision": read(store)["revision"], "actor": "human", "reason": "Missing revision"}, no_git=True)
    assert not store.path().exists()


@pytest.mark.parametrize("component", ["shared", "shared/policies", "shared/policies/agent-policy.md", "shared/policies/.agent-policy-history"])
def test_policy_paths_cannot_escape_through_symlinks(tmp_path, component):
    root = tmp_path / "memory"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / component
    link.parent.mkdir(parents=True, exist_ok=True)
    if component.endswith(".md"):
        (outside / "policy.md").write_text("External unchanged")
        link.symlink_to(outside / "policy.md")
    else:
        link.symlink_to(outside, target_is_directory=True)
    store = PolicyStore(root)
    with pytest.raises(PolicyError, match="(escapes|symlinks)"):
        change(store)
    if (outside / "policy.md").exists():
        assert (outside / "policy.md").read_text() == "External unchanged"
    assert not (outside / "snapshots").exists()


def test_managed_preservation_idempotence_and_revision_checks(tmp_path):
    target = tmp_path / "AGENTS.md"
    prefix = "# Operator instructions\r\nPreserve exactly.\r\n\r\n"
    suffix = "\r\n\r\n# Extra local rules\r\nKeep this too.\r\n"
    target.write_bytes((prefix + BEGIN_MARKER + "\nold\n" + END_MARKER + suffix).encode())
    store = PolicyStore(tmp_path / "memory", (str(target),))
    before = target.read_bytes()
    proposal = store.execute({"action": "sync", "target": str(target)}, no_git=True)
    assert not proposal["applied"] and proposal["changed"]
    assert target.read_bytes() == before
    assert proposal["proposal"].startswith(prefix)
    assert proposal["proposal"].endswith(suffix)
    assert DEFAULT_POLICY.strip() in proposal["proposal"]
    assert "Policy revision: sha256:" in proposal["proposal"]
    request = {"action": "sync", "target": str(target), "apply": True, "expected_revision": proposal["expected_revision"], "expected_target_revision": proposal["expected_target_revision"], "actor": "human", "reason": "Reviewed proposed diff"}
    result = store.execute(request, no_git=True)
    assert result["applied"] and result["write_success"]
    second = store.execute({"action": "sync", "target": str(target)}, no_git=True)
    assert not second["changed"] and second["diff"] == ""
    assert target.read_bytes().startswith(prefix.encode())
    assert target.read_bytes().endswith(suffix.encode())
    with pytest.raises(PolicyError, match="target revision conflict"):
        store.execute(request, no_git=True)


def test_sync_new_target_and_policy_conflict(tmp_path):
    target = tmp_path / "AGENTS.md"
    store = PolicyStore(tmp_path / "memory", (str(target),))
    proposal = store.execute({"action": "sync", "target": str(target)}, no_git=True)
    assert proposal["expected_target_revision"] == "missing"
    request = {"action": "sync", "target": str(target), "apply": True, "expected_revision": proposal["expected_revision"], "expected_target_revision": "missing", "actor": "agent", "reason": "Review"}
    change(store)
    with pytest.raises(PolicyError, match="Policy revision conflict"):
        store.execute(request, no_git=True)
    assert not target.exists()
    request["expected_revision"] = read(store)["revision"]
    result = store.execute(request, no_git=True)
    assert result["applied"] and target.exists()


def test_target_edit_after_proposal_is_not_clobbered(tmp_path):
    target = tmp_path / "AGENTS.md"
    target.write_text("Original")
    store = PolicyStore(tmp_path / "memory", (str(target),))
    proposal = store.execute({"action": "sync", "target": str(target)}, no_git=True)
    target.write_text("A concurrent human edit")
    with pytest.raises(PolicyError, match="target revision conflict"):
        store.execute({"action": "sync", "target": str(target), "apply": True, "expected_revision": proposal["expected_revision"], "expected_target_revision": proposal["expected_target_revision"], "actor": "agent", "reason": "Stale diff"}, no_git=True)
    assert target.read_text() == "A concurrent human edit"


@pytest.mark.parametrize("body", [
    BEGIN_MARKER, END_MARKER, END_MARKER + "\n" + BEGIN_MARKER,
    BEGIN_MARKER + "\n" + BEGIN_MARKER + "\n" + END_MARKER,
    BEGIN_MARKER + "\n" + END_MARKER + "\n" + END_MARKER,
    "inline " + BEGIN_MARKER + "\n" + END_MARKER,
    BEGIN_MARKER + "\n" + END_MARKER + " inline",
    "<!-- memory-rsi:policy:begn -->\n",
])
def test_malformed_or_duplicate_markers_fail(body):
    with pytest.raises(PolicyError, match="(markers|Malformed)"):
        managed_instructions(body, DEFAULT_POLICY, content_revision(DEFAULT_POLICY))


@pytest.mark.parametrize("original", ["", "plain no final newline", "# Existing\n", "# Existing\n\n"])
def test_append_preserves_original_and_is_idempotent(original):
    result = managed_instructions(original, DEFAULT_POLICY, content_revision(DEFAULT_POLICY))
    assert result.startswith(original)
    assert managed_instructions(result, DEFAULT_POLICY, content_revision(DEFAULT_POLICY)) == result


def test_allowlist_is_operator_only_and_symlinks_rejected(tmp_path):
    target = tmp_path / "AGENTS.md"
    store = PolicyStore(tmp_path / "memory")
    with pytest.raises(PolicyError, match="allowlist"):
        store.execute({"action": "sync", "target": str(target)}, no_git=True)
    with pytest.raises(PolicyError, match="Unknown request fields"):
        store.execute({"action": "sync", "target": str(target), "instruction_files": [str(target)]}, no_git=True)
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("Do not touch")
    target.symlink_to(elsewhere)
    store = PolicyStore(tmp_path / "memory", (str(target),))
    with pytest.raises(PolicyError, match="symlinks"):
        store.execute({"action": "sync", "target": str(target)}, no_git=True)
    assert elsewhere.read_text() == "Do not touch"


def test_no_self_overwrite(tmp_path):
    target = tmp_path / "memory" / POLICY_RELATIVE_PATH
    store = PolicyStore(tmp_path / "memory", (str(target),))
    with pytest.raises(PolicyError, match="overwrite policy"):
        store.execute({"action": "sync", "target": str(target)}, no_git=True)


@pytest.mark.parametrize("extra", [{"apply": "yes"}, {"apply": True}, {"expected_revision": []}, {"expected_target_revision": True}, {"actor": True, "reason": "test"}, {"apply": True, "actor": "agent", "reason": "test", "expected_revision": 12}])
def test_sync_invalid_types_and_required_fields(tmp_path, extra):
    target = tmp_path / "AGENTS.md"
    store = PolicyStore(tmp_path / "memory", (str(target),))
    with pytest.raises(PolicyError):
        store.execute({"action": "sync", "target": str(target), **extra}, no_git=True)
    assert not target.exists()


def _concurrent_change(arguments):
    root, revision, number = arguments
    store = PolicyStore(root)
    try:
        store.execute({"action": "update", "body": f"Writer {number}\n", "expected_revision": revision, "actor": "agent", "reason": "Concurrency test"}, no_git=True)
        return "written"
    except PolicyError as exc:
        return exc.code


def test_concurrent_writers_have_one_winner(store):
    revision = read(store)["revision"]
    # Separate opens exercise flock's open-file-description ownership; threads
    # avoid requiring POSIX named semaphores in restricted CI environments.
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(_concurrent_change, [(str(store.root), revision, i) for i in range(4)]))
    assert results.count("written") == 1
    assert results.count("revision_conflict") == 3
    assert len(store.execute({"action": "history"}, no_git=True)["events"]) == 1


def test_atomic_replacement_keeps_mode_and_no_temp_files(store):
    change(store)
    store.path().chmod(0o600)
    change(store, "Replacement\n")
    assert store.path().stat().st_mode & 0o777 == 0o600
    assert not list(store.root.rglob("*.tmp"))


def test_failed_atomic_swap_preserves_previous_canonical(store, monkeypatch):
    import os

    change(store)
    before = store.path().read_bytes()
    replace = os.replace

    def fail_canonical(source, destination):
        if destination == store.path():
            raise OSError("Simulated interrupted canonical replacement")
        return replace(source, destination)

    monkeypatch.setattr("agent_memory.policy.os.replace", fail_canonical)
    with pytest.raises(OSError, match="interrupted"):
        change(store, "Must not partially replace canonical")
    assert store.path().read_bytes() == before
    assert not list(store.root.rglob("*.tmp"))
    assert len(store.execute({"action": "history"}, no_git=True)["events"]) == 1


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def git_store(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Policy Test")
    git(root, "config", "user.email", "policy-test@example.invalid")
    (root / "seed").write_text("Initial unrelated content")
    git(root, "add", "seed")
    git(root, "commit", "-m", "initial")
    return PolicyStore(root / "memory"), root


def test_git_commits_only_owned_files_and_no_remote_is_truthful(tmp_path):
    store, root = git_store(tmp_path)
    (root / "staged.txt").write_text("Unrelated staged change")
    git(root, "add", "staged.txt")
    (root / "unstaged.txt").write_text("Unrelated untracked change")
    initial = read(store)["revision"]
    result = store.execute({"action": "update", "body": "# Policy\n", "expected_revision": initial, "actor": "human", "reason": "Commit isolated policy change"})
    assert result["write_success"]
    assert result["git"]["status"] == "committed_no_remote"
    assert result["git"]["committed"] and not result["git"]["pushed"]
    names = git(root, "show", "--pretty=format:", "--name-only", "HEAD").splitlines()
    assert names and all(name.startswith("memory/shared/policies/") for name in names)
    assert not any(name.endswith(".lock") for name in names)
    assert git(root, "diff", "--cached", "--name-only") == "staged.txt"


def test_git_sync_uses_literal_owned_paths_only(tmp_path):
    store, root = git_store(tmp_path)
    target = root / "AGENTS*.md"
    unrelated = root / "AGENTSextra.md"
    unrelated.write_text("Unrelated staged instructions")
    git(root, "add", "AGENTSextra.md")
    store = PolicyStore(store.root, (str(target),))
    proposal = store.execute({"action": "sync", "target": str(target)}, no_git=True)
    result = store.execute({"action": "sync", "target": str(target), "apply": True, "actor": "human", "reason": "Reviewed literal target diff", "expected_revision": proposal["expected_revision"], "expected_target_revision": proposal["expected_target_revision"]})
    assert result["git"]["status"] == "committed_no_remote"
    assert git(root, "show", "--pretty=format:", "--name-only", "HEAD") == "AGENTS*.md"
    assert git(root, "diff", "--cached", "--name-only") == "AGENTSextra.md"


def test_policy_change_does_not_rewrite_template_or_instruction_files(store):
    template = store.root / "shared" / "templates" / "plan.md"
    template.parent.mkdir(parents=True)
    template.write_text("Pinned template content")
    change(store)
    assert template.read_text() == "Pinned template content"
    assert not (store.root / "AGENTS.md").exists()


def test_git_nondefault_branch_guard_and_override(tmp_path):
    store, root = git_store(tmp_path)
    git(root, "checkout", "-b", "feature")
    request = {"action": "update", "body": "Branch policy", "expected_revision": read(store)["revision"], "actor": "agent", "reason": "Intentional feature change"}
    with pytest.raises(PolicyError, match="non-default"):
        store.execute(request)
    assert not store.path().exists()
    result = store.execute(request, allow_non_main_branch=True)
    assert result["git"]["committed"]


def test_push_failure_reports_commit_and_write_success_separately(tmp_path):
    store, root = git_store(tmp_path)
    git(root, "remote", "add", "origin", str(tmp_path / "nonexistent-remote.git"))
    git(root, "config", "branch.main.remote", "origin")
    git(root, "config", "branch.main.merge", "refs/heads/main")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    result = store.execute({"action": "update", "body": "Persist and commit despite push failure", "expected_revision": read(store)["revision"], "actor": "agent", "reason": "Separate persistence from publication"})
    assert result["write_success"]
    assert result["git"]["status"] == "push_failed"
    assert result["git"]["committed"] and not result["git"]["pushed"]
    assert result["git"]["commit"] == git(root, "rev-parse", "HEAD")


@pytest.mark.parametrize("target_branch", ["main", "published-policy"])
def test_push_matching_configuration_publishes_only_selected_upstream(tmp_path, target_branch):
    store, root = git_store(tmp_path)
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare")
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-u", "origin", f"HEAD:refs/heads/{target_branch}")
    git(root, "checkout", "-b", "unrelated")
    git(root, "push", "origin", "unrelated")
    previous_unrelated = git(remote, "rev-parse", "refs/heads/unrelated")
    (root / "private.txt").write_text("Do not publish this unrelated branch")
    git(root, "add", "private.txt")
    git(root, "commit", "-m", "Unrelated private change")
    git(root, "checkout", "main")
    git(root, "config", "push.default", "matching")
    git(root, "config", "remote.origin.push", "refs/heads/unrelated:refs/heads/unrelated")
    git(root, "config", "push.followTags", "true")
    git(root, "tag", "-a", "private-note", "-m", "Do not automatically publish tags")
    result = store.execute({"action": "update", "body": "Publish selected policy branch only", "expected_revision": read(store)["revision"], "actor": "human", "reason": "Reviewed narrow publication"})
    assert result["git"]["status"] == "pushed"
    assert result["git"]["committed"] and result["git"]["pushed"]
    assert git(remote, "rev-parse", f"refs/heads/{target_branch}") == result["git"]["commit"]
    assert git(remote, "rev-parse", "refs/heads/unrelated") == previous_unrelated
    assert git(remote, "for-each-ref", "--format=%(refname)", "refs/tags") == ""


def test_remote_without_upstream_is_not_configured(tmp_path):
    store, root = git_store(tmp_path)
    git(root, "remote", "add", "origin", str(tmp_path / "not-used.git"))
    result = store.execute({"action": "update", "body": "Commit locally without configured tracking", "expected_revision": read(store)["revision"], "actor": "agent", "reason": "Missing upstream must not select an arbitrary destination"})
    assert result["write_success"] and result["git"]["committed"]
    assert result["git"]["status"] == "not_configured"
    assert not result["git"]["pushed"]


def test_local_upstream_is_not_pushed(tmp_path):
    store, root = git_store(tmp_path)
    git(root, "remote", "add", "origin", str(tmp_path / "not-used.git"))
    git(root, "branch", "local-target")
    previous = git(root, "rev-parse", "local-target")
    git(root, "config", "branch.main.remote", ".")
    git(root, "config", "branch.main.merge", "refs/heads/local-target")
    result = store.execute({"action": "update", "body": "Local upstream does not grant branch mutation", "expected_revision": read(store)["revision"], "actor": "agent", "reason": "Do not push into another local branch"})
    assert result["git"]["status"] == "committed_local_only"
    assert result["git"]["committed"] and not result["git"]["pushed"]
    assert git(root, "rev-parse", "local-target") == previous


@pytest.mark.parametrize("action", ["update", "rollback"])
def test_staged_policy_refused_before_any_policy_write(tmp_path, action):
    store, root = git_store(tmp_path)
    initial = read(store)["revision"]
    change(store)
    store.path().write_text("Staged-only policy that must remain recoverable")
    relative = str(store.path().relative_to(root))
    git(root, "add", "--", relative)
    staged = git(root, "rev-parse", ":" + relative)
    store.path().write_text("Different current worktree policy")
    before = store.path().read_bytes()
    history = {str(p): p.read_bytes() for p in store.root.rglob("*.json")}
    request = {"action": action, "expected_revision": read(store)["revision"], "actor": "human", "reason": "Do not discard a staged-only blob"}
    request["body" if action == "update" else "revision"] = "New explicit policy" if action == "update" else initial
    with pytest.raises(PolicyError, match="already has staged changes") as error:
        store.execute(request)
    assert error.value.code == "staged_changes"
    assert store.path().read_bytes() == before
    assert git(root, "rev-parse", ":" + relative) == staged
    assert {str(p): p.read_bytes() for p in store.root.rglob("*.json")} == history


def test_staged_managed_target_refused_before_content_write(tmp_path):
    store, root = git_store(tmp_path)
    target = root / "AGENTS.md"
    target.write_text("Staged-only operator instructions")
    git(root, "add", "AGENTS.md")
    staged = git(root, "rev-parse", ":AGENTS.md")
    target.write_text("Different worktree instructions")
    store = PolicyStore(store.root, (str(target),))
    proposal = store.execute({"action": "sync", "target": str(target)}, no_git=True)
    before = target.read_bytes()
    with pytest.raises(PolicyError, match="already has staged changes"):
        store.execute({"action": "sync", "target": str(target), "apply": True, "actor": "human", "reason": "Preserve staged target before synchronization", "expected_revision": proposal["expected_revision"], "expected_target_revision": proposal["expected_target_revision"]})
    assert target.read_bytes() == before
    assert git(root, "rev-parse", ":AGENTS.md") == staged


def test_git_failure_does_not_misreport_successful_write(tmp_path):
    store, root = git_store(tmp_path)
    git(root, "config", "user.email", "")
    git(root, "config", "user.name", "")
    result = store.execute({"action": "update", "body": "Persist despite Git failure", "expected_revision": read(store)["revision"], "actor": "agent", "reason": "Persistence is distinct from publication"})
    assert result["write_success"]
    assert result["git"]["status"] == "git_failed"
    assert not result["git"]["committed"]
    assert read(store)["body"] == "Persist despite Git failure"


def test_cli_stdin_registration_and_machine_readable_errors(tmp_path):
    runner = CliRunner()
    args = ["--request", "-", "--base", str(tmp_path / "memory"), "--no-git"]
    result = runner.invoke(policy_cmd, args, input='{"action":"read"}')
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["body"] == DEFAULT_POLICY
    assert policy_cmd.name == "policy"
    for raw in ('[]', '{"action":"read","max_chars":true}', '{"action":"read","action":"read"}', '{"action":NaN}', 'not JSON'):
        result = runner.invoke(policy_cmd, args, input=raw)
        assert result.exit_code == 1
        assert json.loads(result.output)["ok"] is False


def test_cli_instruction_allowlist_and_sync_flow(tmp_path):
    runner = CliRunner()
    target = tmp_path / "AGENTS.md"
    args = ["--request", "-", "--base", str(tmp_path / "memory"), "--no-git", "--instruction-file", str(target)]
    proposal = runner.invoke(policy_cmd, args, input=json.dumps({"action": "sync", "target": str(target)}))
    assert proposal.exit_code == 0, proposal.output
    data = json.loads(proposal.output)
    applied = runner.invoke(policy_cmd, args, input=json.dumps({"action": "sync", "target": str(target), "apply": True, "actor": "human", "reason": "Reviewed proposal", "expected_revision": data["expected_revision"], "expected_target_revision": data["expected_target_revision"]}))
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["applied"]
    assert DEFAULT_POLICY.strip() in target.read_text()
