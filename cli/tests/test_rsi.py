"""RSI lifecycle, immutable provenance, lossless boundaries, and stale source tests."""
import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_memory.cli import cli
from agent_memory.contracts_cli import execute_request as plan_api
from agent_memory.contracts_store import ContractError
from agent_memory.policy import MAX_BODY_CHARS, PolicyStore, content_revision
from agent_memory.rsi import execute_request
from agent_memory.rsi_schema import MAX_DATA_BYTES, MAX_REQUEST_BYTES, parse_json
from agent_memory.validator import validate_file
from agent_memory.writer import create_entry, update_entry


@pytest.fixture
def api(tmp_path):
    def run(request, **kwargs):
        return execute_request(request, tmp_path, no_git=True, **kwargs)
    return run


def make_plan(base, plan_id="example"):
    reviewed = plan_api({"action": "review", "template_id": "general"}, base, no_git=True)
    request = reviewed["create_example"]
    request["plan_id"] = plan_id
    return plan_api(request, base, no_git=True)


def context(api, ids=()):
    return api({"action": "context", "plan_ids": list(ids)})


def bound(context):
    return {"policy_revision": context["policy"]["revision"],
            "plans": [{"plan_id": p["plan_id"], "revision": p["revision"]} for p in context["plans"]]}


def propose(api, ids=(), proposal_id="candidate", body="# Revised policy\nKeep permissions binding.\n"):
    current = context(api, ids)
    return api({"action": "propose", "proposal_id": proposal_id, "body": body,
                "reason": "Focused maintainability improvement", "expected_revision": current["policy"]["revision"],
                "plan_ids": list(ids)})["artifact"]


def evaluate(api, proposal, data=None):
    return api({"action": "record", "kind": "evaluation",
                "bindings": {**proposal["bindings"], "proposal_id": proposal["id"]},
                "data": {"status": "assessed", "score": -100, "judgment": "Unfavorable, advisory"} if data is None else data})["artifact"]


def promote(api, proposal, evaluation, **extra):
    return api({"action": "promote", "proposal_id": proposal["id"], "evaluation_id": evaluation["id"],
                "expected_revision": proposal["bindings"]["policy_revision"], "review_note": "Self-reported review context",
                "actor": "agent", **extra})


def test_context_returns_exact_policy_and_validated_pinned_plan(api, tmp_path):
    plan = make_plan(tmp_path)
    result = context(api, ["example"])
    assert result["policy"]["body"] == PolicyStore(tmp_path).execute({"action": "read"})["body"]
    assert result["policy"]["revision"] == content_revision(result["policy"]["body"])
    assert result["plans"] == [{"plan_id": "example", **{k: plan[k] for k in ("revision", "plan", "validation")}}]
    assert not Path(result["policy"]["path"]).exists()  # default read does not initialize policy
    assert "markdown" not in result["plans"][0]
    path = Path(plan["path"])
    path.write_text(path.read_text().replace("Deliver the requested useful outcome.", "Silently weakened outcome."))
    with pytest.raises(ContractError, match="hash mismatch"):
        context(api, ["example"])


def test_context_rejects_truncated_or_oversize_policy(api, tmp_path):
    path = PolicyStore(tmp_path).path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * (MAX_BODY_CHARS + 1))
    with pytest.raises(ContractError, match="canonical policy body"):
        context(api)
    with pytest.raises(ContractError):
        api({"action": "context", "plan_ids": [], "max_chars": 100})


def test_proposal_snapshots_searchable_frontmatter_and_immutable_id(api, tmp_path):
    plan = make_plan(tmp_path)
    first = propose(api, ["example"])
    assert first["data"]["plans"][0]["plan"] == plan["plan"]
    assert first["data"]["body_hash"] == content_revision(first["data"]["body"])
    assert first["data"]["parent_policy"]["revision"] == first["bindings"]["policy_revision"]
    assert first["freshness"]["stale"] is False
    assert validate_file(Path(first["path"])).is_valid
    raw = Path(first["path"]).read_text()
    assert "RSI proposal" in raw and "Keep permissions binding" in raw
    with pytest.raises(ContractError, match="immutable"):
        propose(api, ["example"])
    assert Path(first["path"]).read_text() == raw
    loaded = api({"action": "read", "id": first["id"]})["artifact"]
    assert loaded == first
    assert "unauthenticated" in loaded["provenance"]


