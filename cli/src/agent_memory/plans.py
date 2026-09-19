"""Immutable pinned contracts with scoped, evidence-backed work-item updates.

Actor names are audit labels, not authentication. Review is an explicit judgment,
not a proof that an artifact is true; this API never turns a report into a review.
"""
from __future__ import annotations

from copy import deepcopy

from agent_memory.contracts_store import (
    ContractError, check_revision, content_revision, identifier, now, sequence, shape, text,
)
from agent_memory.plan_templates import (
    render_template, requirements, review_template, string_list, validate_template,
)

TASK_FIELDS = ("title", "purpose", "good")
TASK_ADDITIONS = ("achievements", "validation", "steps", "boundaries")
STATUSES = ("pending", "in_progress", "complete")


def task_requirements(task, template):
    combined = []
    for key in ("achievements", "validation"):
        combined += template[key] + task.get(key, [])
    ids = [r["id"] for r in combined]
    if len(ids) != len(set(ids)):
        raise ContractError("task requirements cannot replace pinned requirement IDs")
    return combined


def validate_task(task, template):
    shape(task, TASK_FIELDS, TASK_ADDITIONS, label="task")
    for key in TASK_FIELDS:
        text(task[key], key)
    for key in ("achievements", "validation"):
        if key in task:
            requirements(task[key], key)
    for key in ("steps", "boundaries"):
        if key in task:
            string_list(task[key], key)
    return task_requirements(task, template)


def validate_item_scope(items, requirement_ids, *, stored=False):
    seen, assigned = set(), set()
    for item in sequence(items, "work_items", nonempty=True):
        fields = ("id", "title", "owner", "requirement_ids")
        shape(item, fields + (("status", "evidence", "exceptions") if stored else ()), label="work item")
        item_id = identifier(item["id"], "work item id")
        if item_id in seen:
            raise ContractError("duplicate work item ID")
        seen.add(item_id)
        text(item["title"], "work item title")
        text(item["owner"], "owner")
        ids = sequence(item["requirement_ids"], "requirement_ids", nonempty=True)
        for req in ids:
            identifier(req, "requirement id")
            if req not in requirement_ids:
                raise ContractError(f"unknown requirement: {req}")
        if len(ids) != len(set(ids)):
            raise ContractError("duplicate work item requirement")
        assigned.update(ids)
    if assigned != requirement_ids:
        raise ContractError(f"work items must cover all requirements; missing {sorted(requirement_ids - assigned)}")


def _review(value, target_key):
    shape(value, (target_key, "verdict", "note"), label="review")
    identifier(value[target_key], target_key)
    if not isinstance(value["verdict"], str) or value["verdict"] not in ("accepted", "rejected"):
        raise ContractError("review verdict must be accepted or rejected")
    text(value["note"], "review note")


def _report(value, exception=False):
    fields = ("id", "requirement_id", "reason", "alternative") if exception else ("id", "requirement_id", "summary", "reference")
    shape(value, fields, label="exception" if exception else "evidence")
    identifier(value["id"])
    identifier(value["requirement_id"], "requirement_id")
    for key in fields[2:]:
        text(value[key], key)


def _validate_records(item, exception=False):
    key = "exceptions" if exception else "evidence"
    report_fields = ("id", "requirement_id", "reason", "alternative") if exception else ("id", "requirement_id", "summary", "reference")
    seen = set()
    for record in sequence(item[key], key):
        shape(record, (*report_fields, "reported_by", "reported_at", "reviews"), label=key)
        _report({k: record[k] for k in report_fields}, exception)
        if record["id"] in seen or record["requirement_id"] not in item["requirement_ids"]:
            raise ContractError("duplicate record ID or evidence outside work-item scope")
        seen.add(record["id"])
        text(record["reported_by"], "reported_by")
        text(record["reported_at"], "reported_at")
        for review in sequence(record["reviews"], "reviews"):
            shape(review, ("verdict", "note", "reviewed_by", "reviewed_at"), label="stored review")
            _review({"record_id": record["id"], "verdict": review["verdict"], "note": review["note"]}, "record_id")
            text(review["reviewed_by"], "reviewed_by")
            text(review["reviewed_at"], "reviewed_at")


