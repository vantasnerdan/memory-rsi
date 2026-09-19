"""Thin JSON-in/JSON-out Click adapter for shared reward-first plans."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import click

from agent_memory.config import get_agent_id, resolve_base_path
from agent_memory.contracts_store import ContractError, ContractStore, shape, text
from agent_memory.plan_templates import list_templates, review_template, save_template
from agent_memory.plans import create_plan, read_plan, update_plan


def _review_examples(review, actor):
    ids = [r["id"] for key in ("achievements", "validation") for r in review["template"][key]]
    review["create_example"] = {
        "action": "create", "plan_id": "my-plan", "template_id": review["template_id"],
        "template_revision": review["revision"],
        "task": {"title": "Useful outcome", "purpose": "Why this matters",
                 "good": "Concrete task-specific acceptance criteria",
                 "work_items": [{"id": "work", "title": "Scoped outcome", "owner": actor,
                                 "requirement_ids": ids}]},
    }
    base = {"action": "update", "plan_id": "my-plan", "revision": "<latest plan revision>", "work_item_id": "work"}
    review["update_examples"] = [
        {**base, "evidence": [{"id": "result", "requirement_id": ids[0],
                               "summary": "Observed result and relevance", "reference": "Artifact or command/result"}]},
        {**base, "reviews": [{"evidence_id": "result", "verdict": "accepted", "note": "Why this evidence meets the requirement"}]},
        {**base, "exceptions": [{"id": "capability", "requirement_id": ids[0],
                                 "reason": "Specific unavailable capability", "alternative": "Substitute validation and limitations"}]},
        {**base, "exception_reviews": [{"exception_id": "capability", "verdict": "accepted", "note": "Explicit acceptance rationale"}]},
        {**base, "status": "complete"},
    ]
    review["guidance"] = (
        "Read/review before creating. Template snapshots are pinned; task additions may only add requirements. "
        "Every requirement must belong to a work item. Updates require the latest revision and one work_item_id. "
        "Evidence/exception reports are append-only; review existing reports in a separate update. "
        "Completion requires reviewed evidence or accepted exceptions for every scoped requirement. "
        "Scope, assignments, work-item topology and task acceptance fields are immutable; create an explicit successor plan to change them. "
        "Actor labels record provenance, not authenticated authority. Optional task additions: "
        "achievements and validation [{id,description,evidence}], steps and boundaries [nonempty string]."
    )
    return review


def execute_request(request, base, *, actor="unknown", no_git=False, allow_non_main_branch=False):
    """Execute a strict request. Filesystem save and Git/sync results are separate."""
    if not isinstance(request, dict):
        raise ContractError("request must be an object")
    action = text(request.get("action"), "action")
    text(actor, "actor")
    store = ContractStore(Path(base), no_git=no_git, allow_non_main_branch=allow_non_main_branch)
    with store.locked():
        if action == "templates":
            shape(request, ("action",), label="templates request")
            return list_templates(store)
        if action == "review":
            shape(request, ("action", "template_id"), label="review request")
            return _review_examples(review_template(store, request["template_id"]), actor)
        if action == "save_template":
            return save_template(store, request, actor)
        if action == "create":
            return create_plan(store, request, actor)
        if action in ("read", "validate"):
            shape(request, ("action", "plan_id"), label=f"{action} request")
            result = read_plan(store, request["plan_id"])
            if action == "validate":
                return {"plan_id": request["plan_id"], "revision": result["revision"], **result["validation"]}
            return result
        if action == "update":
            return update_plan(store, request, actor)
        raise ContractError(f"unknown plan action: {action}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ContractError(f"invalid JSON constant: {value}")


@click.command(name="plan")
@click.option("--request", required=True, help="JSON request, or '-' to read JSON from stdin.")
@click.option("--base", default=None, help="Memory base directory (config/env default).")
@click.option("--no-git", is_flag=True, help="Save atomically without Git commit or sync.")
@click.option("--allow-non-main-branch", is_flag=True, help="Explicitly allow persistence on another branch.")
def plan_cmd(request, base, no_git, allow_non_main_branch):
    """Discover/review templates and manage shared evidence-backed plans."""
    try:
        raw = click.get_text_stream("stdin").read() if request == "-" else request
        payload = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        result = execute_request(payload, resolve_base_path(base), actor=get_agent_id() or "unknown",
                                 no_git=no_git, allow_non_main_branch=allow_non_main_branch)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        click.echo(json.dumps({"error": str(exc), "saved": False}), err=True)
        raise click.exceptions.Exit(1) from exc
    click.echo(json.dumps(result, ensure_ascii=False))