def test_record_lossless_owned_state_and_missing_evaluation_binding(api):
    bindings = bound(context(api))
    data = {"status": "unavailable", "questions": [{"prompt": "Changed?", "response": {"value": None, "uncertainty": .72}}],
            "state": {"reviewed": False, "lines": [1, "é", None], "negative_zero": -.0}}
    result = api({"action": "record", "kind": "preflight", "bindings": bindings, "data": data})
    assert result["artifact"]["data"] == data
    assert result["persistence"] == {"saved": True, "git": {"status": "disabled", "committed": False, "pushed": False}}
    assert api({"action": "read", "id": result["artifact"]["id"]})["artifact"]["data"] == data
    with pytest.raises(ContractError, match="requires proposal"):
        api({"action": "record", "kind": "evaluation", "bindings": bindings, "data": data})


def test_preview_apply_history_and_rollback_preserve_plan(api, tmp_path):
    plan = make_plan(tmp_path)
    original_plan = Path(plan["path"]).read_bytes()
    proposal = propose(api, ["example"])
    evaluation = evaluate(api, proposal)  # Negative semantic assessment does NOT forbid explicit promotion.
    preview = promote(api, proposal, evaluation)
    assert preview["applied"] is False and preview["changed"]
    assert "Keep permissions binding" in preview["diff"]
    assert "not verified authorization" in preview["review_guidance"]
    assert not Path(proposal["data"]["parent_policy"]["path"]).exists()
    applied = promote(api, proposal, evaluation, apply=True)
    assert applied["applied"] and applied["policy"]["write_success"]
    assert applied["policy"]["revision"] == proposal["data"]["body_hash"]
    assert Path(plan["path"]).read_bytes() == original_plan
    policy = PolicyStore(tmp_path)
    history = policy.execute({"action": "history"})
    assert len(history["snapshots"]) == 2 and len(history["events"]) == 1
    event = history["events"][0]
    assert f"proposal={proposal['id']}@{proposal['revision']}" in event["reason"]
    assert f"evaluation={evaluation['id']}@{evaluation['revision']}" in event["reason"]
    assert api({"action": "read", "id": evaluation["id"]})["artifact"]["freshness"]["stale"]
    rolled = policy.execute({"action": "rollback", "revision": proposal["bindings"]["policy_revision"],
                             "expected_revision": applied["policy"]["revision"], "actor": "human", "reason": "Explicit rollback"}, no_git=True)
    assert rolled["revision"] == proposal["bindings"]["policy_revision"]
    assert len(policy.execute({"action": "history"})["events"]) == 2
    assert not api({"action": "read", "id": evaluation["id"]})["artifact"]["freshness"]["stale"]


@pytest.mark.parametrize("data", [{}, {"status": "unavailable"}, {"status": "uncertain"}, {"status": True}, {"status": "assessed ", "score": 100}])
def test_unavailable_evaluation_never_promotes_but_remains_readable(api, data):
    proposal = propose(api)
    evaluation = evaluate(api, proposal, data)
    with pytest.raises(ContractError, match="unavailable"):
        promote(api, proposal, evaluation, apply=True)
    assert api({"action": "read", "id": evaluation["id"]})["artifact"]["data"] == data
    assert context(api)["policy"]["revision"] == proposal["bindings"]["policy_revision"]


def test_proposal_evaluation_identity_and_exact_source_binding(api, tmp_path):
    make_plan(tmp_path)
    first = propose(api, ["example"])
    second = propose(api, ["example"], "other")
    evaluation = evaluate(api, first)
    with pytest.raises(ContractError, match="exact proposal"):
        promote(api, second, evaluation)
    with pytest.raises(ContractError, match="do not match proposal"):
        api({"action": "record", "kind": "evaluation", "bindings": {**first["bindings"], "plans": [], "proposal_id": first["id"]},
             "data": {"status": "assessed"}})
    with pytest.raises(ContractError, match="exact proposal"):
        promote(api, first, first)


def test_stale_policy_refuses_record_and_promote_but_read_recomputes(api, tmp_path):
    proposal = propose(api)
    evaluation = evaluate(api, proposal)
    policy = PolicyStore(tmp_path)
    policy.execute({"action": "update", "body": "Changed directly through policy API", "expected_revision": proposal["bindings"]["policy_revision"],
                    "actor": "human", "reason": "New source"}, no_git=True)
    with pytest.raises(ContractError, match="stale"):
        evaluate(api, proposal)
    with pytest.raises(ContractError, match="stale"):
        promote(api, proposal, evaluation, apply=True)
    current = api({"action": "read", "id": proposal["id"]})["artifact"]
    assert current["revision"] == proposal["revision"] and current["data"] == proposal["data"]
    assert current["freshness"]["policy_status"] == "stale"
    assert current["freshness"]["current_policy_revision"] != proposal["bindings"]["policy_revision"]


