"""Reward-first built-ins and revision-checked shared template editing."""
from __future__ import annotations

from copy import deepcopy

from agent_memory.contracts_store import (
    ContractError, check_revision, content_revision, identifier, sequence, shape, text,
)


GENERAL = {
    "title": "General reward-first plan",
    "purpose": "Deliver useful outcomes, not tool-call counts or performative gates.",
    "good": "The requested outcome is useful, evidenced, and honestly reviewed.",
    "achievements": [
        {"id": "outcome", "description": "Deliver the requested useful outcome.",
         "evidence": "Inspect the resulting artifact or observable behavior against the task."},
    ],
    "steps": ["Understand the goal and constraints.", "Do scoped work toward the outcome.",
              "Validate, review evidence, and explain remaining limitations."],
    "validation": [
        {"id": "verification", "description": "Demonstrate the outcome meets acceptance criteria.",
         "evidence": "Record checks, results, artifacts, and limitations; review their relevance."},
    ],
    "boundaries": ["Permissions and safety instructions remain binding.",
                   "Reward outcomes with evidence, never tool-call counts.",
                   "Reported evidence is not reviewed achievement; exceptions must be explicit and accepted."],
}
CODING = deepcopy(GENERAL)
CODING.update(
    title="Coding reward-first plan",
    purpose="Ship maintainable, validated changes that solve the requested problem.",
    good="Small cohesive changes preserve responsibilities and have reviewed behavioral evidence.",
)
CODING["achievements"] += [
    {"id": "ast", "description": "Use AST-aware exploration to map affected symbols before modifying.",
     "evidence": "Record the inspected symbols, dependencies, and resulting implementation decisions."},
    {"id": "lsp", "description": "Use available LSP navigation/diagnostics; otherwise record a capability exception.",
     "evidence": "Record LSP findings, or an accepted exception naming unavailable capability and substitute checks."},
    {"id": "design", "description": "Respect SRP and separation of concerns; avoid god files and god functions.",
     "evidence": "Review responsibility boundaries, interfaces, and focused module/function changes."},
    {"id": "gitops", "description": "Use GitOps: inspect diffs and state, preserve unrelated changes, and report delivery accurately.",
     "evidence": "Record diff review, tests, scoped persistence and authorized commit/push/deployment results or exceptions."},
]
CODING["steps"] = [
    "Map affected symbols using AST and assess LSP capability before editing.",
    "Separate responsibilities and implement focused changes.",
    "Run relevant tests and diagnostics, review the diff, and verify delivery.",
]
CODING["boundaries"] += ["Never silently weaken the pinned requirements of an active plan.",
                           "Do not commit, push, or deploy beyond the user's authorization."]
BUILTINS = {"general": GENERAL, "coding": CODING}
TEMPLATE_FIELDS = ("title", "purpose", "good", "achievements", "steps", "validation", "boundaries")


def requirements(value, label):
    result = sequence(value, label, nonempty=True)
    for item in result:
        shape(item, ("id", "description", "evidence"), label=label)
        identifier(item["id"], "requirement id")
        text(item["description"], "description")
        text(item["evidence"], "evidence expectation")
    return result


def string_list(value, label):
    for item in sequence(value, label, nonempty=True):
        text(item, label)


def validate_template(template):
    shape(template, TEMPLATE_FIELDS, label="template")
    for key in ("title", "purpose", "good"):
        text(template[key], key)
    all_requirements = requirements(template["achievements"], "achievements") + requirements(template["validation"], "validation")
    ids = [r["id"] for r in all_requirements]
    if len(ids) != len(set(ids)):
        raise ContractError("requirement IDs must be unique across achievements and validation")
    for key in ("steps", "boundaries"):
        string_list(template[key], key)
    return template


def render_template(template):
    sections = [f"# {template['title']}", "Rewards > gates: useful outcomes and reviewed evidence come first; safety remains a boundary.",
                "## Purpose", template["purpose"],
                "## What good looks like", template["good"], "## Achievements and evidence"]
    for item in template["achievements"]:
        sections.append(f"- **{item['id']}**: {item['description']} Evidence: {item['evidence']}")
    sections += ["## Steps", *[f"{i}. {step}" for i, step in enumerate(template["steps"], 1)], "## Validation"]
    sections += [f"- **{item['id']}**: {item['description']} Evidence: {item['evidence']}" for item in template["validation"]]
    sections += ["## Boundaries and exceptions", *[f"- {s}" for s in template["boundaries"]]]
    return "\n\n".join(sections)


def review_template(store, template_id):
    identifier(template_id, "template_id")
    path = store.path("templates", template_id)
    if path.exists():
        entry = store.read("templates", template_id)
        contract = entry["contract"]
        shape(contract, ("kind", "schema", "template_id", "template"), label="template contract")
        if contract["kind"] != "template" or type(contract["schema"]) is not int or contract["schema"] != 1 or contract["template_id"] != template_id:
            raise ContractError("invalid template contract identity")
        template = validate_template(contract["template"])
        return {"template_id": template_id, "revision": entry["revision"], "source": "shared",
                "path": entry["path"], "template": template}
    if template_id not in BUILTINS:
        raise ContractError(f"template not found: {template_id}")
    template = deepcopy(BUILTINS[template_id])
    return {"template_id": template_id, "revision": content_revision(template),
            "source": "builtin", "template": template}


def list_templates(store):
    ids = sorted(set(BUILTINS) | set(store.list_ids("templates")))
    summaries = []
    for template_id in ids:
        entry = review_template(store, template_id)
        summaries.append({**{k: entry[k] for k in ("template_id", "revision", "source")},
                          "title": entry["template"]["title"], "purpose": entry["template"]["purpose"]})
    return {"templates": summaries}


def save_template(store, request, actor):
    shape(request, ("action", "template_id", "revision", "template"), label="save_template request")
    template_id = identifier(request["template_id"], "template_id")
    template = validate_template(request["template"])
    # Built-ins can be deliberately overridden, but their reviewed revision is
    # required. A new named template requires explicit JSON null.
    exists = store.path("templates", template_id).exists()
    previous = review_template(store, template_id) if exists or template_id in BUILTINS else None
    check_revision(request["revision"], previous["revision"] if previous else None)
    contract = {"kind": "template", "schema": 1, "template_id": template_id, "template": deepcopy(template)}
    result = store.save("templates", template_id, contract, render_template(template), actor=actor,
                        expected=previous["revision"] if exists else None)
    return {**result, "template_id": template_id, "template": deepcopy(template)}
