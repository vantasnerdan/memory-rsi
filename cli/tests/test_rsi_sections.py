"""Section revision preserves bytes and identity, not an arbitrary line budget."""
import pytest

from agent_memory.contracts_store import ContractError
from agent_memory.rsi_sections import apply_section_edits, revision_metrics, section_index

POLICY = "# Rewards > gates\n\nKeep useful outcomes.\n\n## Evidence\nReview outcomes.\n\n### Examples\nPreserve nested guidance.\n\n## Change\nKeep revisions and rollback.\n"


def test_exact_sections_keep_intro_and_nested_headings():
    sections = section_index(POLICY)
    assert [item["title"] for item in sections] == ["Policy introduction", "Evidence", "Change"]
    assert "### Examples" in sections[1]["body"]
    assert "".join(item["body"] for item in sections) == POLICY
    assert all(item["revision"].startswith("sha256:") for item in sections)


def test_replace_preserves_unselected_bytes_and_allows_useful_growth():
    sections = section_index(POLICY)
    replacement = "## Evidence\n" + "A meaningful distinct example with reviewed outcomes.\n" * 30
    candidate, summary = apply_section_edits(POLICY, [{"operation": "replace", "section_ids": [sections[1]["section_id"]], "reason": "Clarify necessary examples instead of appending another rule", "body": replacement}])
    assert candidate == sections[0]["body"] + replacement + sections[2]["body"]
    assert summary[0]["operation"] == "replace"
    metrics = revision_metrics(POLICY, candidate)
    assert metrics["characters"]["delta"] > 0
    assert metrics["sections"]["unchanged"] == 2
    assert metrics["append_only"] is False
    assert "not a target" in metrics["guidance"]


def test_merge_adjacent_sections_and_retire_obsolete_content():
    sections = section_index(POLICY)
    merged = "## Reviewed changes\nPreserve evidence, revisions and rollback.\n"
    candidate, _ = apply_section_edits(POLICY, [{"operation": "merge", "section_ids": [item["section_id"] for item in sections[1:]], "reason": "One coherent concept replaces overlapping guidance", "body": merged}])
    assert candidate == sections[0]["body"] + merged
    retired, _ = apply_section_edits(POLICY, [{"operation": "retire", "section_ids": [sections[2]["section_id"]], "reason": "Demonstrated redundant material; requires semantic preservation review"}])
    assert retired == sections[0]["body"] + sections[1]["body"]


def test_fenced_headings_are_not_sections_and_crlf_preserved():
    text = "# Policy\r\n\r\n## Instructions\r\n```md\r\n## Example only\r\n```\r\n## Final\r\nKeep.\r\n"
    sections = section_index(text)
    assert len(sections) == 3
    assert "Example only" in sections[1]["body"]
    assert "".join(item["body"] for item in sections) == text


def test_fence_prefix_with_content_does_not_close_code_block():
    text = "# Policy\n## Guidance\n```text\n```not-a-closing-fence\n## Example heading inside code\n...\n```\n## Evidence\nKeep.\n"
    sections = section_index(text)
    assert [item["title"] for item in sections] == ["Policy introduction", "Guidance", "Evidence"]
    assert "## Example heading inside code" in sections[1]["body"]
    assert "".join(item["body"] for item in sections) == text


def test_backtick_in_opener_info_is_not_a_fence():
    text = "# Policy\n```invalid`info\n## Visible heading\nKeep.\n"
    assert [item["title"] for item in section_index(text)] == ["Policy introduction", "Visible heading"]


def test_repeated_heading_ids_are_unambiguous_even_identical_content():
    text = "## Same\nRule.\n## Same\nRule.\n"
    sections = section_index(text)
    assert sections[0]["revision"] == sections[1]["revision"]
    assert sections[0]["section_id"] != sections[1]["section_id"]


def test_edits_apply_against_one_snapshot_not_shifting_offsets():
    sections = section_index(POLICY)
    candidate, _ = apply_section_edits(POLICY, [
        {"operation": "replace", "section_ids": [sections[2]["section_id"]], "reason": "Clarify", "body": "## Change\nExplicit review.\n"},
        {"operation": "replace", "section_ids": [sections[1]["section_id"]], "reason": "Replace, not append", "body": "## Evidence\nUseful evidence.\n"},
    ])
    assert candidate == sections[0]["body"] + "## Evidence\nUseful evidence.\n## Change\nExplicit review.\n"


@pytest.mark.parametrize("case", ["unknown", "duplicate", "overlap", "nonadjacent", "merge_single", "replace_multiple", "retire_body", "missing_reason", "unterminated", "all_retired", "empty", "unknown_field"])
def test_invalid_or_ambiguous_edits_fail(case):
    sections = section_index(POLICY)
    ids = [item["section_id"] for item in sections]
    edit = {"operation": "replace", "section_ids": [ids[1]], "reason": "Reviewed reason", "body": "## Evidence\nGood.\n"}
    edits = [edit]
    if case == "unknown": edit["section_ids"] = ["stale-id"]
    elif case == "duplicate": edit.update(operation="merge", section_ids=[ids[1], ids[1]])
    elif case == "overlap": edits = [edit, dict(edit)]
    elif case == "nonadjacent": edit.update(operation="merge", section_ids=[ids[0], ids[2]])
    elif case == "merge_single": edit["operation"] = "merge"
    elif case == "replace_multiple": edit["section_ids"] = ids[:2]
    elif case == "retire_body": edit["operation"] = "retire"
    elif case == "missing_reason": edit.pop("reason")
    elif case == "unterminated": edit["body"] = "## Evidence\nNo final newline"
    elif case == "all_retired": edits = [{"operation": "retire", "section_ids": ids, "reason": "Would leave empty policy"}]
    elif case == "empty": edits = []
    elif case == "unknown_field": edit["approved"] = True
    with pytest.raises(ContractError): apply_section_edits(POLICY, edits)


def test_append_only_metrics_are_diagnostic_not_rejected():
    candidate = POLICY + "\n## Newly needed enduring concept\nA justified new domain.\n"
    metrics = revision_metrics(POLICY, candidate)
    assert metrics["append_only"] is True
    assert metrics["sections"]["after"] > metrics["sections"]["before"]
    assert metrics["unchanged"] is False
