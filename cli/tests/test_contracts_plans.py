"""Contract lifecycle, strict schemas, pinning, scoped collaboration, and CLI tests."""
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from agent_memory.contracts_cli import execute_request, plan_cmd
from agent_memory.contracts_store import ContractError, ContractStore
from agent_memory.plan_templates import BUILTINS
from agent_memory.validator import validate_file


@pytest.fixture
def api(tmp_path):
    def run(request, actor="parent"):
        return execute_request(request, tmp_path, actor=actor, no_git=True)
    return run


def create(api, template="general", plan_id="example"):
    review = api({"action": "review", "template_id": template})
    request = review["create_example"]
    request["plan_id"] = plan_id
    return api(request)


def update(api, plan, **fields):
    return api({"action": "update", "plan_id": plan["plan"]["plan_id"], "revision": plan["revision"],
                "work_item_id": "work", **fields})


def report(req="outcome", evidence_id="result"):
    return {"id": evidence_id, "requirement_id": req, "summary": "Observed useful result", "reference": "test output/artifact"}


def review(evidence_id="result", verdict="accepted"):
    return {"evidence_id": evidence_id, "verdict": verdict, "note": "Reviewed against requirement"}


def test_discovery_review_snapshot_and_memory_frontmatter(api):
    listing = api({"action": "templates"})
    assert {i["template_id"] for i in listing["templates"]} == {"general", "coding"}
    assert all(t["title"] and t["purpose"] for t in listing["templates"])
    coding = api({"action": "review", "template_id": "coding"})
    assert len(coding["revision"]) == 64
    assert coding["template"] == BUILTINS["coding"]
    assert "LSP" in str(coding["template"])
    assert "SRP" in str(coding["template"])
    assert "GitOps" in str(coding["template"])
    result = create(api, "coding")
    assert result["plan"]["template"]["content"] == coding["template"]
    assert result["plan"]["template"]["revision"] == coding["revision"]
    assert validate_file(Path(result["path"])).is_valid
    assert "Rewards > gates" in Path(result["path"]).read_text().split("# ", 1)[1].split("## Purpose", 1)[0]
    data = yaml.safe_load(Path(result["path"]).read_text().split("---")[1])
    assert data["contract"]["kind"] == "plan"
    assert data["author"] == "parent"
    assert not api({"action": "validate", "plan_id": "example"})["complete"]


def test_lifecycle_report_review_and_completion(api):
    plan = create(api)
    with pytest.raises(ContractError, match="cannot complete"):
        update(api, plan, status="complete")
    reported = update(api, plan, evidence=[report(), report("verification", "tests")], status="in_progress")
    progress = reported["validation"]["work_items"][0]
    assert progress["reported_evidence"] == 2
    assert progress["reviewed_requirements"] == []
    with pytest.raises(ContractError, match="cannot complete"):
        update(api, reported, status="complete")
    done = update(api, reported, reviews=[review(), review("tests")], status="complete")
    assert done["validation"]["complete"]
    loaded = api({"action": "read", "plan_id": "example"})
    assert loaded["plan"] == done["plan"]
    assert "Review accepted by parent" in loaded["markdown"]
    assert api({"action": "validate", "plan_id": "example"})["complete"]


def test_exception_needs_explicit_acceptance_and_audit(api):
    plan = create(api)
    exception = {"id": "unavailable", "requirement_id": "verification", "reason": "No runtime",
                 "alternative": "Static verification; runtime check remains unavailable"}
    reported = update(api, plan, evidence=[report()], exceptions=[exception])
    with pytest.raises(ContractError, match="cannot complete"):
        update(api, reported, reviews=[review()], status="complete")
    request = {"action": "update", "plan_id": "example", "revision": reported["revision"], "work_item_id": "work",
               "reviews": [review()], "exception_reviews": [{"exception_id": "unavailable", "verdict": "accepted",
                                                               "note": "Capability limitation accepted with substitute"}],
               "status": "complete"}
    accepted = api(request, actor="reviewer-agent")
    exception = accepted["plan"]["work_items"][0]["exceptions"][0]
    assert exception["reported_by"] == "parent"
    assert exception["reviews"][0]["reviewed_by"] == "reviewer-agent"
    assert accepted["validation"]["complete"]


def test_rejected_exception_does_not_satisfy_requirement(api):
    plan = update(api, create(api), evidence=[report()], exceptions=[
        {"id": "skip", "requirement_id": "verification", "reason": "Skip", "alternative": "None available"}])
    with pytest.raises(ContractError, match="cannot complete"):
        update(api, plan, reviews=[review()], exception_reviews=[
            {"exception_id": "skip", "verdict": "rejected", "note": "Run the check"}], status="complete")