def test_stale_deleted_or_corrupt_plans_are_inspectable_never_promotable(api, tmp_path):
    plan = make_plan(tmp_path)
    proposal = propose(api, ["example"])
    evaluation = evaluate(api, proposal)
    plan_api({"action": "update", "plan_id": "example", "revision": plan["revision"], "work_item_id": "work", "status": "in_progress"}, tmp_path, no_git=True)
    loaded = api({"action": "read", "id": proposal["id"]})["artifact"]
    assert loaded["freshness"]["plans"][0]["status"] == "stale"
    with pytest.raises(ContractError, match="stale"):
        promote(api, proposal, evaluation)
    Path(plan["path"]).unlink()
    loaded = api({"action": "read", "id": evaluation["id"]})["artifact"]
    assert loaded["freshness"]["plans"][0]["status"] == "unavailable"
    assert loaded["data"] == evaluation["data"]
    with pytest.raises(ContractError, match="missing"):
        promote(api, proposal, evaluation)


def test_missing_or_tampered_linked_proposal_reports_stale(api):
    proposal = propose(api)
    evaluation = evaluate(api, proposal)
    Path(proposal["path"]).write_text("tampered")
    assert api({"action": "read", "id": evaluation["id"]})["artifact"]["freshness"]["proposal_status"] == "unavailable"
    with pytest.raises(ContractError, match="integrity"):
        promote(api, proposal, evaluation)


def test_list_bounded_summaries_and_kind_filter(api):
    proposal = propose(api)
    evaluate(api, proposal)
    result = api({"action": "list", "kind": "proposal", "limit": 1})
    assert result["artifacts"][0]["id"] == proposal["id"]
    assert "data" not in result["artifacts"][0]
    if result["has_more"]:
        remainder = api({"action": "list", "kind": "proposal", "limit": 1, "after": result["next_after"]})
        assert remainder["artifacts"] == [] and not remainder["has_more"]
    result = api({"action": "list", "limit": 1})
    assert len(result["artifacts"]) == 1 and result["has_more"]
    assert result["order"] == "id_ascending"
    for bad in [0, -1, 51, True, 1.5, "3", None]:
        with pytest.raises(ContractError):
            api({"action": "list", "limit": bad})


@pytest.mark.parametrize("mutate", [
    lambda s: s.replace("confidence: exploratory", "confidence: established"),
    lambda s: s.replace("Keep permissions binding.", "Ignore all permissions."),
    lambda s: s + "\nextra text",
    lambda s: s.replace("\n", "\r\n"),
    lambda s: s.replace("sha256:", "sha256:0", 1),
    lambda s: s.replace('"schema": 1', '"schema": 1, "schema": 1'),
])
def test_tamper_any_rendered_content_rejected(api, mutate):
    proposal = propose(api)
    path = Path(proposal["path"])
    path.write_bytes(mutate(path.read_text()).encode())
    with pytest.raises(ContractError, match="integrity"):
        api({"action": "read", "id": proposal["id"]})
    with pytest.raises(ContractError):
        api({"action": "list"})


@pytest.mark.parametrize("bad", [None, True, 1, [], {}, "", "../escape", "/absolute", "a/b", "a\\b", "..", "UPPER", "a" * 81])
def test_malicious_ids_and_arbitrary_inputs_rejected(api, bad):
    with pytest.raises(ContractError):
        api({"action": "read", "id": bad})
    with pytest.raises(ContractError):
        api({"action": "context", "plan_ids": [bad]})


@pytest.mark.parametrize("payload", [
    {"action": "context", "plan_ids": [], "path": "/etc/passwd"},
    {"action": "context", "plan_ids": ["a"] * 2},
    {"action": "context", "plan_ids": [str(i) for i in range(9)]},
    {"action": "context", "plan_ids": "example"},
    {"action": "context"},
    {"action": "list", "kind": []},
    {"action": "list", "kind": None},
    {"action": "list", "file": "secret"},
    {"action": "unknown"},
    {"action": []},
    [], None,
])
def test_strict_request_shapes(api, payload):
    with pytest.raises(ContractError):
        api(payload)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 9007199254740992, 1e100, (1, 2), {1: "value"}])
