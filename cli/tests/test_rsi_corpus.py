"""Successor corpus discovery, cache identities, lineage and section integration."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier

import pytest

from agent_memory.contracts_cli import execute_request as plan_api
from agent_memory.contracts_store import ContractError
from agent_memory.policy import MAX_BODY_CHARS, PolicyStore
from agent_memory.rsi import execute_request
from agent_memory.rsi_schema import artifact_refs
from agent_memory.rsi_store import RSIStore, render_artifact, validate_artifact


@pytest.fixture
def api(tmp_path):
    return lambda request: execute_request(request, tmp_path, no_git=True)


def binding(api, artifacts=()):
    current = api({"action": "context", "plan_ids": []})
    result = {"policy_revision": current["policy"]["revision"], "plans": []}
    if artifacts:
        result["artifacts"] = [{"id": item["id"], "revision": item["revision"]} for item in artifacts]
    return result


def record(api, kind="observation", *, artifacts=(), data=None, record_id=None):
    request = {"action": "record", "kind": kind, "bindings": binding(api, artifacts),
               "data": data if data is not None else {"title": "Selected owned signals", "counts": {"tool_error": 1}}}
    if record_id is not None:
        request["record_id"] = record_id
    return api(request)["artifact"]


def make_plan(base, plan_id):
    request = plan_api({"action": "review", "template_id": "general"}, base, no_git=True)["create_example"]
    request["plan_id"] = plan_id
    request["task"]["title"] = f"Contract {plan_id}"
    return plan_api(request, base, no_git=True)


def propose(api, sources=(), **fields):
    current = api({"action": "context", "plan_ids": []})
    request = {"action": "propose", "proposal_id": "candidate", "reason": "Selected recurring issue",
               "expected_revision": current["policy"]["revision"], "plan_ids": [],
               "source_artifact_ids": [source["id"] for source in sources]}
    if not fields:
        fields = {"body": "# Revised policy\nPreserve permissions and reviewed evidence.\n"}
    return api({**request, **fields})["artifact"]


def evaluate(api, proposal, **overrides):
    return api({"action": "record", "kind": "evaluation",
                "bindings": {**proposal["bindings"], "proposal_id": proposal["id"], **overrides},
                "data": {"status": "assessed"}})["artifact"]


def test_plan_corpus_paging_summaries_and_review_counts(api, tmp_path):
    plans = [make_plan(tmp_path, name) for name in ("alpha", "beta", "gamma")]
    update = {"action": "update", "plan_id": "alpha", "revision": plans[0]["revision"], "work_item_id": "work",
              "evidence": [{"id": "result", "requirement_id": "outcome", "summary": "Observed result", "reference": "artifact"}]}
    first = plan_api(update, tmp_path, no_git=True)
    for verdict in ("accepted", "rejected"):
        first = plan_api({"action": "update", "plan_id": "alpha", "revision": first["revision"], "work_item_id": "work",
                          "reviews": [{"evidence_id": "result", "verdict": verdict, "note": "Latest review"}]}, tmp_path, no_git=True)
    page = api({"action": "corpus", "kind": "plans", "limit": 2})
    assert [item["id"] for item in page["sources"]] == ["alpha", "beta"]
    alpha = page["sources"][0]
    assert alpha["kind"] == "plan" and alpha["revision"] == first["revision"]
    assert alpha["path"] == first["path"] and alpha["title"] == "Contract alpha"
    assert alpha["complete"] is False
    assert alpha["review_counts"] == {"evidence_reports": 1, "exception_reports": 0, "accepted": 0, "rejected": 1, "pending": 0}
    assert "plan" not in alpha and "markdown" not in alpha
    assert page["has_more"] and page["next_after"] == "beta"
    tail = api({"action": "corpus", "kind": "plans", "after": page["next_after"], "limit": 2})
    assert [item["id"] for item in tail["sources"]] == ["gamma"]
    assert not tail["has_more"] and tail["next_after"] is None


def test_bad_sources_advance_cursor_and_do_not_hide_good_plan(api, tmp_path, monkeypatch):
    import agent_memory.rsi_corpus as corpus
    plans = [make_plan(tmp_path, name) for name in ("alpha", "beta", "gamma")]
    Path(plans[0]["path"]).write_text("CORRUPT-PRIVATE-SENTINEL not YAML")
    path = Path(plans[1]["path"])
    path.unlink()
    secret = tmp_path / "outside-secret"
    secret.write_text("NEVER-READ-THIS-SOURCE")
    path.symlink_to(secret)
    monkeypatch.setattr(corpus, "MAX_PAGE_SCANNED", 2)
    first = api({"action": "corpus", "kind": "plans"})
    assert first["sources"] == []
    assert [error["id"] for error in first["errors"]] == ["alpha", "beta"]
    assert first["next_after"] == "beta" and first["has_more"] and first["scanned"] == 2
    assert "PRIVATE-SENTINEL" not in json.dumps(first) and "NEVER-READ" not in json.dumps(first)
    tail = api({"action": "corpus", "kind": "plans", "after": first["next_after"]})
    assert [item["id"] for item in tail["sources"]] == ["gamma"] and not tail["errors"]


def test_corpus_rejects_hardlinks_oversize_and_pinned_template_tamper(api, tmp_path):
    plans = [make_plan(tmp_path, name) for name in ("alpha", "beta", "gamma", "omega")]
    os.link(plans[0]["path"], tmp_path / "hardlink")
    Path(plans[1]["path"]).write_text("x" * (1024 * 1024 + 1))
    tampered = Path(plans[2]["path"])
    tampered.write_text(tampered.read_text().replace("Deliver the requested useful outcome.", "Silently weakened"))
    result = api({"action": "corpus", "kind": "plans"})
    assert [item["id"] for item in result["sources"]] == ["omega"]
    assert [item["id"] for item in result["errors"]] == ["alpha", "beta", "gamma"]
    assert all(error["code"] == "invalid_source" for error in result["errors"])


def test_corpus_file_and_byte_caps_and_unsafe_names(api, tmp_path, monkeypatch):
    import agent_memory.rsi_corpus as corpus
    plans = [make_plan(tmp_path, name) for name in ("alpha", "bravo", "delta")]
    directory = Path(plans[0]["path"]).parent
    (directory / "UNSAFE name.md").write_text("Invalid ID cannot hide valid files")
    result = api({"action": "corpus", "kind": "plans"})
    assert result["invalid_file_names"] == 1 and len(result["sources"]) == 3
    size = Path(plans[0]["path"]).stat().st_size
    monkeypatch.setattr(corpus, "MAX_PAGE_BYTES", size)
    result = api({"action": "corpus", "kind": "plans"})
    assert result["scanned"] == 1 and result["scanned_bytes"] == size
    assert result["next_after"] == "alpha" and result["has_more"]
    monkeypatch.setattr(corpus, "MAX_CORPUS_FILES", 3)
    with pytest.raises(ContractError, match="enumeration exceeds"):
        api({"action": "corpus", "kind": "plans"})


def test_observation_corpus_filters_generic_records_and_bounds_metadata(api):
    record(api, "mapping")
    observation = record(api, data={"title": "T" * 500, "raw_owned_note": "Do not return full data in corpus summaries"})
    record(api, "insight")
    result = api({"action": "corpus", "kind": "observations"})
    assert len(result["sources"]) == 1 and result["scanned"] == 3
    summary = result["sources"][0]
    assert summary["id"] == observation["id"] and summary["revision"] == observation["revision"]
    assert len(summary["title"]) == 300 and summary["title_truncated"]
    assert summary["complete"] is None and not any(summary["review_counts"].values())
    assert "raw_owned_note" not in json.dumps(summary)
    assert api({"action": "lookup", "id": observation["id"]})["artifact"] == observation


def test_corrupt_rsi_source_does_not_hide_later_observation(api, tmp_path, monkeypatch):
    import agent_memory.rsi_corpus as corpus
    observation = record(api)
    bad = tmp_path / "shared/efforts/rsi-aaa.md"
    bad.write_text("corrupt")
    monkeypatch.setattr(corpus, "MAX_PAGE_SCANNED", 1)
    first = api({"action": "corpus", "kind": "observations"})
    assert first["errors"][0]["id"] == "aaa" and first["next_after"] == "aaa"
    rest = api({"action": "corpus", "kind": "observations", "after": "aaa"})
    assert rest["sources"][0]["id"] == observation["id"]


@pytest.mark.parametrize("fields", [{"kind": "../plans"}, {"kind": []}, {"kind": "plans", "after": "../x"},
                                    {"kind": "plans", "limit": True}, {"kind": "plans", "limit": 51},
                                    {"kind": "plans", "path": "/secret"}])
def test_corpus_strict_shapes(api, fields):
    with pytest.raises(ContractError):
        api({"action": "corpus", **fields})


def test_corpus_does_not_follow_directory_symlink(api, tmp_path):
    (tmp_path / "shared").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "shared/plans").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ContractError, match="symlink"):
        api({"action": "corpus", "kind": "plans"})


def test_lookup_null_only_on_true_absence(api, tmp_path):
    assert api({"action": "lookup", "id": "missing"})["artifact"] is None
    observation = record(api)
    path = Path(observation["path"])
    path.write_text("bad")
    with pytest.raises(ContractError, match="integrity"):
        api({"action": "lookup", "id": observation["id"]})
    path.unlink()
    path.symlink_to(tmp_path / "missing-target")
    with pytest.raises(ContractError, match="symlink"):
        api({"action": "lookup", "id": observation["id"]})
    path.unlink()
    path.mkdir()
    with pytest.raises(ContractError):
        api({"action": "lookup", "id": observation["id"]})
    with pytest.raises(ContractError):
        api({"action": "lookup", "id": "../escape"})


def test_deterministic_mapping_identity_is_immutable_and_race_safe(api, tmp_path):
    map_id = "map-" + "a" * 64
    barrier = Barrier(2)
    def save():
        barrier.wait(timeout=5)
        try:
            return record(api, "mapping", record_id=map_id)
        except ContractError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=10) for future in [pool.submit(save), pool.submit(save)]]
    assert len([value for value in results if isinstance(value, dict)]) == 1
    assert "already exists" in next(value for value in results if isinstance(value, str))
    stored = api({"action": "lookup", "id": map_id})["artifact"]
    assert stored["id"] == map_id and stored["kind"] == "mapping"
    original = Path(stored["path"]).read_bytes()
    with pytest.raises(ContractError, match="already exists"):
        record(api, "mapping", data={"overwrite": True}, record_id=map_id)
    assert Path(stored["path"]).read_bytes() == original


@pytest.mark.parametrize("kind,record_id", [("observation", "map-" + "a" * 64), ("mapping", "map-" + "A" * 64),
                                           ("mapping", "map-" + "a" * 63), ("mapping", "../escape"),
                                           ("mapping", "normal-id")])
def test_mapping_record_id_strict_shape(api, kind, record_id):
    with pytest.raises(ContractError):
        record(api, kind, record_id=record_id)


def test_direct_artifact_bindings_validate_exact_revision_and_freshness(api, tmp_path):
    observation = record(api)
    source = {"id": observation["id"], "revision": observation["revision"]}
    mapping = record(api, "mapping", artifacts=[observation])
    assert mapping["bindings"]["artifacts"] == [source]
    assert mapping["freshness"]["artifacts"][0]["status"] == "current"
    wrong = {**binding(api), "artifacts": [{**source, "revision": "sha256:" + "0" * 64}]}
    with pytest.raises(ContractError, match="stale"):
        api({"action": "record", "kind": "insight", "bindings": wrong, "data": {}})
    # Even a self-consistently rehashed external rewrite cannot satisfy old refs.
    store = RSIStore(tmp_path, no_git=True)
    value = store.read(observation["id"])
    value["data"]["external_change"] = True
    Path(observation["path"]).write_text(render_artifact(value))
    loaded = api({"action": "read", "id": mapping["id"]})["artifact"]
    assert loaded["freshness"]["stale"] and loaded["freshness"]["artifacts"][0]["status"] == "stale"
    Path(observation["path"]).unlink()
    loaded = api({"action": "read", "id": mapping["id"]})["artifact"]
    assert loaded["freshness"]["artifacts"][0]["status"] == "unavailable"
    assert loaded["data"] == mapping["data"]


def test_lineage_rechecks_ancestry_without_serializing_transitive_payloads(api, monkeypatch):
    observation = record(api)
    mapping = record(api, "mapping", artifacts=[observation])
    insight = record(api, "insight", artifacts=[mapping])
    Path(observation["path"]).unlink()
    calls = []
    original = RSIStore.read
    def counted(self, entry_id):
        calls.append(entry_id)
        return original(self, entry_id)
    monkeypatch.setattr(RSIStore, "read", counted)
    loaded = api({"action": "read", "id": insight["id"]})["artifact"]
    # Reviewed P1 correction: direct metadata remains compact, but transitive
    # missing sources must no longer masquerade as fresh adoption evidence.
    assert calls == [insight["id"], mapping["id"]]  # absent leaf fails before read
    assert loaded["freshness"]["artifacts"][0]["status"] == "current"
    assert loaded["freshness"]["stale"]
    assert loaded["freshness"]["lineage"]["status"] == "unavailable"
    assert loaded["freshness"]["lineage"]["problems"][0]["id"] == observation["id"]
    assert loaded["data"] == insight["data"]


def test_policy_change_does_not_destroy_cached_mapping_features(api, tmp_path):
    observation = record(api)
    mapping = record(api, "mapping", artifacts=[observation], record_id="map-" + "1" * 64,
                     data={"status": "assessed", "features": ["focused-tool-failure"], "policy_independent": True})
    policy = PolicyStore(tmp_path)
    policy.execute({"action": "update", "body": "# Changed policy\nSame safety boundaries.\n",
                    "expected_revision": mapping["bindings"]["policy_revision"], "actor": "human", "reason": "Explicit change"}, no_git=True)
    cached = api({"action": "lookup", "id": mapping["id"]})["artifact"]
    assert cached["freshness"]["policy_status"] == "stale" and cached["data"] == mapping["data"]
    assert cached["revision"] == mapping["revision"]
    insight = record(api, "insight", artifacts=[cached])
    assert not insight["freshness"]["stale"]  # caller owns cache applicability judgment


def test_artifact_reference_bounds_and_duplicates(api):
    refs = [{"id": f"source-{i}", "revision": "sha256:" + "1" * 64} for i in range(128)]
    assert artifact_refs(refs) == refs
    for invalid in [refs + [refs[0]], [refs[0], refs[0]], [{"id": "../x", "revision": refs[0]["revision"]}],
                    [{"id": "safe", "revision": "bad"}], [{"id": "safe", "revision": refs[0]["revision"], "path": "/secret"}], None]:
        with pytest.raises(ContractError):
            artifact_refs(invalid)
    with pytest.raises(ContractError):
        record(api, "insight", artifacts=[{"id": "missing", "revision": "sha256:" + "1" * 64}])


def test_proposal_and_evaluation_preserve_exact_insight_lineage(api):
    insight = record(api, "insight", data={"issue": "Recurring evidence-linked friction"})
    proposal = propose(api, [insight])
    refs = [{"id": insight["id"], "revision": insight["revision"]}]
    assert proposal["bindings"]["artifacts"] == refs and proposal["data"]["source_artifacts"] == refs
    assert "issue" not in proposal["data"]  # no whole source bodies copied
    evaluation = evaluate(api, proposal)
    assert evaluation["bindings"]["artifacts"] == refs
    with pytest.raises(ContractError, match="do not match proposal"):
        evaluate(api, proposal, artifacts=[])
    Path(insight["path"]).write_text("corrupted")
    loaded = api({"action": "read", "id": proposal["id"]})["artifact"]
    assert loaded["freshness"]["artifacts"][0]["status"] == "unavailable"
    with pytest.raises(ContractError):
        api({"action": "promote", "proposal_id": proposal["id"], "evaluation_id": evaluation["id"],
             "expected_revision": proposal["bindings"]["policy_revision"], "actor": "human", "review_note": "Review", "apply": True})


def test_proposal_sources_require_unique_bounded_insight_ids(api):
    observation = record(api)
    with pytest.raises(ContractError, match="insight"):
        propose(api, [observation])
    insight = record(api, "insight")
    with pytest.raises(ContractError, match="duplicate"):
        propose(api, [insight, insight])
    with pytest.raises(ContractError, match="at most 32"):
        propose(api, [insight] * 33)


def test_sections_action_and_replay_verified_candidate_integration(api, tmp_path):
    sectioned = api({"action": "sections"})
    section = sectioned["sections"][0]
    edits = [{"operation": "replace", "section_ids": [section["section_id"]],
              "reason": "Clarify introduction without appending", "body": "# Clarified policy\n\nPermissions remain binding.\n\n"}]
    proposal = propose(api, edits=edits)
    assert proposal["data"]["edits"] == edits
    assert proposal["data"]["edit_summary"][0]["section_ids"] == [section["section_id"]]
    assert proposal["data"]["metrics"]["append_only"] is False
    assert api({"action": "read", "id": proposal["id"]})["artifact"]["data"] == proposal["data"]
    raw = RSIStore(tmp_path, no_git=True).read(proposal["id"])
    for field in ("body", "edit_summary", "metrics"):
        tampered = deepcopy(raw)
        if field == "body":
            tampered["data"]["body"] += "Unaudited appendix"
        elif field == "edit_summary":
            tampered["data"][field][0]["reason"] = "Incorrect edit summary"
        else:
            tampered["data"][field]["append_only"] = True
        with pytest.raises(ContractError):
            validate_artifact(tampered)


def test_proposal_body_xor_edits_and_inherited_policy_size_ceiling(api):
    with pytest.raises(ContractError, match="exactly one"):
        propose(api, body="New body", edits=[])
    with pytest.raises(ContractError):
        propose(api, edits=[])
    candidate = "# Coherent long policy\n" + "a" * 16000
    proposal = propose(api, body=candidate)
    assert proposal["data"]["body"] == candidate
    assert len(candidate) < MAX_BODY_CHARS


def test_sections_edit_cas_and_old_proposal_compatibility(api, tmp_path):
    sections = api({"action": "sections"})
    proposal = propose(api)
    raw = RSIStore(tmp_path, no_git=True).read(proposal["id"])
    raw["data"].pop("metrics")
    raw["data"].pop("source_artifacts")
    raw["bindings"].pop("artifacts")
    validate_artifact(raw)  # original body-only immutable schema remains accepted
    policy = PolicyStore(tmp_path)
    policy.execute({"action": "update", "body": "Changed current body", "expected_revision": sections["policy"]["revision"],
                    "actor": "human", "reason": "Explicit change"}, no_git=True)
    with pytest.raises(ContractError, match="stale"):
        api({"action": "propose", "proposal_id": "stale", "expected_revision": sections["policy"]["revision"], "reason": "Old edit",
             "plan_ids": [], "edits": [{"operation": "retire", "section_ids": [sections["sections"][0]["section_id"]], "reason": "Old source"}]})


def test_observation_filtered_scan_cursor_then_more_than_fifty_sources(api, monkeypatch):
    import agent_memory.rsi_corpus as corpus
    map_ids = ["map-" + str(i) * 64 for i in range(3)]
    for map_id in map_ids:
        record(api, "mapping", record_id=map_id)
    saved = {record(api)["id"] for _ in range(53)}
    monkeypatch.setattr(corpus, "MAX_PAGE_SCANNED", 3)
    first = api({"action": "corpus", "kind": "observations", "limit": 50})
    assert first["sources"] == [] and first["next_after"] == map_ids[-1] and first["has_more"]
    monkeypatch.setattr(corpus, "MAX_PAGE_SCANNED", 128)
    main = api({"action": "corpus", "kind": "observations", "after": first["next_after"], "limit": 50})
    assert len(main["sources"]) == 50 and main["has_more"]
    tail = api({"action": "corpus", "kind": "observations", "after": main["next_after"], "limit": 50})
    assert len(tail["sources"]) == 3 and not tail["has_more"]
    assert {item["id"] for item in main["sources"] + tail["sources"]} == saved


def test_section_candidate_lineage_preview_apply_and_rollback(api, tmp_path):
    sections = api({"action": "sections"})
    first = sections["sections"][0]
    insight = record(api, "insight", data={"issue": "Clarify scope while preserving all unchanged sections"})
    edits = [{"operation": "replace", "section_ids": [first["section_id"]], "reason": "Explicit replacement, not appendix",
              "body": "# Revised introduction\n\nNo judgment grants platform permissions.\n\n"}]
    proposed = propose(api, [insight], edits=edits)
    evaluated = evaluate(api, proposed)
    request = {"action": "promote", "proposal_id": proposed["id"], "evaluation_id": evaluated["id"],
               "expected_revision": sections["policy"]["revision"], "actor": "human", "review_note": "Explicit self-reported review"}
    preview = api(request)
    assert not preview["applied"] and "Revised introduction" in preview["diff"]
    applied = api({**request, "apply": True})
    assert applied["applied"]
    policy = PolicyStore(tmp_path)
    current = policy.execute({"action": "read"})
    assert current["body"].endswith(sections["policy"]["body"][first["end"]:])
    assert current["body"] == proposed["data"]["body"]
    assert len(policy.execute({"action": "history"})["events"]) == 1
    policy.execute({"action": "rollback", "revision": sections["policy"]["revision"], "expected_revision": current["revision"],
                    "actor": "human", "reason": "Explicit rollback"}, no_git=True)
    assert policy.execute({"action": "read"})["body"] == sections["policy"]["body"]
