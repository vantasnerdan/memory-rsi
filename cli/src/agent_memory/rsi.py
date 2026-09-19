"""Revision-bound RSI lifecycle; assessments are advisory provenance only.

Lock order is contracts -> policy, never the reverse. No assessor/network call
occurs here. Publication happens after all snapshot/write locks are released.
Promotion delegates the canonical swap and history to PolicyStore.execute; RSI
never mutates plans, pinned templates, or managed instruction files.
"""
from __future__ import annotations

import difflib
import re
import uuid
from pathlib import Path

from agent_memory.contracts_store import ContractError, identifier, now, shape
from agent_memory.plans import read_plan
from agent_memory.policy import (
    MAX_BODY_CHARS as MAX_POLICY_CHARS,
    REVIEW_GUIDANCE, _lock, content_revision,
)
from agent_memory.rsi_schema import (
    KINDS, MAX_CONTEXT_BYTES, MAX_PLAN_FILE_BYTES, PROVENANCE,
    artifact_revision, bindings, body, data_object, json_text, plan_ids, revision,
    same_bindings, string,
)
from agent_memory.rsi_store import RSIStore
from agent_memory.rsi_corpus import corpus_page
from agent_memory.rsi_lineage import LineageCheck, require_lineage
from agent_memory.rsi_sections import apply_section_edits, revision_metrics, section_index


def _policy_lock(store):
    return _lock(store.policy.path(Path("shared/policies/.agent-policy.lock")))


def _policy(store):
    current = store.policy._current()
    # Existing policy limits are unchanged; direct edits outside those limits
    # cannot be silently truncated and assessed as if they were the whole policy.
    string(current["body"], "canonical policy body", MAX_POLICY_CHARS)
    return current


def _plan(store, entry_id):
    path = store.contracts.path("plans", entry_id)
    if not path.is_file() or path.stat().st_size > MAX_PLAN_FILE_BYTES:
        raise ContractError("plan is missing or exceeds bounded snapshot size")
    entry = read_plan(store.contracts, entry_id)
    result = {"plan_id": entry_id, **{k: entry[k] for k in ("revision", "plan", "validation")}}
    json_text(result, limit=MAX_CONTEXT_BYTES)
    return result


def _context(store, ids):
    result = {"policy": _policy(store), "plans": [_plan(store, entry_id) for entry_id in plan_ids(ids)]}
    json_text(result, limit=MAX_CONTEXT_BYTES)
    return result


def _bindings(context):
    return {"policy_revision": context["policy"]["revision"],
            "plans": [{"plan_id": p["plan_id"], "revision": p["revision"]} for p in context["plans"]]}


def _check_current(store, bound, *, adopt_insights=False):
    context = _context(store, [p["plan_id"] for p in bound["plans"]])
    current = _bindings(context)
    # Root policy/plan CAS first; every artifact hash and transitive plan pin is
    # independently revalidated below, not trusted from this comparison object.
    current["artifacts"] = bound.get("artifacts", [])
    if not same_bindings(bound, current):
        raise ContractError("stale RSI bindings: read current policy, plans and artifact revisions before recording/promoting")
    require_lineage(store, bound, _plan, policy_revision=context["policy"]["revision"], adopt_insights=adopt_insights)
    return context


def _proposal(store, entry_id, bound=None):
    proposal = store.read(identifier(entry_id, "proposal_id"))
    if proposal["kind"] != "proposal":
        raise ContractError("proposal_id must identify an immutable proposal")
    if bound is not None and not same_bindings(bound, proposal["bindings"]):
        raise ContractError("evaluation/report bindings do not match proposal source revisions")
    return proposal


def _freshness(store, value):
    """Recompute against live sources; old evidence remains readable if missing."""
    bound = value["bindings"]
    try:
        current = _policy(store)["revision"]
        policy_status = "current" if current == bound["policy_revision"] else "stale"
    except (ValueError, OSError, RuntimeError):
        current, policy_status = None, "unavailable"
    checker = LineageCheck(store, _plan)
    lineage = checker.inspect(bound, policy_revision=current, adopt_insights=value["kind"] in ("proposal", "evaluation"))
    result = {"stale": policy_status != "current" or lineage["status"] != "current",
              "policy_status": policy_status, "current_policy_revision": current,
              "plans": checker.direct_plans(bound), "lineage": lineage}
    if "artifacts" in bound:
        result["artifacts"] = checker.direct_artifacts(bound)
    if "proposal_id" in bound:
        try:
            _proposal(store, bound["proposal_id"], bound)
            result["proposal_status"] = "current"
        except (ValueError, OSError, RuntimeError):
            result.update(proposal_status="unavailable", stale=True)
    return result


def _export(store, value):
    return {**store.exported(value), "freshness": _freshness(store, value)}


def _new(entry_id, kind, bound, data, actor):
    return {"schema": 1, "id": entry_id, "kind": kind, "created_at": now(),
            "created_by": actor, "bindings": bound, "data": data}


