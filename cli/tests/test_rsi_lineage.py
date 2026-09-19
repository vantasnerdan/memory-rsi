"""Reviewed P1: stale transitive source evidence cannot reach policy adoption."""
import os
from collections import Counter
from pathlib import Path

import pytest

from agent_memory.contracts_cli import execute_request as plan_api
from agent_memory.contracts_store import ContractError
from agent_memory.policy import PolicyStore
from agent_memory.rsi import execute_request
from agent_memory.rsi_schema import artifact_revision
from agent_memory.rsi_store import RSIStore, render_artifact
from .test_rsi_corpus import binding, evaluate, make_plan, propose, record


@pytest.fixture
def api(tmp_path):
    return lambda request: execute_request(request, tmp_path, no_git=True)


def plan_record(api, plan, *, kind="mapping", artifacts=()):
    bound = binding(api, artifacts)
    bound["plans"] = [{"plan_id": plan["plan"]["plan_id"], "revision": plan["revision"]}]
    return api({"action": "record", "kind": kind, "bindings": bound,
                "data": {"status": "assessed", "feature": "selected evidence"}})["artifact"]


def update_plan(tmp_path, plan):
    return plan_api({"action": "update", "plan_id": plan["plan"]["plan_id"], "revision": plan["revision"],
                     "work_item_id": "work", "status": "in_progress"}, tmp_path, no_git=True)


def promotion(proposal, evaluation):
    return {"action": "promote", "proposal_id": proposal["id"], "evaluation_id": evaluation["id"],
            "expected_revision": proposal["bindings"]["policy_revision"], "actor": "human", "review_note": "Explicit review", "apply": True}


@pytest.mark.parametrize("stage", ["propose", "evaluation", "promote"])
@pytest.mark.parametrize("nested", [False, True])
def test_stale_insight_plan_blocks_each_adoption_boundary(api, tmp_path, stage, nested):
    plan = make_plan(tmp_path, "source-plan")
    if nested:
        mapping = plan_record(api, plan)
        insight = record(api, "insight", artifacts=[mapping])
    else:
        insight = plan_record(api, plan, kind="insight")
    proposal = propose(api, [insight]) if stage != "propose" else None
    evaluation = evaluate(api, proposal) if stage == "promote" else None
    update_plan(tmp_path, plan)
    loaded = api({"action": "read", "id": insight["id"]})["artifact"]
    assert loaded["freshness"]["stale"]
    assert loaded["freshness"]["lineage"]["status"] == "stale"
    assert loaded["freshness"]["lineage"]["complete"]
    assert any(p["kind"] == "plan" and p["id"] == "source-plan" for p in loaded["freshness"]["lineage"]["problems"])
    # Root proposal plan_ids is empty: only transitive validation catches P1.
    with pytest.raises(ContractError, match="lineage.*plan.*revision_mismatch"):
        if stage == "propose":
            propose(api, [insight])
        elif stage == "evaluation":
            evaluate(api, proposal)
        else:
            api(promotion(proposal, evaluation))
    assert not PolicyStore(tmp_path).path().exists()


def test_direct_old_policy_insight_rejected_but_rereduced_ancestry_reusable(api, tmp_path):
    plan = make_plan(tmp_path, "source-plan")
    old_map = plan_record(api, plan)
    old_insight = record(api, "insight", artifacts=[old_map])
    policy = PolicyStore(tmp_path)
    policy.execute({"action": "update", "body": "# New policy\nPermissions remain binding.\n",
                    "expected_revision": old_map["bindings"]["policy_revision"], "actor": "human", "reason": "Explicit source change"}, no_git=True)
    cached = api({"action": "lookup", "id": old_map["id"]})["artifact"]
    assert cached["freshness"]["policy_status"] == "stale"
    assert cached["freshness"]["lineage"]["status"] == "current"
    with pytest.raises(ContractError, match="lineage.*policy.*revision_mismatch"):
        propose(api, [old_insight])
    # New policy-relative reasoning can reuse historical extracted features and
    # insights, provided their recursively checked source pins remain unchanged.
    new_insight = record(api, "insight", artifacts=[cached, old_insight])
    assert not new_insight["freshness"]["stale"]
    proposal = propose(api, [new_insight])
    assert not proposal["freshness"]["stale"]
    evaluation = evaluate(api, proposal)
    assert api(promotion(proposal, evaluation))["applied"]


