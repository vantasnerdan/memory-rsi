"""Ordinary memory updates must not strip shared contract metadata."""
import pytest

from agent_memory.contracts_cli import execute_request
from agent_memory.writer import update_entry


def test_generic_writer_preserves_plan_contract(tmp_path):
    review = execute_request({"action": "review", "template_id": "general"}, tmp_path, no_git=True)
    created = execute_request(review["create_example"], tmp_path, no_git=True)
    path = tmp_path / "shared/plans/my-plan.md"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Use memory plan"):
        update_entry(path, body="would discard the contract")
    assert path.read_bytes() == before
    assert execute_request({"action": "read", "plan_id": "my-plan"}, tmp_path)["revision"] == created["revision"]
