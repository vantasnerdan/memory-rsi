"""Fresh-HOME lifecycle tests; no developer config, globals, or live memory."""
import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_memory.bootstrap import BootstrapStore
from agent_memory.bootstrap_cli import bootstrap_cmd
from agent_memory.bootstrap_fs import BootstrapError
from agent_memory.contracts_store import ContractStore
from agent_memory.plan_templates import review_template
from agent_memory.policy import DEFAULT_POLICY, POLICY_RELATIVE_PATH


@pytest.fixture(autouse=True)
def no_global_git(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "absent-gitconfig"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_CONFIG_COUNT"):
        monkeypatch.delenv(name, raising=False)


def git(base, *args):
    return subprocess.run(["git", "-C", str(base), *args], capture_output=True, text=True, check=True).stdout.strip()


def test_status_is_read_only_and_fresh_initialize_is_ready(tmp_path):
    base = tmp_path / "fresh/memory"
    store = BootstrapStore(base)
    report = store.execute({"action": "status"})
    assert report["state"] == "incomplete"
    assert not base.exists()
    result = store.execute({"action": "initialize"})
    assert result["ready"]
    assert result["git"]["committed"] and not result["git"]["pushed"]
    assert len(result["created"]) == 3
    assert git(base, "config", "--local", "user.name") == "Memory Bootstrap"
    assert git(base, "config", "--local", "user.email") == "memory-bootstrap@localhost"
    assert not git(base, "remote")
    assert (base / POLICY_RELATIVE_PATH).read_text() == DEFAULT_POLICY
    assert review_template(ContractStore(base), "coding")["source"] == "shared"
    before = {p: p.read_bytes() for p in base.rglob("*.md")}
    head = git(base, "rev-parse", "HEAD")
    again = store.execute({"action": "initialize"})
    assert again["ready"] and not again["changed"]
    assert again["created"] == []
    assert git(base, "rev-parse", "HEAD") == head
    assert all(p.read_bytes() == data for p, data in before.items())
    assert not (Path.home() / ".gitconfig").exists()


def test_no_git_is_file_only_and_portable(tmp_path):
    base = tmp_path / "memory"
    result = BootstrapStore(base, "assistant").initialize(no_git=True)
    assert result["ready"] and result["git"]["state"] == "disabled"
    assert not (base / ".git").exists()
    assert (base / "assistant/atlas").is_dir()
    for path in base.rglob("*.md"):
        raw = path.read_text()
        assert "/home/dan" not in raw and "session-" not in raw


def test_preserve_existing_policy_and_custom_template(tmp_path):
    base = tmp_path / "memory"
    store = BootstrapStore(base)
    store.initialize(no_git=True)
    policy = base / POLICY_RELATIVE_PATH
    policy.write_bytes(b"# Human policy\r\nKeep exact bytes.\r\n")
    template = base / "shared/templates/coding.md"
    # Existing malformed/custom content is preserved, but readiness does not lie.
    template.write_bytes(b"# Entirely custom template\r\n")
    result = store.initialize(no_git=True)
    assert not result["ready"] and not result["changed"]
    assert policy.read_bytes() == b"# Human policy\r\nKeep exact bytes.\r\n"
    assert template.read_bytes() == b"# Entirely custom template\r\n"


@pytest.mark.parametrize("body,ready", [
    pytest.param(b"x" * 32768, True, id="ascii-at-character-limit"),
    pytest.param(b"x" * 32769, False, id="ascii-over-character-limit"),
    pytest.param("😀".encode("utf-8") * 32768, True, id="unicode-at-byte-and-character-limits"),
    pytest.param("😀".encode("utf-8") * 32769, False, id="unicode-over-byte-limit"),
    pytest.param(b"\xffinvalid-utf8", False, id="invalid-utf8"),
    pytest.param(b" \n\t", False, id="empty-policy"),
])
def test_policy_readiness_matches_prompt_limits_and_preserves_bytes(tmp_path, body, ready):
    base = tmp_path / "memory"
    store = BootstrapStore(base)
    store.initialize(no_git=True)
    policy = base / POLICY_RELATIVE_PATH
    policy.write_bytes(body)
    for report in (store.status(no_git=True), store.initialize(no_git=True)):
        assert report["ready"] is ready
        step = next(item for item in report["steps"] if item["id"] == "policy")
        assert step["state"] == ("ready" if ready else "incomplete")
        assert bool(step["error"]) is not ready
        assert policy.read_bytes() == body


def test_existing_branch_index_and_identity_untouched(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Existing User")
    git(repo, "config", "user.email", "user@example.invalid")
    (repo / "existing.txt").write_text("initial")
    git(repo, "add", "existing.txt")
    git(repo, "commit", "-m", "existing")
    git(repo, "checkout", "-b", "work-in-progress")
    (repo / "existing.txt").write_text("staged unrelated")
    git(repo, "add", "existing.txt")
    before = git(repo, "diff", "--cached")
    head = git(repo, "rev-parse", "HEAD")
    result = BootstrapStore(repo / "memory").initialize()
    assert result["ready"] and not result["git"]["committed"]
    assert git(repo, "diff", "--cached") == before
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "branch", "--show-current") == "work-in-progress"
    assert git(repo, "config", "user.name") == "Existing User"


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory", "hardlink"])
def test_unsafe_seed_destination_rejected_before_writes(tmp_path, kind):
    base = tmp_path / "memory"
    target = base / POLICY_RELATIVE_PATH
    target.parent.mkdir(parents=True)
    if kind == "symlink":
        target.symlink_to(tmp_path / "missing")
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "directory":
        target.mkdir()
    else:
        other = tmp_path / "other"
        other.write_text("keep")
        os.link(other, target)
    with pytest.raises(BootstrapError):
        BootstrapStore(base).initialize(no_git=True)
    assert not (base / "shared/templates").exists()


def test_symlink_base_and_traversal_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(BootstrapError):
        BootstrapStore(link / "memory")
    with pytest.raises(BootstrapError):
        BootstrapStore(tmp_path / "child/../memory")
    with pytest.raises(ValueError):
        BootstrapStore(real, "../escape")


def test_instruction_sync_requires_allowlist_and_explicit_cas(tmp_path):
    base = tmp_path / "memory"
    target = tmp_path / "AGENTS.md"
    original = b"# Operator content\r\nKeep unchanged.\r\n"
    target.write_bytes(original)
    store = BootstrapStore(base, instruction_files=(str(target),))
    store.initialize(no_git=True)
    assert target.read_bytes() == original
    with pytest.raises(ValueError, match="allowlist"):
        BootstrapStore(base).execute({"action": "sync_instructions", "target": str(target)})
    preview = store.execute({"action": "sync_instructions", "target": str(target)})
    assert target.read_bytes() == original
    request = {"action": "sync_instructions", "target": str(target), "apply": True,
               "expected_revision": preview["expected_revision"], "expected_target_revision": preview["expected_target_revision"],
               "actor": "human", "reason": "Explicit first-run sync"}
    result = store.execute(request)
    assert result["applied"]
    assert target.read_bytes().startswith(original)
    assert result["git"]["pushed"] is False
    with pytest.raises(ValueError, match="revision conflict"):
        store.execute(request)


def test_cli_json_stdin_inline_errors_and_status(tmp_path):
    runner = CliRunner()
    base = tmp_path / "memory"
    result = runner.invoke(bootstrap_cmd, ["--base", str(base), "--request", '{"action":"status"}'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["state"] == "incomplete"
    assert not base.exists()
    result = runner.invoke(bootstrap_cmd, ["--base", str(base), "--no-git", "--request", "-"], input='{"action":"initialize"}')
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ready"]
    for bad in ['[]', '{"action":"status","action":"initialize"}', '{"action":"status","instruction_files":[]}']:
        result = runner.invoke(bootstrap_cmd, ["--base", str(base), "--request", bad])
        assert result.exit_code == 1
        assert json.loads(result.output)["ok"] is False


def test_existing_repository_without_identity_stays_incomplete(tmp_path):
    base = tmp_path / "memory"
    base.mkdir()
    git(base, "init", "-b", "feature")
    report = BootstrapStore(base).initialize()
    assert not report["ready"]
    step = next(item for item in report["steps"] if item["id"] == "git")
    assert not step["identity_configured"]
    assert "git config --local" in step["remedy"]
    assert git(base, "branch", "--show-current") == "feature"
    result = subprocess.run(["git", "-C", str(base), "config", "--local", "--get", "user.name"], capture_output=True)
    assert result.returncode == 1


def test_contract_fifo_lock_refused_without_hanging(tmp_path):
    base = tmp_path / "memory"
    base.mkdir()
    os.mkfifo(base / ".contracts.lock")
    with pytest.raises(BootstrapError, match="Special file"):
        BootstrapStore(base).initialize(no_git=True)
    assert not (base / "shared").exists()


def test_missing_git_is_actionable_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_memory.bootstrap.shutil.which", lambda _: None)
    base = tmp_path / "memory"
    store = BootstrapStore(base)
    assert store.status()["state"] == "incomplete"
    with pytest.raises(BootstrapError, match="--no-git"):
        store.initialize()
    assert not base.exists()
    assert store.initialize(no_git=True)["ready"]