def _accepted(record):
    return bool(record["reviews"] and record["reviews"][-1]["verdict"] == "accepted")


def item_progress(item):
    evidenced = {e["requirement_id"] for e in item["evidence"] if _accepted(e)}
    excepted = {e["requirement_id"] for e in item["exceptions"] if _accepted(e)}
    pending_exceptions = [e["id"] for e in item["exceptions"] if not e["reviews"]]
    missing = sorted(set(item["requirement_ids"]) - evidenced - excepted)
    return {"work_item_id": item["id"], "reported_evidence": len(item["evidence"]),
            "reviewed_requirements": sorted(evidenced), "accepted_exceptions": sorted(excepted),
            "missing_requirements": missing, "pending_exceptions": pending_exceptions,
            "ready": not missing and not pending_exceptions}


def validate_plan(plan):
    shape(plan, ("kind", "schema", "plan_id", "title", "created_by", "template", "task", "work_items"), label="plan contract")
    if plan["kind"] != "plan" or type(plan["schema"]) is not int or plan["schema"] != 1:
        raise ContractError("unsupported plan contract")
    identifier(plan["plan_id"], "plan_id")
    text(plan["title"], "title")
    text(plan["created_by"], "created_by")
    pin = shape(plan["template"], ("template_id", "revision", "content", "content_sha256"), label="template pin")
    identifier(pin["template_id"], "template_id")
    text(pin["revision"], "template revision")
    template = validate_template(pin["content"])
    if content_revision(template) != pin["content_sha256"]:
        raise ContractError("pinned template snapshot hash mismatch")
    reqs = validate_task(plan["task"], template)
    validate_item_scope(plan["work_items"], {r["id"] for r in reqs}, stored=True)
    progress = []
    for item in plan["work_items"]:
        if not isinstance(item["status"], str) or item["status"] not in STATUSES:
            raise ContractError("invalid work item status")
        _validate_records(item)
        _validate_records(item, exception=True)
        detail = item_progress(item)
        if item["status"] == "complete" and not detail["ready"]:
            raise ContractError(f"work item {item['id']} cannot complete without reviewed evidence or accepted exceptions")
        progress.append(detail)
    complete = all(i["status"] == "complete" for i in plan["work_items"])
    return {"valid": True, "complete": complete, "work_items": progress}


def render_plan(plan):
    task = plan["task"]
    pin = plan["template"]
    sections = [f"# {task['title']}", "Rewards > gates: reward useful outcomes with evidence, not tool-call counts.",
                "## Purpose", task["purpose"], "## What good looks like", task["good"],
                "## Governing template snapshot",
                f"Template `{pin['template_id']}` at content revision `{pin['revision']}`. Requirements remain pinned.",
                render_template(pin["content"]).replace("\n## ", "\n### ").replace("# ", "### ", 1)]
    for key in TASK_ADDITIONS:
        if key in task:
            sections.append(f"## Task {key}")
            sections.extend(f"- {v['id']}: {v['description']} Evidence: {v['evidence']}" if isinstance(v, dict) else f"- {v}" for v in task[key])
    sections += ["## Work items and evidence", "Scoped work tracks reported evidence and explicit review separately."]
    for item in plan["work_items"]:
        sections += [f"### {item['id']}: {item['title']}",
                     f"Owner: {item['owner']}. Status: {item['status']}. Requirements: {', '.join(item['requirement_ids'])}."]
        for key in ("evidence", "exceptions"):
            for record in item[key]:
                content = (f"{record['summary']} — {record['reference']}" if key == "evidence" else
                           f"{record['reason']} — alternative: {record['alternative']}")
                sections.append(f"- {key} `{record['id']}` for `{record['requirement_id']}`, reported by {record['reported_by']}: {content}")
                for review in record["reviews"]:
                    sections.append(f"  - Review {review['verdict']} by {review['reviewed_by']}: {review['note']}")
    sections += ["## Completion", "Complete with reviewed evidence/accepted exceptions." if validate_plan(plan)["complete"] else
                 "Not complete. Status alone never establishes achievement; reported evidence needs explicit review."]
    return "\n\n".join(sections)