def test_cannot_self_accept_new_report_in_same_operation(api):
    plan = create(api)
    with pytest.raises(ContractError, match="review target"):
        update(api, plan, evidence=[report()], reviews=[review()])
    assert api({"action": "read", "plan_id": "example"})["revision"] == plan["revision"]


def test_stale_revision_atomic_failure_and_duplicate_creation(api):
    first = create(api)
    second = update(api, first, status="in_progress")
    with pytest.raises(ContractError, match="stale"):
        update(api, first, evidence=[report()])
    with pytest.raises(ContractError, match="stale"):
        create(api)
    with pytest.raises(ContractError, match="duplicate"):
        update(api, second, evidence=[report(), report()])
    assert api({"action": "read", "plan_id": "example"})["revision"] == second["revision"]


def test_shared_agents_and_scoped_items(api):
    req = api({"action": "review", "template_id": "general"})["create_example"]
    req["task"]["work_items"] = [
        {"id": "build", "title": "Build", "owner": "child-a", "requirement_ids": ["outcome"]},
        {"id": "check", "title": "Check", "owner": "child-b", "requirement_ids": ["verification"]},
    ]
    plan = api(req)
    updated = api({"action": "update", "plan_id": "my-plan", "revision": plan["revision"], "work_item_id": "build",
                   "evidence": [report()]}, actor="child-a")
    assert updated["plan"]["work_items"][1] == plan["plan"]["work_items"][1]
    assert updated["plan"]["work_items"][0]["evidence"][0]["reported_by"] == "child-a"
    with pytest.raises(ContractError, match="scope"):
        api({"action": "update", "plan_id": "my-plan", "revision": updated["revision"], "work_item_id": "check",
             "evidence": [report()]}, actor="child-b")
    with pytest.raises(ContractError, match="stale"):
        api({"action": "update", "plan_id": "my-plan", "revision": plan["revision"], "work_item_id": "check",
             "status": "in_progress"}, actor="child-b")


def test_edit_template_and_pin_old_plan(api):
    old = api({"action": "review", "template_id": "general"})
    plan = create(api)
    modified = deepcopy(old["template"])
    modified["achievements"][0]["description"] = "Changed requirement for future plans"
    saved = api({"action": "save_template", "template_id": "general", "revision": old["revision"], "template": modified})
    assert saved["revision"] != old["revision"]
    assert api({"action": "read", "plan_id": "example"})["plan"]["template"] == plan["plan"]["template"]
    with pytest.raises(ContractError, match="stale"):
        api(old["create_example"])
    with pytest.raises(ContractError, match="stale"):
        api({"action": "save_template", "template_id": "general", "revision": old["revision"], "template": modified})
    new = create(api, plan_id="new-plan")
    assert new["plan"]["template"]["content"] == modified
    assert validate_file(Path(saved["path"])).is_valid


def test_template_creation_revision_and_metadata_preservation(api):
    template = deepcopy(BUILTINS["general"])
    created = api({"action": "save_template", "template_id": "team", "revision": None, "template": template})
    path = Path(created["path"])
    raw = path.read_text()
    path.write_text(raw.replace("category: efforts", "custom_metadata:\n  nested: kept\ncategory: efforts"))
    current = api({"action": "review", "template_id": "team"})
    assert current["revision"] != created["revision"]
    template["purpose"] = "New team purpose"
    saved = api({"action": "save_template", "template_id": "team", "revision": current["revision"], "template": template})
    assert "nested: kept" in Path(saved["path"]).read_text()
    assert next(t for t in api({"action": "templates"})["templates"] if t["template_id"] == "team")["source"] == "shared"
    with pytest.raises(ContractError):
        api({"action": "save_template", "template_id": "new", "template": template})


def test_additive_task_requirements_and_no_silent_weakening(api):
    req = api({"action": "review", "template_id": "general"})["create_example"]
    req["task"]["achievements"] = [{"id": "accessibility", "description": "Keyboard works", "evidence": "Keyboard interaction check"}]
    with pytest.raises(ContractError, match="cover all"):
        api(req)
    req["task"]["work_items"][0]["requirement_ids"].append("accessibility")
    plan = api(req)
    with pytest.raises(ContractError, match="unknown"):
        update(api, plan, template={})
    with pytest.raises(ContractError, match="unknown"):
        update(api, plan, task={"good": "Less strict"})
    req["task"]["achievements"][0]["id"] = "outcome"
    with pytest.raises(ContractError, match="cannot replace"):
        api(req)


def test_detect_tampered_pinned_snapshot(api):
    plan = create(api)
    path = Path(plan["path"])
    raw = path.read_text()
    path.write_text(raw.replace("Deliver the requested useful outcome.", "Weakened outcome."))
    with pytest.raises(ContractError, match="hash mismatch"):
        api({"action": "validate", "plan_id": "example"})