@pytest.mark.parametrize("damage", ["missing", "corrupt", "symlink", "hardlink"])
def test_deep_unavailable_source_blocks_adoption_but_is_inspectable(api, tmp_path, damage):
    observation = record(api)
    mapping = record(api, "mapping", artifacts=[observation])
    insight = record(api, "insight", artifacts=[mapping])
    proposal = propose(api, [insight])
    evaluation = evaluate(api, proposal)
    path = Path(observation["path"])
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_text("PRIVATE-CORRUPT-DATA")
    elif damage == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "unreadable-secret")
    else:
        os.link(path, tmp_path / "external-link")
    loaded = api({"action": "read", "id": proposal["id"]})["artifact"]
    assert loaded["freshness"]["artifacts"][0]["status"] == "current"
    lineage = loaded["freshness"]["lineage"]
    assert lineage["status"] == "unavailable" and not lineage["complete"]
    assert any(p["id"] == observation["id"] and p["code"] == "unavailable" for p in lineage["problems"])
    assert "PRIVATE-CORRUPT-DATA" not in str(lineage)
    with pytest.raises(ContractError, match="lineage"):
        api(promotion(proposal, evaluation))


def test_diamond_memoizes_each_artifact_and_plan_once(api, tmp_path, monkeypatch):
    import agent_memory.rsi as lifecycle
    plan = make_plan(tmp_path, "source-plan")
    leaf = plan_record(api, plan, kind="observation")
    left = record(api, "mapping", artifacts=[leaf])
    right = record(api, "mapping", artifacts=[leaf])
    insight = record(api, "insight", artifacts=[left, right])
    artifact_reads, plan_reads = Counter(), Counter()
    original_artifact, original_plan = RSIStore.read, lifecycle._plan
    def counted_artifact(self, entry_id):
        artifact_reads[entry_id] += 1
        return original_artifact(self, entry_id)
    def counted_plan(store, entry_id):
        plan_reads[entry_id] += 1
        return original_plan(store, entry_id)
    monkeypatch.setattr(RSIStore, "read", counted_artifact)
    monkeypatch.setattr(lifecycle, "_plan", counted_plan)
    loaded = api({"action": "read", "id": insight["id"]})["artifact"]
    assert artifact_reads == Counter({item["id"]: 1 for item in (insight, left, right, leaf)})
    assert plan_reads == Counter({"source-plan": 1})
    assert loaded["freshness"]["lineage"]["artifacts_checked"] == 3
    assert loaded["freshness"]["lineage"]["plans_checked"] == 1
    assert loaded["freshness"]["lineage"]["edges_checked"] == 4


def unsafe_fixture(tmp_path, policy_revision, entry_id, *, refs=(), kind="mapping"):
    """Simulate externally written graphs; hashes remain valid at each file."""
    value = {"schema": 1, "id": entry_id, "kind": kind, "created_at": "2026-01-01T00:00:00Z", "created_by": "fixture",
             "bindings": {"policy_revision": policy_revision, "plans": [], "artifacts": list(refs)}, "data": {}}
    path = tmp_path / "shared/efforts" / f"rsi-{entry_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_artifact(value))
    return {"id": entry_id, "revision": artifact_revision(value)}


