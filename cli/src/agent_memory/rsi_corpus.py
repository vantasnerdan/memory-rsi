"""Bounded corpus metadata discovery, independent of inference and policy edits.

Only canonical plan IDs and RSI observation IDs are discoverable. Invalid sources
are reported per ID and advance the ordered cursor; no arbitrary file path input
or raw body/error excerpt is returned. Counts describe provenance, not achievement.
"""
from __future__ import annotations

import os
import stat

from agent_memory.contracts_store import ContractError, identifier, shape
from agent_memory.plans import read_plan
from agent_memory.rsi_schema import (
    MAX_ARTIFACT_BYTES, MAX_CONTEXT_BYTES, MAX_PLAN_FILE_BYTES, artifact_revision, json_text,
)

MAX_CORPUS_FILES = 10000
MAX_PAGE_SCANNED = 128
MAX_PAGE_BYTES = 8 * 1024 * 1024


def _entries(store, kind):
    directory = store.contracts._safe(store.contracts.base / "shared" /
                                      ("plans" if kind == "plans" else "efforts"))
    if not directory.exists():
        return [], 0
    ids, invalid, count = [], 0, 0
    with os.scandir(directory) as entries:
        for entry in entries:
            if not entry.name.endswith(".md"):
                continue
            if kind == "observations" and not entry.name.startswith("rsi-"):
                continue
            count += 1
            if count > MAX_CORPUS_FILES:
                raise ContractError(f"corpus enumeration exceeds {MAX_CORPUS_FILES} source files")
            entry_id = entry.name[:-3] if kind == "plans" else entry.name[4:-3]
            try:
                ids.append(identifier(entry_id))
            except ContractError:
                # Unsafe names cannot be addressable cursors. Report their count
                # without disclosing attacker-controlled paths or blocking IDs.
                invalid += 1
    return sorted(ids), invalid


def _counts(plan):
    result = {"evidence_reports": 0, "exception_reports": 0, "accepted": 0, "rejected": 0, "pending": 0}
    for item in plan["work_items"]:
        for field, counter in (("evidence", "evidence_reports"), ("exceptions", "exception_reports")):
            result[counter] += len(item[field])
            for report in item[field]:
                key = report["reviews"][-1]["verdict"] if report["reviews"] else "pending"
                result[key] += 1
    return result


def _summary(store, kind, entry_id, path):
    if kind == "plans":
        entry = read_plan(store.contracts, entry_id)
        json_text(entry["plan"], limit=MAX_CONTEXT_BYTES)
        title = entry["plan"]["title"]
        return {"kind": "plan", "id": entry_id, "revision": entry["revision"], "path": str(path),
                "title": title[:300], "title_truncated": len(title) > 300,
                "complete": entry["validation"]["complete"], "review_counts": _counts(entry["plan"])}
    value = store.read(entry_id)
    if value["kind"] != "observation":
        return None
    title = value["data"].get("title")
    if not isinstance(title, str) or not title.strip():
        title = f"Observation {entry_id}"
    return {"kind": "observation", "id": entry_id, "revision": artifact_revision(value), "path": str(path),
            "title": title[:300], "title_truncated": len(title) > 300, "complete": None,
            "review_counts": {"evidence_reports": 0, "exception_reports": 0, "accepted": 0, "rejected": 0, "pending": 0}}


def corpus_page(store, request):
    """Caller holds snapshot locks; per-source failures never hide later IDs."""
    shape(request, ("action", "kind"), ("after", "limit"), label="corpus request")
    kind = request["kind"]
    if not isinstance(kind, str) or kind not in ("plans", "observations"):
        raise ContractError("corpus kind must be plans or observations")
    after = identifier(request["after"], "after") if "after" in request else ""
    limit = request.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ContractError("limit must be an integer between 1 and 50")
    ids, invalid_names = _entries(store, kind)
    sources, errors = [], []
    scanned, scanned_bytes, cursor, has_more = 0, 0, after, False
    for entry_id in ids:
        if entry_id <= after:
            continue
        if len(sources) >= limit or scanned >= MAX_PAGE_SCANNED or scanned_bytes >= MAX_PAGE_BYTES:
            has_more = True
            break
        try:
            path = store.contracts.path("plans", entry_id) if kind == "plans" else store.path(entry_id)
            info = path.stat()
            maximum = MAX_PLAN_FILE_BYTES if kind == "plans" else MAX_ARTIFACT_BYTES
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise ContractError("source is not a bounded regular file")
            if scanned_bytes + info.st_size > MAX_PAGE_BYTES:
                has_more = True
                break
            scanned_bytes += info.st_size
            summary = _summary(store, kind, entry_id, path)
            if summary is not None:
                sources.append(summary)
        except (ValueError, OSError, RuntimeError, TypeError, KeyError, AttributeError):
            errors.append({"kind": "plan" if kind == "plans" else "observation", "id": entry_id,
                           "code": "invalid_source", "error": "Source unavailable, unsafe, oversized, or integrity-invalid; skipped without reading arbitrary paths."})
        scanned += 1
        cursor = entry_id
    return {"kind": kind, "sources": sources, "errors": errors,
            "has_more": has_more, "next_after": cursor if has_more else None,
            "scanned": scanned, "scanned_bytes": scanned_bytes, "invalid_file_names": invalid_names,
            "order": "id_ascending", "review_count_guidance": "Latest review per report; self-reported provenance, not independently authenticated achievement."}
