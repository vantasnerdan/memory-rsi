"""Immutable searchable RSI artifacts using existing storage and Git primitives.

Cooperating writers use ContractStore's lock. Artifact writes reuse policy's
fsync/atomic-replace primitive; immutable IDs never overwrite an existing path.
Hash verification detects corruption, not malicious authorship: these are not
signed records. Filesystem actors ignoring advisory locks are not participants.
Publication is separate and MUST be invoked only after releasing store locks.
"""
from __future__ import annotations

import os

import yaml

from agent_memory.contracts_store import ContractError, ContractStore, identifier, shape
from agent_memory.plans import validate_plan
from agent_memory.policy import (
    MAX_BODY_CHARS as MAX_POLICY_CHARS,
    PolicyStore, _atomic_write, _git_prepare, content_revision,
)
from agent_memory.policy_git import commit_git, push_git
from agent_memory.rsi_sections import apply_section_edits, revision_metrics
from agent_memory.rsi_schema import (
    KINDS, MAX_ARTIFACT_BYTES, MAX_CONTEXT_BYTES, PROVENANCE,
    artifact_refs, artifact_revision, bindings, body, data_object, json_text, parse_json,
    plan_ids, revision, same_bindings, string,
)


class ContainedPolicyStore(PolicyStore):
    """Add the contract store's ancestor/hardlink checks to policy operations.

    The optional local-commit callback runs after the durable event, while
    PolicyStore.execute still holds its policy writer lock. The callback must
    never perform network I/O; canonical update/history semantics stay delegated.
    """

    def __init__(self, contracts):
        self.contracts = contracts
        self.event_path = None
        self.commit_event = None
        contracts._safe(contracts.base)
        super().__init__(contracts.base)

    def path(self, relative=None):
        path = super().path() if relative is None else super().path(relative)
        return self.contracts._safe(path)

    def _event(self, *args, **kwargs):
        self.event_path = super()._event(*args, **kwargs)
        if self.commit_event is not None:
            self.commit_event(self.event_path)
        return self.event_path


def validate_artifact(value):
    shape(value, ("schema", "id", "kind", "created_at", "created_by", "bindings", "data"), label="RSI artifact")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise ContractError("unsupported RSI artifact schema")
    identifier(value["id"])
    if not isinstance(value["kind"], str) or value["kind"] not in KINDS:
        raise ContractError("invalid RSI artifact kind")
    string(value["created_at"], "created_at", 128)
    string(value["created_by"], "created_by", 128)
    bound = bindings(value["bindings"])
    if value["kind"] != "proposal":
        data_object(value["data"])
        if value["kind"] == "evaluation" and "proposal_id" not in bound:
            raise ContractError("evaluation requires proposal binding")
        return value
    if "proposal_id" in bound:
        raise ContractError("proposal cannot bind another proposal")
    data = shape(value["data"], ("body", "body_hash", "reason", "parent_policy", "plans"),
                 ("source_artifacts", "edits", "edit_summary", "metrics"), label="proposal data")
    sources = artifact_refs(data.get("source_artifacts", []))
    if len(sources) > 32:
        raise ContractError("proposal source_artifacts must contain at most 32 references")
    body(data["body"])
    if data["body_hash"] != content_revision(data["body"]):
        raise ContractError("candidate body hash mismatch")
    string(data["reason"], "reason", 4096)
    policy = shape(data["parent_policy"], ("body", "revision", "path", "exists"), label="policy snapshot")
    string(policy["body"], "parent policy body", MAX_POLICY_CHARS)
    string(policy["path"], "policy snapshot path", 4096)
    if type(policy["exists"]) is not bool:
        raise ContractError("policy snapshot exists must be a boolean")
    if policy["revision"] != bound["policy_revision"] or content_revision(policy["body"]) != policy["revision"]:
        raise ContractError("parent policy snapshot hash mismatch")
    if "edits" in data:
        replayed, summary = apply_section_edits(policy["body"], data["edits"])
        if replayed != data["body"] or summary != data.get("edit_summary"):
            raise ContractError("proposal section edits do not match candidate snapshot")
    elif "edit_summary" in data:
        raise ContractError("proposal edit_summary requires replayable edits")
    if "metrics" in data and data["metrics"] != revision_metrics(policy["body"], data["body"]):
        raise ContractError("proposal revision metrics mismatch")
    if not isinstance(data["plans"], list):
        raise ContractError("proposal plans must be a list")
    snapshots = []
    for plan in data["plans"]:
        shape(plan, ("plan_id", "revision", "plan", "validation"), label="plan snapshot")
        identifier(plan["plan_id"], "plan_id")
        revision(plan["revision"])
        if plan["plan"].get("plan_id") != plan["plan_id"]:
            raise ContractError("plan snapshot identity mismatch")
        if validate_plan(plan["plan"]) != plan["validation"]:
            raise ContractError("plan snapshot validation mismatch")
        snapshots.append({"plan_id": plan["plan_id"], "revision": plan["revision"]})
    plan_ids([p["plan_id"] for p in snapshots])
    if not same_bindings(bound, {"policy_revision": policy["revision"], "plans": snapshots, "artifacts": sources}):
        raise ContractError("proposal snapshot binding mismatch")
    json_text({"policy": policy, "plans": data["plans"]}, limit=MAX_CONTEXT_BYTES)
    return value


