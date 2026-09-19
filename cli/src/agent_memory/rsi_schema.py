"""Bounded, lossless JSON and strict boundary schemas for RSI provenance.

Assessment data is caller-owned provenance, never authenticated evidence or an
approval token. This layer deliberately does not infer authority from model scores.
"""
from __future__ import annotations

import json
import math
import re

from agent_memory.contracts_store import ContractError, identifier, shape
from agent_memory.policy import MAX_BODY_CHARS, content_revision
MAX_DATA_BYTES = 128 * 1024
MAX_CONTEXT_BYTES = 256 * 1024
MAX_REQUEST_BYTES = 512 * 1024
MAX_ARTIFACT_BYTES = 1024 * 1024
MAX_PLAN_FILE_BYTES = 1024 * 1024
MAX_PLAN_IDS = 8
KINDS = ("proposal", "preflight", "evaluation", "reflection", "observation", "mapping", "insight")
MAX_ARTIFACT_REFS = 128
PROVENANCE = (
    "Self-reported, unauthenticated provenance; model judgments are advisory, "
    "not reviewed achievements, human approval, or platform permission. "
    "Store only selected owned state, never credentials or arbitrary file/session dumps."
)


def string(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ContractError(f"{name} must be a nonempty string of at most {limit} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ContractError(f"{name} must be valid UTF-8") from exc
    return value


def revision(value, *, policy=False):
    pattern = r"sha256:[0-9a-f]{64}" if policy else r"[0-9a-f]{64}"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ContractError("invalid policy revision" if policy else "invalid plan revision")
    return value


def plan_ids(value):
    if not isinstance(value, list) or len(value) > MAX_PLAN_IDS:
        raise ContractError(f"plan_ids must be a list of at most {MAX_PLAN_IDS} IDs")
    for entry in value:
        identifier(entry, "plan_id")
    if len(value) != len(set(value)):
        raise ContractError("duplicate plan_id")
    return value


def artifact_refs(value):
    if not isinstance(value, list) or len(value) > MAX_ARTIFACT_REFS:
        raise ContractError(f"artifacts must be a list of at most {MAX_ARTIFACT_REFS} exact references")
    ids = []
    for item in value:
        shape(item, ("id", "revision"), label="artifact reference")
        ids.append(identifier(item["id"], "artifact id"))
        revision(item["revision"], policy=True)
    if len(ids) != len(set(ids)):
        raise ContractError("duplicate artifact reference")
    return value


def bindings(value):
    shape(value, ("policy_revision", "plans"), ("proposal_id", "artifacts"), label="bindings")
    revision(value["policy_revision"], policy=True)
    if not isinstance(value["plans"], list):
        raise ContractError("bindings.plans must be a list")
    ids = []
    for plan in value["plans"]:
        shape(plan, ("plan_id", "revision"), label="plan binding")
        ids.append(plan["plan_id"])
        revision(plan["revision"])
    plan_ids(ids)
    if "proposal_id" in value:
        identifier(value["proposal_id"], "proposal_id")
    if "artifacts" in value:
        artifact_refs(value["artifacts"])
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ContractError("nonfinite JSON number is not allowed")


def json_text(value, *, limit=MAX_REQUEST_BYTES, pretty=False):
    """Reject non-JSON values, excessive complexity, and lossy JS integers."""
    count = 0

    def visit(item, depth):
        nonlocal count
        count += 1
        if depth > 32 or count > 30000:
            raise ContractError("JSON nesting or node limit exceeded")
        if item is None or type(item) is bool:
            return
        if type(item) is int:
            if abs(item) > 9007199254740991:
                raise ContractError("JSON integer exceeds lossless JavaScript range")
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise ContractError("nonfinite JSON number is not allowed")
            if item.is_integer() and abs(item) > 9007199254740991:
                raise ContractError("JSON integer exceeds lossless JavaScript range")
            return
        if type(item) is str:
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ContractError("JSON string must be valid UTF-8") from exc
            return
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ContractError("JSON object keys must be strings")
                visit(key, depth + 1)
                visit(child, depth + 1)
            return
        if type(item) is list:
            for child in item:
                visit(child, depth + 1)
            return
        raise ContractError("value must be lossless JSON")

    visit(value, 0)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                     indent=2 if pretty else None, separators=None if pretty else (",", ":"))
    if len(raw.encode("utf-8")) > limit:
        raise ContractError(f"JSON exceeds {limit} byte limit")
    return raw


def parse_json(raw, *, limit=MAX_REQUEST_BYTES):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > limit:
        raise ContractError(f"JSON exceeds {limit} byte limit")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (ValueError, RecursionError) as exc:
        # Do not echo potentially secret request content in syntax diagnostics.
        raise ContractError("invalid JSON request or duplicate/nonfinite value") from exc
    json_text(value, limit=limit)
    return value


def data_object(value):
    if type(value) is not dict:
        raise ContractError("data must be a JSON object")
    json_text(value, limit=MAX_DATA_BYTES)
    return value


def body(value):
    string(value, "body", MAX_BODY_CHARS)
    if "<!-- memory-rsi:policy:" in value:
        raise ContractError("candidate body cannot contain managed instruction markers")
    return value


def same_bindings(left, right):
    """Reference order is not semantic; identities and exact revisions are."""
    return (left["policy_revision"] == right["policy_revision"] and
            {p["plan_id"]: p["revision"] for p in left["plans"]} ==
            {p["plan_id"]: p["revision"] for p in right["plans"]} and
            {p["id"]: p["revision"] for p in left.get("artifacts", [])} ==
            {p["id"]: p["revision"] for p in right.get("artifacts", [])})


def artifact_revision(value):
    return content_revision(json_text(value, limit=MAX_ARTIFACT_BYTES))