def create_plan(store, request, actor):
    shape(request, ("action", "plan_id", "template_id", "template_revision", "task"), label="create request")
    plan_id = identifier(request["plan_id"], "plan_id")
    review = review_template(store, request["template_id"])
    check_revision(request["template_revision"], review["revision"])
    shape(request["task"], (*TASK_FIELDS, "work_items"), TASK_ADDITIONS, label="task")
    task = deepcopy(request["task"])
    items = task.pop("work_items")
    reqs = validate_task(task, review["template"])
    validate_item_scope(items, {r["id"] for r in reqs})
    for item in items:
        item.update(status="pending", evidence=[], exceptions=[])
    plan = {"kind": "plan", "schema": 1, "plan_id": plan_id, "title": task["title"], "created_by": actor,
            "template": {"template_id": review["template_id"], "revision": review["revision"],
                         "content": deepcopy(review["template"]), "content_sha256": content_revision(review["template"])},
            "task": task, "work_items": items}
    validation = validate_plan(plan)
    result = store.save("plans", plan_id, plan, render_plan(plan), actor=actor, expected=None)
    return {**result, "plan": plan, "validation": validation}


def read_plan(store, plan_id):
    entry = store.read("plans", plan_id)
    if entry["contract"].get("plan_id") != plan_id:
        raise ContractError("plan identity does not match file")
    validation = validate_plan(entry["contract"])
    return {"path": entry["path"], "revision": entry["revision"], "plan": entry["contract"],
            "validation": validation, "markdown": entry["markdown"]}


def append_reports(item, reports, actor, *, exception=False):
    key = "exceptions" if exception else "evidence"
    existing = {e["id"] for e in item[key]}
    for report in sequence(reports, key):
        _report(report, exception)
        if report["id"] in existing:
            raise ContractError(f"duplicate {key} ID; reports are append-only")
        if report["requirement_id"] not in item["requirement_ids"]:
            raise ContractError("evidence/exception is outside selected work-item scope")
        existing.add(report["id"])
        item[key].append({**deepcopy(report), "reported_by": actor, "reported_at": now(), "reviews": []})


def append_reviews(item, reviews, actor, *, exception=False):
    key = "exceptions" if exception else "evidence"
    target = "exception_id" if exception else "evidence_id"
    records = {e["id"]: e for e in item[key]}
    for review in sequence(reviews, "reviews"):
        _review(review, target)
        record = records.get(review[target])
        if record is None:
            raise ContractError(f"review target not in this work item: {review[target]}")
        record["reviews"].append({"verdict": review["verdict"], "note": review["note"],
                                  "reviewed_by": actor, "reviewed_at": now()})


def update_plan(store, request, actor):
    changes = ("status", "evidence", "reviews", "exceptions", "exception_reviews")
    shape(request, ("action", "plan_id", "revision", "work_item_id"), changes, label="update request")
    if not any(k in request for k in changes):
        raise ContractError("update requires a scoped change")
    current = read_plan(store, request["plan_id"])
    check_revision(request["revision"], current["revision"])
    work_item_id = identifier(request["work_item_id"], "work_item_id")
    plan = deepcopy(current["plan"])
    item = next((i for i in plan["work_items"] if i["id"] == work_item_id), None)
    if item is None:
        raise ContractError("work item not found")
    # Reviews deliberately precede new reports: a single operation cannot both
    # report a new achievement/exception and silently mark it accepted.
    append_reviews(item, request.get("reviews", []), actor)
    append_reviews(item, request.get("exception_reviews", []), actor, exception=True)
    append_reports(item, request.get("evidence", []), actor)
    append_reports(item, request.get("exceptions", []), actor, exception=True)
    if "status" in request:
        item["status"] = request["status"]
    validation = validate_plan(plan)
    result = store.save("plans", request["plan_id"], plan, render_plan(plan), actor=actor,
                        expected=request["revision"])
    return {**result, "plan": plan, "validation": validation}