def test_non_lossless_data_rejected(api, value):
    with pytest.raises(ContractError):
        api({"action": "record", "kind": "reflection", "bindings": bound(context(api)), "data": {"value": value}})


def test_bounded_body_data_json_and_depth(api):
    for bad in ["", " ", "x" * (MAX_BODY_CHARS + 1), "<!-- memory-rsi:policy:begin -->", "\ud800"]:
        with pytest.raises(ContractError):
            propose(api, body=bad)
    with pytest.raises(ContractError):
        api({"action": "record", "kind": "reflection", "bindings": bound(context(api)), "data": {"large": "x" * MAX_DATA_BYTES}})
    nested = []
    for _ in range(40):
        nested = [nested]
    with pytest.raises(ContractError, match="nesting"):
        api({"action": "record", "kind": "reflection", "bindings": bound(context(api)), "data": {"nested": nested}})
    with pytest.raises(ContractError):
        parse_json('{"x":1,"x":2}')
    with pytest.raises(ContractError):
        parse_json('{"x":1e999}')
    with pytest.raises(ContractError):
        parse_json('{"x": NaN}')


def test_cli_json_stdin_duplicate_and_unknown_fields(tmp_path):
    runner = CliRunner()
    args = ["rsi", "--base", str(tmp_path), "--no-git", "--request", "-"]
    result = runner.invoke(cli, args, input='{"action":"context","plan_ids":[]}')
    assert result.exit_code == 0 and json.loads(result.output)["policy"]["revision"].startswith("sha256:")
    for raw in ['{"action":"context","action":"list","plan_ids":[]}', '{"action":"record","data":{"x":1,"x":2}}',
                '{"action":"context","plan_ids":[],"file":"secret"}', "x" * (MAX_REQUEST_BYTES + 1), '{"x": NaN}']:
        result = runner.invoke(cli, args, input=raw)
        assert result.exit_code == 1 and json.loads(result.output)["ok"] is False
    result = runner.invoke(cli, args, input='{"SECRET-SENTINEL" broken}')
    assert "SECRET-SENTINEL" not in result.output


def test_writer_cannot_rewrite_artifact_by_path_alias_or_metadata(api, tmp_path):
    proposal = propose(api)
    path = Path(proposal["path"])
    original = path.read_bytes()
    alias = tmp_path / "ordinary.md"
    alias.symlink_to(path)
    hardlink = tmp_path / "hardlink.md"
    os.link(path, hardlink)
    for destination in (path, alias, hardlink):
        with pytest.raises(ValueError, match="immutable"):
            update_entry(destination, body="unauthorized generic rewrite")
    assert path.read_bytes() == original
    path.write_text("corrupted artifact without frontmatter")
    with pytest.raises(ValueError, match="immutable"):
        update_entry(path, tags=["replacement"])


@pytest.mark.parametrize("target", ["artifact", "directory", "policy", "plan", "contracts_lock", "policy_lock"])
def test_symlinks_and_hardlinks_fail_closed(api, tmp_path, target):
    plan = make_plan(tmp_path)
    proposal = propose(api, ["example"])
    policy = PolicyStore(tmp_path).path()
    if not policy.exists():
        policy.write_text(proposal["data"]["parent_policy"]["body"])
    paths = {"artifact": Path(proposal["path"]), "directory": Path(proposal["path"]).parent,
             "policy": policy, "plan": Path(plan["path"]), "contracts_lock": tmp_path / ".contracts.lock",
             "policy_lock": policy.parent / ".agent-policy.lock"}
    target_path = paths[target]
    backup = tmp_path / (target + ".backup")
    target_path.rename(backup)
    target_path.symlink_to(backup, target_is_directory=backup.is_dir())
    action = {"action": "context", "plan_ids": ["example"]} if target in ("plan", "policy") else {"action": "read", "id": proposal["id"]}
    with pytest.raises((ContractError, ValueError), match="symlink"):
        api(action)
    target_path.unlink()
    backup.rename(target_path)
    if target != "directory":
        os.link(target_path, backup)
        with pytest.raises((ContractError, ValueError), match="hard.link"):
            api(action)