@pytest.mark.parametrize("bad", [None, True, 1, [], {}, "", "../escape", "/absolute", "a/b", "a\\b", "..", "UPPER", "a" * 81])
def test_invalid_path_ids(api, bad):
    with pytest.raises(ContractError):
        api({"action": "review", "template_id": bad})
    with pytest.raises(ContractError):
        api({"action": "read", "plan_id": bad})


@pytest.mark.parametrize("target", ["shared", "shared/plans", "shared/plans/example.md", ".contracts.lock"])
def test_reject_symlink_paths(tmp_path, target):
    base, outside = tmp_path / "memory", tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    link = base / target
    link.parent.mkdir(parents=True, exist_ok=True)
    if target.endswith(".md") or target == ".contracts.lock":
        destination = outside / "victim"
        destination.write_text("unchanged")
    else:
        destination = outside
    link.symlink_to(destination)
    with pytest.raises((ContractError, OSError)):
        if target == ".contracts.lock":
            execute_request({"action": "templates"}, base, no_git=True)
        else:
            execute_request({"action": "read", "plan_id": "example"}, base, no_git=True)
    if destination.is_file():
        assert destination.read_text() == "unchanged"


def test_reject_symlinked_base_and_template(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ContractError, match="symlink"):
        execute_request({"action": "templates"}, link, no_git=True)
    (target / "shared/templates").mkdir(parents=True)
    (target / "shared/templates/general.md").symlink_to(tmp_path / "missing")
    with pytest.raises(ContractError, match="symlink"):
        execute_request({"action": "review", "template_id": "general"}, target, no_git=True)


@pytest.mark.parametrize("payload", [None, [], True, 3, "templates", {}, {"action": []},
    {"action": "templates", "surprise": 1}, {"action": "unknown"}])
def test_invalid_top_level_shapes(api, payload):
    with pytest.raises(ContractError):
        api(payload)


@pytest.mark.parametrize("key,value", [("task", None), ("task", []), ("template_revision", True),
    ("template_id", {}), ("plan_id", []), ("extra", 1)])
def test_invalid_create_types(api, key, value):
    req = api({"action": "review", "template_id": "general"})["create_example"]
    req[key] = value
    with pytest.raises(ContractError):
        api(req)


@pytest.mark.parametrize("key,value", [("title", 9), ("purpose", []), ("good", {}),
    ("work_items", {}), ("work_items", [None]), ("achievements", "bad"),
    ("validation", [{"id": [], "description": "ok", "evidence": "ok"}]),
    ("steps", [False]), ("boundaries", []), ("unknown", "value")])
def test_invalid_task_types(api, key, value):
    req = api({"action": "review", "template_id": "general"})["create_example"]
    req["task"][key] = value
    with pytest.raises(ContractError):
        api(req)


@pytest.mark.parametrize("fields", [{"status": []}, {"status": "done"}, {"evidence": None},
    {"evidence": [None]}, {"evidence": [dict(report(), reference=False)]},
    {"reviews": [{"evidence_id": "x", "verdict": [], "note": "x"}]},
    {"exceptions": {}}, {"exceptions": [{"id": "x", "requirement_id": "outcome", "reason": [], "alternative": "ok"}]},
    {"exception_reviews": None}, {"work_item_id": []}, {"revision": 1}, {}])
def test_invalid_scoped_update_types(api, fields):
    plan = create(api)
    with pytest.raises(ContractError):
        update(api, plan, **fields)
    assert api({"action": "read", "plan_id": "example"})["revision"] == plan["revision"]


@pytest.mark.parametrize("raw", ['null', '[]', '{"action": NaN}', '{"action":"templates","action":"review"}', '{bad'])
def test_cli_strict_json(tmp_path, raw):
    result = CliRunner().invoke(plan_cmd, ["--request", "-", "--base", str(tmp_path), "--no-git"], input=raw)
    assert result.exit_code == 1
    assert json.loads(result.output)["saved"] is False


