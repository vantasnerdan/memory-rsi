"""Exact Markdown section edits: preserve unrelated bytes, prefer revision over accretion.

Section IDs identify exact content/occurrence within the CAS-pinned policy, not a
semantic promise that obligations survived. Semantic preservation still needs review.
The existing policy byte/character ceilings are storage safety limits, not targets.
"""
from __future__ import annotations

import re

from agent_memory.contracts_store import ContractError, shape
from agent_memory.policy import MAX_BODY_CHARS, content_revision
from agent_memory.rsi_schema import string

_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*(?:\r?\n)?$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _headings(body):
    offset, fenced, headings = 0, None, []
    for number, line in enumerate(body.splitlines(keepends=True), 1):
        fence = _FENCE.match(line)
        if fence:
            token = fence.group(1)
            remainder = line[fence.end():].rstrip("\r\n")
            if fenced is None:
                # CommonMark: backtick opener info strings cannot contain a backtick.
                if token[0] != "`" or "`" not in remainder:
                    fenced = token
            elif token[0] == fenced[0] and len(token) >= len(fenced) and not remainder.strip(" \t"):
                # A fence prefix followed by content is code, not a closing fence.
                fenced = None
        elif fenced is None:
            heading = _HEADING.match(line)
            if heading:
                headings.append((offset, number, len(heading.group(1)), heading.group(2).strip()))
        offset += len(line)
    return headings


def section_index(body):
    """Return complete exact top-level policy sections (nested headings stay inside)."""
    string(body, "policy body", MAX_BODY_CHARS)
    headings = _headings(body)
    levels = [item[2] for item in headings]
    if 2 in levels:
        level = 2
    elif levels.count(1) > 1:
        level = 1
    else:
        deeper = [value for value in levels if value > 1]
        level = min(deeper) if deeper else 1
    boundaries = [item for item in headings if item[2] == level]
    if not boundaries or boundaries[0][0] != 0:
        boundaries.insert(0, (0, 1, 0, "Policy introduction"))
    result, occurrences = [], {}
    for position, (start, start_line, depth, title) in enumerate(boundaries):
        end = boundaries[position + 1][0] if position + 1 < len(boundaries) else len(body)
        text = body[start:end]
        if not text:
            continue
        revision = content_revision(text)
        count = occurrences.get(revision, 0)
        occurrences[revision] = count + 1
        result.append({"section_id": f"section-{revision[7:23]}-{count}", "title": title,
                       "revision": revision, "level": depth, "start": start, "end": end,
                       "start_line": start_line, "end_line": start_line + len(text.splitlines()) - 1,
                       "body": text})
    return result


def apply_section_edits(original, edits):
    """Replace/merge/retire explicitly selected sections against one original snapshot.

    No edits are applied sequentially to shifting offsets. A merge selects adjacent
    sections in document order. Unselected bytes are retained exactly.
    """
    if not isinstance(edits, list) or not edits or len(edits) > 32:
        raise ContractError("edits must be a nonempty list of at most 32 section operations")
    sections = section_index(original)
    by_id = {section["section_id"]: (index, section) for index, section in enumerate(sections)}
    used, changes = set(), []
    for edit in edits:
        shape(edit, ("operation", "section_ids", "reason"), ("body",), label="section edit")
        operation = edit["operation"]
        if operation not in ("replace", "merge", "retire"):
            raise ContractError("section operation must be replace, merge, or retire")
        string(edit["reason"], "section edit reason", 4096)
        ids = edit["section_ids"]
        if not isinstance(ids, list) or not ids or any(not isinstance(key, str) for key in ids):
            raise ContractError("section_ids must be a nonempty list of section IDs")
        if len(ids) != len(set(ids)) or any(key in used for key in ids):
            raise ContractError("section edits overlap or repeat an ID")
        if any(key not in by_id for key in ids):
            raise ContractError("unknown or stale section ID; read current section inventory")
        positions = [by_id[key][0] for key in ids]
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise ContractError("merge/retirement section IDs must be adjacent in document order")
        if operation == "replace" and len(ids) != 1:
            raise ContractError("replace edits exactly one section; use merge for multiple sections")
        if operation == "merge" and len(ids) < 2:
            raise ContractError("merge requires at least two adjacent sections")
        if operation == "retire":
            if "body" in edit:
                raise ContractError("retire has no body; its reason must justify removing obsolete guidance")
            replacement = ""
        else:
            replacement = string(edit.get("body"), "replacement section body", MAX_BODY_CHARS)
        first, last = by_id[ids[0]][1], by_id[ids[-1]][1]
        if replacement and last["end"] < len(original) and not replacement.endswith("\n"):
            raise ContractError("replacement before another section must end with a newline")
        if replacement and first["start"] and not original[:first["start"]].endswith("\n"):
            raise ContractError("section boundary is not a complete line")
        changes.append({"operation": operation, "section_ids": ids, "reason": edit["reason"],
                        "start": first["start"], "end": last["end"], "body": replacement})
        used.update(ids)
    candidate, cursor = "", 0
    for change in sorted(changes, key=lambda item: item["start"]):
        candidate += original[cursor:change["start"]] + change["body"]
        cursor = change["end"]
    candidate += original[cursor:]
    string(candidate, "resulting policy", MAX_BODY_CHARS)
    if "<!-- memory-rsi:policy:" in candidate:
        raise ContractError("policy body cannot contain managed instruction markers")
    return candidate, [{key: value for key, value in change.items() if key not in ("start", "end", "body")} for change in changes]


def revision_metrics(original, candidate):
    """Describe growth/reuse without assigning a desired small size or promotion score."""
    old, new = section_index(original), section_index(candidate)
    retained = sum(1 for section in new if any(section["body"] == before["body"] for before in old))
    return {"characters": {"before": len(original), "after": len(candidate), "delta": len(candidate) - len(original)},
            "sections": {"before": len(old), "after": len(new), "unchanged": retained},
            "append_only": candidate.startswith(original) and len(candidate) > len(original),
            "unchanged": candidate == original,
            "guidance": "Length is descriptive, not a target or gate. Prefer rewriting/merging/retiring existing guidance; justify new concepts by evidence and placement. Review semantic preservation independently."}