def _propose(store, request, actor):
    shape(request, ("action", "proposal_id", "reason", "expected_revision", "plan_ids"),
          ("body", "edits", "source_artifact_ids"), label="propose request")
    if ("body" in request) == ("edits" in request):
        raise ContractError("propose requires exactly one of body or edits")
    entry_id = identifier(request["proposal_id"], "proposal_id")
    string(request["reason"], "reason", 4096)
    revision(request["expected_revision"], policy=True)
    context = _context(store, request["plan_ids"])
    if request["expected_revision"] != context["policy"]["revision"]:
        raise ContractError("stale canonical policy revision")
    original = context["policy"]["body"]
    edit_summary = None
    if "edits" in request:
        candidate, edit_summary = apply_section_edits(original, request["edits"])
        body(candidate)
    else:
        candidate = body(request["body"])
    data = {"body": candidate, "body_hash": content_revision(candidate), "reason": request["reason"],
            "parent_policy": context["policy"], "plans": context["plans"],
            "metrics": revision_metrics(original, candidate)}
    if edit_summary is not None:
        data.update(edits=request["edits"], edit_summary=edit_summary)
    bound = _bindings(context)
    if "source_artifact_ids" in request:
        ids = request["source_artifact_ids"]
        if not isinstance(ids, list) or len(ids) > 32:
            raise ContractError("source_artifact_ids must be a list of at most 32 insight IDs")
        for source_id in ids:
            identifier(source_id, "source_artifact_id")
        if len(ids) != len(set(ids)):
            raise ContractError("duplicate source_artifact_id")
        sources = []
        for source_id in ids:
            source = store.read(source_id)
            if source["kind"] != "insight":
                raise ContractError("proposal sources must be insight artifacts")
            sources.append({"id": source_id, "revision": artifact_revision(source)})
        bound["artifacts"] = sources
        data["source_artifacts"] = sources
    require_lineage(store, bound, _plan, policy_revision=context["policy"]["revision"], adopt_insights=True)
    value = _new(entry_id, "proposal", bound, data, actor)
    repo, paths = store.save(value)
    return {"artifact": _export(store, value)}, repo, paths


def _record(store, request, actor):
    shape(request, ("action", "kind", "bindings", "data"), ("record_id",), label="record request")
    kind = request["kind"]
    if not isinstance(kind, str) or kind not in KINDS[1:]:
        raise ContractError("record kind must be " + ", ".join(KINDS[1:]))
    record_id = f"{kind}-{uuid.uuid4().hex}"
    if "record_id" in request:
        record_id = identifier(request["record_id"], "record_id")
        if kind != "mapping" or not re.fullmatch(r"map-[0-9a-f]{64}", record_id):
            raise ContractError("record_id is only allowed for mapping as map-<64 lowercase hex digits>")
        if store.lookup(record_id) is not None:
            raise ContractError("immutable RSI artifact ID already exists")
    bound = bindings(request["bindings"])
    data = data_object(request["data"])
    if kind == "evaluation" and "proposal_id" not in bound:
        raise ContractError("evaluation requires proposal binding")
    _check_current(store, bound, adopt_insights=kind == "evaluation")
    if "proposal_id" in bound:
        _proposal(store, bound["proposal_id"], bound)
    value = _new(record_id, kind, bound, data, actor)
    repo, paths = store.save(value)
    return {"artifact": _export(store, value)}, repo, paths


def _list(store, request):
    shape(request, ("action",), ("kind", "limit", "after"), label="list request")
    kind = request.get("kind")
    if "kind" in request and (not isinstance(kind, str) or kind not in KINDS):
        raise ContractError("invalid RSI list kind")
    limit = request.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ContractError("limit must be an integer between 1 and 50")
    after = identifier(request["after"], "after") if "after" in request else ""
    summaries = []
    has_more = False
    last_scanned = after
    scanned, byte_count = 0, 0
    for entry_id in store.list_ids():
        if entry_id <= after:
            continue
        if len(summaries) == limit or scanned >= 128 or byte_count >= 8 * 1024 * 1024:
            has_more = True
            break
        value = store.read(entry_id)
        scanned += 1
        byte_count += store.path(entry_id).stat().st_size
        last_scanned = entry_id
        if kind is not None and value["kind"] != kind:
            continue
        exported = _export(store, value)
        summary = {k: exported[k] for k in ("id", "kind", "created_at", "created_by", "revision", "path", "bindings", "freshness")}
        status = value["data"].get("status")
        if isinstance(status, str) and len(status) <= 64:
            summary["status"] = status
        summaries.append(summary)
    return {"artifacts": summaries, "has_more": has_more,
            "next_after": last_scanned if has_more else None,
            "order": "id_ascending", "provenance": PROVENANCE}