def test_cli_stdin_inline_and_actor(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_ID", "child-session")
    args = ["--request", "-", "--base", str(tmp_path), "--no-git"]
    result = CliRunner().invoke(plan_cmd, args, input='{"action":"review","template_id":"general"}')
    assert result.exit_code == 0, result.output
    req = json.loads(result.output)["create_example"]
    created = CliRunner().invoke(plan_cmd, args, input=json.dumps(req))
    assert created.exit_code == 0, created.output
    assert json.loads(created.output)["plan"]["created_by"] == "child-session"
    inline = CliRunner().invoke(plan_cmd, ["--request", '{"action":"read","plan_id":"my-plan"}', "--base", str(tmp_path)])
    assert inline.exit_code == 0


def test_interprocess_optimistic_concurrency(api, tmp_path):
    plan = create(api)
    # Independent processes and pipe start signals avoid multiprocessing's
    # platform-specific shared-memory semaphore dependency.
    script = '''
import sys
from agent_memory.contracts_cli import execute_request
from agent_memory.contracts_store import ContractError
sys.stdin.readline()
try:
    result = execute_request({"action":"update", "plan_id":"example", "revision":sys.argv[2],
                              "work_item_id":"work", "status":"in_progress"},
                             sys.argv[1], actor=sys.argv[3], no_git=True)
    print(result["revision"])
except ContractError as exc:
    print(str(exc))
'''
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    workers = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path), plan["revision"], f"child-{i}"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=env) for i in range(4)]
    try:
        for worker in workers:
            worker.stdin.write("go\n")
            worker.stdin.flush()
        results = []
        for worker in workers:
            output, errors = worker.communicate(timeout=15)
            assert worker.returncode == 0, errors
            results.append(output)
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.communicate()
    assert sum("stale revision" in r for r in results) == 3
    assert len({r for r in results if "stale" not in r}) == 1
    assert api({"action": "read", "plan_id": "example"})["validation"]["valid"]


def test_atomic_replace_failure_keeps_original(api, monkeypatch):
    import agent_memory.contracts_store as storage
    plan = create(api)
    path = Path(plan["path"])
    before = path.read_bytes()
    def fail(*args):
        raise OSError("injected rename failure")
    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError, match="rename failure"):
        update(api, plan, status="in_progress")
    assert path.read_bytes() == before
    assert list(path.parent.glob(".contract-*")) == []


def test_store_preserves_unknown_plan_metadata(api):
    plan = create(api)
    path = Path(plan["path"])
    path.write_text(path.read_text().replace("category: efforts", "custom_field: retained\ncategory: efforts"))
    current = api({"action": "read", "plan_id": "example"})
    saved = update(api, current, status="in_progress")
    assert "custom_field: retained" in Path(saved["path"]).read_text()


def test_store_rejects_hardlinks(api, tmp_path):
    plan = create(api)
    (tmp_path / "alias").hardlink_to(Path(plan["path"]))
    with pytest.raises(ContractError, match="hard-linked"):
        api({"action": "read", "plan_id": "example"})


def test_store_rejects_unknown_directory(tmp_path):
    with pytest.raises(ContractError):
        ContractStore(tmp_path).path("../escape", "entry")


def test_revisions_hash_exact_file_bytes(api):
    import hashlib
    plan = create(api)
    path = Path(plan["path"])
    windows_bytes = path.read_bytes().replace(b"\n", b"\r\n")
    path.write_bytes(windows_bytes)
    current = api({"action": "read", "plan_id": "example"})
    assert current["revision"] == hashlib.sha256(windows_bytes).hexdigest()
    assert current["revision"] != plan["revision"]
    with pytest.raises(ContractError, match="stale"):
        update(api, plan, status="in_progress")


@pytest.mark.parametrize("key,value", [("title", None), ("purpose", False), ("good", []),
    ("achievements", {}), ("achievements", [True]), ("validation", []),
    ("steps", [None]), ("boundaries", "not-a-list"), ("unknown", 1)])
def test_invalid_template_shapes(api, key, value):
    template = deepcopy(BUILTINS["general"])
    template[key] = value
    with pytest.raises(ContractError):
        api({"action": "save_template", "template_id": "team", "revision": None, "template": template})


@pytest.mark.parametrize("key,value", [("id", []), ("title", None), ("owner", {}),
    ("requirement_ids", {}), ("requirement_ids", [[]]), ("requirement_ids", ["outcome", "outcome"]),
    ("requirement_ids", ["unknown"]), ("status", "complete")])
def test_invalid_work_item_shapes(api, key, value):
    req = api({"action": "review", "template_id": "general"})["create_example"]
    req["task"]["work_items"][0][key] = value
    with pytest.raises(ContractError):
        api(req)


def test_pending_exception_blocks_completion_even_with_evidence(api):
    plan = update(api, create(api), evidence=[report(), report("verification", "tests")], exceptions=[
        {"id": "pending", "requirement_id": "verification", "reason": "Unreviewed limitation", "alternative": "Review its impact"}])
    with pytest.raises(ContractError, match="cannot complete"):
        update(api, plan, reviews=[review(), review("tests")], status="complete")


def test_review_reversal_requires_explicit_reopening(api):
    plan = update(api, create(api), evidence=[report(), report("verification", "tests")])
    done = update(api, plan, reviews=[review(), review("tests")], status="complete")
    with pytest.raises(ContractError, match="cannot complete"):
        update(api, done, reviews=[review("tests", "rejected")])
    reopened = update(api, done, reviews=[review("tests", "rejected")], status="in_progress")
    assert not reopened["validation"]["complete"]
    assert len(reopened["plan"]["work_items"][0]["evidence"][1]["reviews"]) == 2