def render_artifact(value):
    """One canonical rendering makes frontmatter AND searchable text verifiable."""
    metadata = {
        "description": f"RSI {value['kind']} {value['id']} (unauthenticated provenance)",
        "author": value["created_by"], "created": value["created_at"],
        "updated": value["created_at"], "category": "efforts", "status": "active",
        "confidence": "exploratory", "tags": ["rsi", value["kind"], "provenance"],
        "rsi": {"schema": 1, "id": value["id"], "kind": value["kind"], "revision": artifact_revision(value)},
    }
    raw = ("---\n" + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True) +
           f"---\n\n# RSI {value['kind']}: {value['id']}\n\n" + PROVENANCE +
           "\n\n## Immutable record\n\nExact revision-bound payload; inspect freshness through memory rsi read.\n\n```json\n" +
           json_text(value, limit=MAX_ARTIFACT_BYTES, pretty=True) + "\n```\n")
    if len(raw.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise ContractError("RSI artifact exceeds file size limit")
    return raw


class RSIStore:
    def __init__(self, base, *, no_git=False, allow_non_main_branch=False):
        self.contracts = ContractStore(base, no_git=no_git, allow_non_main_branch=allow_non_main_branch)
        self.policy = ContainedPolicyStore(self.contracts)
        self.no_git = no_git
        self.allow_non_main_branch = allow_non_main_branch
        self.publication = None

    def path(self, entry_id):
        identifier(entry_id)
        return self.contracts._safe(self.contracts.base / "shared" / "efforts" / f"rsi-{entry_id}.md")

    def read(self, entry_id):
        path = self.path(entry_id)
        if not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise ContractError("RSI artifact not found or not a bounded regular file")
        try:
            raw = path.read_bytes().decode("utf-8")
            start = raw.index("\n```json\n") + len("\n```json\n")
            if not raw.endswith("\n```\n"):
                raise ContractError("invalid RSI artifact framing")
            value = parse_json(raw[start:-len("\n```\n")], limit=MAX_ARTIFACT_BYTES)
            validate_artifact(value)
            if value["id"] != entry_id or render_artifact(value) != raw:
                raise ContractError("RSI artifact content hash/rendering mismatch")
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
            raise ContractError("RSI artifact integrity validation failed") from exc
        return value

    def lookup(self, entry_id):
        """Only genuine absence becomes None; unsafe/corrupt/unreadable files fail."""
        path = self.path(entry_id)
        try:
            path.stat()
        except FileNotFoundError:
            return None
        return self.read(entry_id)

    def prepare(self, paths):
        return _git_prepare(self.contracts.base, self.no_git, self.allow_non_main_branch, paths)

    def save(self, value):
        """Caller holds contracts then policy lock; no network in this method."""
        validate_artifact(value)
        path = self.path(value["id"])
        if path.exists():
            raise ContractError("immutable RSI artifact ID already exists")
        raw = render_artifact(value)
        repo = self.prepare([path])
        self.contracts._safe(path.parent).mkdir(parents=True, exist_ok=True)
        self.contracts._safe(path)
        _atomic_write(path, raw)
        self.commit_local(repo, [path])
        return repo, [path]

    def commit_local(self, repo, paths):
        """Only local Git under writer locks; capture this operation's exact SHA."""
        self.publication = commit_git(repo, paths, no_git=self.no_git,
                                      message="memory: persist immutable RSI record/promotion")

    def publish(self, repo, paths):
        """Network-only phase: never stage mutable paths after releasing locks."""
        if self.publication is None:
            return {"status": "git_failed", "committed": False, "pushed": False,
                    "error": "No local commit was prepared; publication stopped"}
        return push_git(self.publication)

    def list_ids(self):
        directory = self.path("probe").parent
        if not directory.exists():
            return []
        ids = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.startswith("rsi-") and entry.name.endswith(".md"):
                    ids.append(identifier(entry.name[4:-3]))
                    if len(ids) > 10000:
                        raise ContractError("RSI listing exceeds 10000 artifact limit")
        return sorted(ids)

    def exported(self, value):
        return {**value, "revision": artifact_revision(value), "path": str(self.path(value["id"])),
                "provenance": PROVENANCE}