def _promote(store, request):
    shape(request, ("action", "proposal_id", "evaluation_id", "expected_revision", "review_note", "actor"), ("apply",), label="promote request")
    proposal_id = identifier(request["proposal_id"], "proposal_id")
    evaluation_id = identifier(request["evaluation_id"], "evaluation_id")
    revision(request["expected_revision"], policy=True)
    string(request["review_note"], "review_note", 2048)
    if not isinstance(request["actor"], str) or request["actor"] not in ("human", "agent"):
        raise ContractError("actor must be human or agent (unauthenticated provenance)")
    apply = request.get("apply", False)
    if type(apply) is not bool:
        raise ContractError("apply must be a boolean")
    # Only contracts lock is held on entry. Release the read policy lock before
    # PolicyStore.execute acquires it itself; its CAS closes that policy race.
    with _policy_lock(store):
        proposal = _proposal(store, proposal_id)
        evaluation = store.read(evaluation_id)
        if evaluation["kind"] != "evaluation" or evaluation["bindings"].get("proposal_id") != proposal_id:
            raise ContractError("evaluation must bind this exact proposal")
        if not same_bindings(evaluation["bindings"], proposal["bindings"]):
            raise ContractError("evaluation source revisions do not match proposal")
        # Availability is a technical prerequisite, not a semantic acceptance
        # score. Low scores and negative judgments are still advisory evidence.
        if evaluation["data"].get("status") != "assessed":
            raise ContractError("evaluation unavailable: data.status must be assessed before promotion")
        context = _check_current(store, proposal["bindings"], adopt_insights=True)
        if request["expected_revision"] != context["policy"]["revision"]:
            raise ContractError("stale expected canonical revision")
        original, candidate = context["policy"]["body"], proposal["data"]["body"]
        result = {
            "proposal_id": proposal_id, "evaluation_id": evaluation_id,
            "proposal_revision": store.exported(proposal)["revision"],
            "evaluation_revision": store.exported(evaluation)["revision"],
            "applied": False, "changed": candidate != original,
            "expected_revision": context["policy"]["revision"],
            "proposed_revision": content_revision(candidate),
            "diff": "".join(difflib.unified_diff(original.splitlines(keepends=True), candidate.splitlines(keepends=True),
                                                fromfile="canonical/agent-policy.md", tofile=f"proposal/{proposal_id}")),
            "actor": request["actor"], "review_note": request["review_note"],
            "review_guidance": REVIEW_GUIDANCE + " Model judgments never authorize automatic promotion or override platform permissions.",
        }
    if not apply:
        return result, None, []
    # Preserve staged/default-branch protections even though the canonical
    # update delegates local persistence to PolicyStore. Stage/commit the exact
    # canonical/history/event bytes inside its writer lock, then push only that
    # captured commit outside locks. No later no_git update can be swept in.
    paths = [store.policy.path(), store.policy._snapshot_path(result["expected_revision"]),
             store.policy._snapshot_path(result["proposed_revision"])]
    repo = store.prepare(paths)
    reason = (f"RSI promotion: proposal={proposal_id}@{result['proposal_revision']}; "
              f"evaluation={evaluation_id}@{result['evaluation_revision']}; "
              f"review_note={request['review_note']}")
    store.policy.commit_event = lambda event: store.commit_local(repo, [*paths, event])
    try:
        updated = store.policy.execute({"action": "update", "body": candidate,
                                       "expected_revision": request["expected_revision"],
                                       "actor": request["actor"], "reason": reason}, no_git=True)
    finally:
        store.policy.commit_event = None
    if store.policy.event_path is not None:
        paths.append(store.policy.event_path)
    result.update(applied=True, policy={k: updated[k] for k in
                                     ("path", "revision", "previous_revision", "write_success", "review_guidance")})
    return result, repo, paths


def execute_request(request, base, *, actor="unknown", no_git=False, allow_non_main_branch=False):
    """Strict JSON request API used by the Click adapter and server integration."""
    json_text(request)
    if not isinstance(request, dict):
        raise ContractError("request must be an object")
    action = string(request.get("action"), "action", 32)
    string(actor, "actor", 128)
    store = RSIStore(base, no_git=no_git, allow_non_main_branch=allow_non_main_branch)
    repo, paths = None, []
    with store.contracts.locked():
        if action == "promote":
            result, repo, paths = _promote(store, request)
        else:
            with _policy_lock(store):
                if action == "context":
                    shape(request, ("action", "plan_ids"), label="context request")
                    result = _context(store, request["plan_ids"])
                elif action == "sections":
                    shape(request, ("action",), label="sections request")
                    policy = _policy(store)
                    result = {"policy": policy, "sections": section_index(policy["body"])}
                elif action == "corpus":
                    result = corpus_page(store, request)
                elif action == "propose":
                    result, repo, paths = _propose(store, request, actor)
                elif action == "record":
                    result, repo, paths = _record(store, request, actor)
                elif action in ("read", "lookup"):
                    shape(request, ("action", "id"), label=f"{action} request")
                    value = store.read(request["id"]) if action == "read" else store.lookup(request["id"])
                    result = {"artifact": _export(store, value) if value is not None else None}
                elif action == "list":
                    result = _list(store, request)
                else:
                    raise ContractError("action must be context, sections, corpus, propose, record, read, lookup, list, or promote")
    if paths:
        result["persistence"] = {"saved": True, "git": store.publish(repo, paths)}
    return {"ok": True, "action": action, **result}