def test_cycle_is_explicitly_unavailable_not_infinite_recursion(api, tmp_path):
    policy_revision = binding(api)["policy_revision"]
    dummy = "sha256:" + "0" * 64
    a = unsafe_fixture(tmp_path, policy_revision, "cycle-a", refs=[{"id": "cycle-b", "revision": dummy}])
    unsafe_fixture(tmp_path, policy_revision, "cycle-b", refs=[a])
    root = unsafe_fixture(tmp_path, policy_revision, "root-insight", kind="insight", refs=[a])
    loaded = api({"action": "read", "id": root["id"]})["artifact"]
    lineage = loaded["freshness"]["lineage"]
    assert lineage["status"] == "unavailable" and not lineage["complete"]
    assert any(problem["code"] == "cycle" for problem in lineage["problems"])
    with pytest.raises(ContractError, match="lineage"):
        propose(api, [loaded])


@pytest.mark.parametrize("limit_name,limit", [("MAX_LINEAGE_ARTIFACTS", 1), ("MAX_LINEAGE_BYTES", 1), ("MAX_LINEAGE_EDGES", 1)])
def test_graph_safety_limits_fail_closed(api, monkeypatch, limit_name, limit):
    import agent_memory.rsi_lineage as lineage_module
    observation = record(api)
    mapping = record(api, "mapping", artifacts=[observation])
    insight = record(api, "insight", artifacts=[mapping])
    monkeypatch.setattr(lineage_module, limit_name, limit)
    loaded = api({"action": "read", "id": insight["id"]})["artifact"]
    lineage = loaded["freshness"]["lineage"]
    assert loaded["freshness"]["stale"] and lineage["status"] == "unavailable" and not lineage["complete"]
    assert any(problem["code"].endswith("_limit") for problem in lineage["problems"])
    counter = {"MAX_LINEAGE_ARTIFACTS": "artifacts_checked", "MAX_LINEAGE_BYTES": "bytes_checked",
               "MAX_LINEAGE_EDGES": "edges_checked"}[limit_name]
    assert lineage[counter] <= limit
    with pytest.raises(ContractError, match="lineage"):
        propose(api, [insight])


def test_unique_plan_limit_fails_closed(api, tmp_path, monkeypatch):
    import agent_memory.rsi_lineage as lineage_module
    mappings = [plan_record(api, make_plan(tmp_path, f"source-{i}")) for i in range(3)]
    insight = record(api, "insight", artifacts=mappings)
    monkeypatch.setattr(lineage_module, "MAX_LINEAGE_PLANS", 2)
    loaded = api({"action": "read", "id": insight["id"]})["artifact"]
    lineage = loaded["freshness"]["lineage"]
    assert lineage["status"] == "unavailable" and not lineage["complete"]
    assert lineage["plans_checked"] == 2
    assert any(problem["code"] == "plan_limit" for problem in lineage["problems"])


def test_problem_output_is_compact_and_never_false_fresh(api, tmp_path):
    policy_revision = binding(api)["policy_revision"]
    refs = [{"id": f"missing-{i}", "revision": "sha256:" + "0" * 64} for i in range(40)]
    root = unsafe_fixture(tmp_path, policy_revision, "root-insight", kind="insight", refs=refs)
    loaded = api({"action": "read", "id": root["id"]})["artifact"]
    lineage = loaded["freshness"]["lineage"]
    assert lineage["status"] == "unavailable" and not lineage["complete"]
    assert len(lineage["problems"]) == 32 and lineage["problem_count"] == 40 and lineage["problems_truncated"]
    assert loaded["freshness"]["stale"]


def test_deep_acyclic_chain_uses_iterative_traversal(api, tmp_path):
    policy_revision = binding(api)["policy_revision"]
    refs = []
    for i in range(1050):
        ref = unsafe_fixture(tmp_path, policy_revision, f"node-{i}", refs=refs)
        refs = [ref]
    root = unsafe_fixture(tmp_path, policy_revision, "root-insight", kind="insight", refs=refs)
    loaded = api({"action": "read", "id": root["id"]})["artifact"]
    lineage = loaded["freshness"]["lineage"]
    assert lineage["complete"] and lineage["status"] == "current"
    assert lineage["artifacts_checked"] == 1050 and lineage["problem_count"] == 0
    assert not loaded["freshness"]["stale"]
