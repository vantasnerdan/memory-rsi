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
    shape(value, (target_key, "verdict", "note"), ("blocking", "basis"), label="review")
    identifier(value[target_key], target_key)
    if not isinstance(value["verdict"], str) or value["verdict"] not in ("accepted", "rejected", "concern"):
        raise ContractError("review verdict must be accepted, rejected or concern")
    text(value["note"], "review note")
    if "blocking" in value and type(value["blocking"]) is not bool:
        raise ContractError("blocking must be boolean")
    if value.get("blocking"):
        if value["verdict"] == "accepted":
            raise ContractError("an accepted review cannot be blocking")
        text(value.get("basis"), "blocking finding basis")
    elif "basis" in value:
        text(value["basis"], "finding basis")


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
        shape(record, (*report_fields, "reported_by", "reported_at", "reviews"), ("outcome_generation",), label=key)
        _report({k: record[k] for k in report_fields}, exception)
        if record["id"] in seen or record["requirement_id"] not in item["requirement_ids"]:
            raise ContractError("duplicate record ID or evidence outside work-item scope")
        seen.add(record["id"])
        text(record["reported_by"], "reported_by")
        text(record["reported_at"], "reported_at")
        if "outcome_generation" in record and (type(record["outcome_generation"]) is not int or record["outcome_generation"] < 0):
            raise ContractError("outcome_generation must be a nonnegative integer")
        for review in sequence(record["reviews"], "reviews"):
            shape(review, ("verdict", "note", "reviewed_by", "reviewed_at"), ("blocking", "basis"), label="stored review")
            _review({"record_id": record["id"], **{k: review[k] for k in ("verdict", "note", "blocking", "basis") if k in review}}, "record_id")
            text(review["reviewed_by"], "reviewed_by")
            text(review["reviewed_at"], "reviewed_at")


def _accepted(record):
    return bool(record["reviews"] and record["reviews"][-1]["verdict"] == "accepted")


def _usable(record):
    if not record["reviews"]:
        return True
    last = record["reviews"][-1]
    return last["verdict"] != "rejected" and not last.get("blocking", False)


def item_progress(item, *, optional=(), withdrawn=(), generation=0):
    evidenced = {e["requirement_id"] for e in item["evidence"] if _usable(e) and e.get("outcome_generation", 0) == generation}
    reviewed = {e["requirement_id"] for e in item["evidence"] if _accepted(e) and e.get("outcome_generation", 0) == generation}
    excepted = {e["requirement_id"] for e in item["exceptions"] if _accepted(e) and e.get("outcome_generation", 0) == generation}
    required = set(item["requirement_ids"]) - set(optional) - set(withdrawn)
    missing = sorted(required - evidenced - excepted)
    pending_exceptions = [e["id"] for e in item["exceptions"] if not e["reviews"] and e["requirement_id"] in missing and e.get("outcome_generation", 0) == generation]
    blockers = [e["id"] for e in item["evidence"] + item["exceptions"] if e.get("outcome_generation", 0) == generation
                and e["requirement_id"] in required and e["reviews"] and e["reviews"][-1].get("blocking") is True]
    return {"work_item_id": item["id"], "reported_evidence": len(item["evidence"]),
            "reviewed_requirements": sorted(reviewed), "accepted_exceptions": sorted(excepted),
            "missing_requirements": missing, "pending_exceptions": pending_exceptions,
            "process_unreported": sorted((set(item["requirement_ids"]) & set(optional)) - evidenced - excepted),
            "blocking_findings": blockers, "ready": not missing and not blockers}


def _direction(plan):
    records = plan.get("steering", [])
    generation, outcome, withdrawn, exemptions = 0, plan["task"]["good"], set(), set()
    template = plan["template"]["content"]
    process = {r["id"] for key in ("achievements", "validation") for r in template[key] if r.get("scope") == "process"}
    # Existing plans pin an older built-in coding template without scope tags.
    # Recognize only exact historical built-in descriptions, never custom text.
    legacy = {
        "ast": "Use AST-aware exploration to map affected symbols before modifying.",
        "lsp": "Use available LSP navigation/diagnostics; otherwise record a capability exception.",
        "design": "Respect SRP and separation of concerns; avoid god files and god functions.",
        "gitops": "Use GitOps: inspect diffs and state, preserve unrelated changes, and report delivery accurately.",
    }
    if plan["template"]["template_id"] == "coding":
        process.update(r["id"] for r in template["achievements"]
                       if "scope" not in r and legacy.get(r["id"]) == r["description"])
    task_ids = {r["id"] for key in ("achievements", "validation") for r in plan["task"].get(key, [])}
    for item in sequence(records, "steering"):
        shape(item, ("instruction", "actor", "at"), ("outcome", "withdraw_requirements", "process_exemptions"), label="steering")
        for key in ("instruction", "actor", "at"): text(item[key], key)
        if "outcome" in item:
            text(item["outcome"], "outcome")
            if item["outcome"] != outcome:
                generation += 1
                outcome = item["outcome"]
        for key, allowed, target in (("withdraw_requirements", task_ids, withdrawn), ("process_exemptions", process, exemptions)):
            if key in item:
                ids = sequence(item[key], key)
                if any(not isinstance(i, str) or i not in allowed for i in ids) or len(ids) != len(set(ids)):
                    raise ContractError(f"{key} must name distinct applicable {('task' if key == 'withdraw_requirements' else 'process')} requirements")
                if key == "withdraw_requirements" and "outcome" not in item:
                    raise ContractError("withdrawing an outcome requirement requires an explicit revised outcome")
                target.update(ids)
    return {"outcome": outcome, "outcome_generation": generation, "withdrawn_requirements": sorted(withdrawn),
            "process_exemptions": sorted(exemptions), "process_requirements": sorted(process),
            "latest_instruction": records[-1]["instruction"] if records else None}