def test_symlinked_base_ancestor_is_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ContractError, match="symlink"):
        execute_request({"action": "context", "plan_ids": []}, alias / "memory", no_git=True)


def test_revision_formats_and_strict_nested_bindings(api):
    current = bound(context(api))
    for bad in ["missing", "0" * 64, "sha256:" + "z" * 64, None]:
        with pytest.raises(ContractError):
            api({"action": "record", "kind": "preflight", "bindings": {**current, "policy_revision": bad}, "data": {}})
    for addition in [{"unknown": 1}, {"proposal_id": "../escape"}, {"plans": [{"plan_id": "p", "revision": "x", "path": "/secret"}]}]:
        with pytest.raises(ContractError):
            api({"action": "record", "kind": "preflight", "bindings": {**current, **addition}, "data": {}})


def test_list_pagination_retrieves_more_than_fifty_without_duplicate_ids(api):
    current = bound(context(api))
    saved = set()
    for i in range(53):
        saved.add(api({"action": "record", "kind": "reflection", "bindings": current,
                       "data": {"sequence": i}})["artifact"]["id"])
    page = api({"action": "list", "limit": 50})
    assert len(page["artifacts"]) == 50 and page["has_more"]
    tail = api({"action": "list", "limit": 50, "after": page["next_after"]})
    assert len(tail["artifacts"]) == 3 and not tail["has_more"] and tail["next_after"] is None
    found = [item["id"] for item in page["artifacts"] + tail["artifacts"]]
    assert len(found) == len(set(found)) and set(found) == saved
    for after in ["../escape", "", None, True]:
        with pytest.raises(ContractError):
            api({"action": "list", "after": after})


def test_generic_create_cannot_claim_or_overwrite_rsi_namespace(api, tmp_path):
    proposal = propose(api)
    path = Path(proposal["path"])
    original = path.read_bytes()
    for slug in ["rsi-candidate", "rsi-unclaimed"]:
        with pytest.raises(ValueError, match="reserved immutable"):
            create_entry(tmp_path, "agent", slug, "Generic entry", category="efforts", shared=True)
    assert path.read_bytes() == original
    assert not (path.parent / "rsi-unclaimed.md").exists()
    # A dangling destination symlink must not turn generic new into overwrite.
    dangling = path.parent / "rsi-dangling.md"
    destination = tmp_path / "secret-not-created"
    dangling.symlink_to(destination)
    with pytest.raises(ValueError, match="reserved immutable"):
        create_entry(tmp_path, "agent", "rsi-dangling", "Generic entry", category="efforts", shared=True)
    assert not destination.exists()


def test_atomic_replace_failure_leaves_no_artifact_or_temporary_file(api, tmp_path, monkeypatch):
    context(api)
    def fail(*_args):
        raise OSError("simulated atomic replace failure")
    monkeypatch.setattr("agent_memory.policy.os.replace", fail)
    with pytest.raises(OSError, match="atomic replace failure"):
        propose(api)
    directory = tmp_path / "shared/efforts"
    assert list(directory.iterdir()) == []


def test_full_assessment_report_shape_roundtrips_without_becoming_authority(api, tmp_path):
    make_plan(tmp_path)
    proposed = propose(api, ["example"])
    report = {
        "status": "assessed",
        "evaluator": {"rubric_revision": "sha256:" + "1" * 64, "requested_model": "jev-latest",
                      "endpoint": "https://api.typesafe.ai/v1/evaluate"},
        "state": {"baseline": {"body": proposed["data"]["parent_policy"]["body"], "revision": proposed["bindings"]["policy_revision"]},
                  "candidate": {"body": proposed["data"]["body"], "reason": proposed["data"]["reason"]},
                  "cases": proposed["data"]["plans"]},
        "questions": [{"id": "regression", "question": "Does this weaken a pinned requirement?", "type": "boolean"}],
        "response": {"answers": [{"id": "regression", "value": True, "uncertainty": 0.91}]},
        "interpretation": {"automatic_promotion": False, "disposition": "review-required", "permission_effect": "none"},
    }
    evaluated = evaluate(api, proposed, report)
    loaded = api({"action": "read", "id": evaluated["id"]})["artifact"]
    assert loaded["data"] == report
    assert "unauthenticated" in loaded["provenance"]
    # Exact reviewed state is stored; neither uncertainty nor low judgments are approval gates.
    assert promote(api, proposed, evaluated)["applied"] is False