def validate_plan(plan):
    shape(plan, ("kind", "schema", "plan_id", "title", "created_by", "template", "task", "work_items"), ("steering",), label="plan contract")
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
    direction = _direction(plan)
    progress = []
    for item in plan["work_items"]:
        if not isinstance(item["status"], str) or item["status"] not in STATUSES:
            raise ContractError("invalid work item status")
        _validate_records(item)
        _validate_records(item, exception=True)
        detail = item_progress(item, optional=direction["process_requirements"], withdrawn=direction["withdrawn_requirements"], generation=direction["outcome_generation"])
        if item["status"] == "complete" and not detail["ready"]:
            raise ContractError(f"work item {item['id']} cannot complete without evidence for its current outcome or accepted exceptions")
        progress.append(detail)
    complete = all(i["status"] == "complete" for i in plan["work_items"])
    return {"valid": True, "complete": complete, "direction": direction, "work_items": progress}


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
    direction = _direction(plan)
    if direction["latest_instruction"]:
        sections += ["## Current direction", direction["latest_instruction"],
                     f"Current intended outcome: {direction['outcome']}",
                     "The original task and direction history remain below as recorded; apply the latest direction to the next action."]
        for item in plan["steering"]:
            sections.append(f"- {item['at']} — {item['actor']}: {item['instruction']}" + (f" (revised outcome: {item['outcome']})" if 'outcome' in item else ""))
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
    sections += ["## Completion", "Complete with evidence for the current requested outcome." if validate_plan(plan)["complete"] else
                 "Not complete. Keep the current outcome visible and record evidence appropriate to the change."]
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


def append_reports(item, reports, actor, *, exception=False, generation=0):
    key = "exceptions" if exception else "evidence"
    existing = {e["id"] for e in item[key]}
    for report in sequence(reports, key):
        _report(report, exception)
        if report["id"] in existing:
            raise ContractError(f"duplicate {key} ID; reports are append-only")
        if report["requirement_id"] not in item["requirement_ids"]:
            raise ContractError("evidence/exception is outside selected work-item scope")
        existing.add(report["id"])
        item[key].append({**deepcopy(report), "reported_by": actor, "reported_at": now(), "reviews": [], "outcome_generation": generation})


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
                                  **{k: review[k] for k in ("blocking", "basis") if k in review},
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
    generation = _direction(plan)["outcome_generation"]
    # A caller may report and review in one update. Actor labels record who
    # submitted it; they do not turn a self-review into external approval.
    append_reports(item, request.get("evidence", []), actor, generation=generation)
    append_reports(item, request.get("exceptions", []), actor, exception=True, generation=generation)
    append_reviews(item, request.get("reviews", []), actor)
    append_reviews(item, request.get("exception_reviews", []), actor, exception=True)
    if "status" in request:
        item["status"] = request["status"]
    validation = validate_plan(plan)
    result = store.save("plans", request["plan_id"], plan, render_plan(plan), actor=actor,
                        expected=request["revision"])
    return {**result, "plan": plan, "validation": validation}


def steer_plan(store, request, actor):
    """Persist explicit direction without rewriting the original user task."""
    shape(request, ("action", "plan_id", "revision", "instruction"),
          ("outcome", "withdraw_requirements", "process_exemptions"), label="steer request")
    current = read_plan(store, request["plan_id"])
    check_revision(request["revision"], current["revision"])
    plan = deepcopy(current["plan"])
    before = _direction(plan)
    item = {"instruction": text(request["instruction"], "instruction"), "actor": actor, "at": now()}
    for key in ("outcome", "withdraw_requirements", "process_exemptions"):
        if key in request: item[key] = deepcopy(request[key])
    plan.setdefault("steering", []).append(item)
    after = _direction(plan)
    if after["outcome_generation"] != before["outcome_generation"]:
        for work in plan["work_items"]:
            if work["status"] == "complete": work["status"] = "in_progress"
    validation = validate_plan(plan)
    result = store.save("plans", request["plan_id"], plan, render_plan(plan), actor=actor, expected=request["revision"])
    return {**result, "plan": plan, "validation": validation, "next_instruction": item["instruction"]}
