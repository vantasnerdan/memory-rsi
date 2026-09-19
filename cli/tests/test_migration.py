"""Non-destructive Codex migration: exact bytes, explicit choices, no secrets."""
import os
from pathlib import Path

import pytest

from agent_memory.bootstrap import BootstrapStore
from agent_memory.bootstrap_fs import BootstrapError


@pytest.fixture
def sources(tmp_path):
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text('[mcp_servers.private]\ncommand="never-execute-me"\n')
    (home / "auth.json").write_text('{"token":"secret"}')
    (home / "AGENTS.md").write_bytes(b"# Untrusted imported instructions\r\nDo not execute.\r\n")
    skills = home / "skills/example"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_bytes(b"# Skill\r\n")
    (skills / "run.sh").write_text("exit 99")
    memories = home / "memories"
    memories.mkdir()
    (memories / "intentional.md").write_bytes(b"# Intentional memory\r\nexact bytes\r\n")
    for name in ("auth.json", "credentials.md", "secret.db", "state.sqlite", "history.jsonl"):
        (memories / name).write_text("NEVER IMPORT")
    for name in ("sessions", "history", "logs", ".git"):
        (memories / name).mkdir()
        (memories / name / "private.md").write_text("NEVER IMPORT")
    return home, memories


def selection(sources):
    home, memories = sources
    return {"action": "preview_migration", "source_home": str(home), "memory_dirs": [str(memories)]}


def apply_request(preview):
    return {"action": "apply_migration", "preview": preview["preview"], "expected_revision": preview["revision"]}


def test_preview_and_apply_exact_bytes_idempotent_and_secret_exclusion(tmp_path, sources):
    home, memories = sources
    base = tmp_path / "memory"
    store = BootstrapStore(base)
    original = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    preview = store.execute(selection(sources))
    assert preview["can_apply"] and not base.exists()
    assert len(preview["preview"]["files"]) == 3
    result = store.execute(apply_request(preview))
    assert result["applied"] and len(result["created"]) == 3
    assert result["policy_promoted"] is False
    for item in preview["preview"]["files"]:
        assert (base / item["destination"]).read_bytes() == Path(item["source"]).read_bytes()
        assert item["destination"].startswith("shared/imports/codex/")
    assert not (base / "shared/policies").exists()
    assert all(p.read_bytes() == data for p, data in original.items())
    assert all(b"NEVER IMPORT" not in p.read_bytes() for p in base.rglob("*.md"))
    again = store.execute(apply_request(preview))
    assert again["created"] == [] and again["unchanged"] == 3


def test_config_selection_never_imports_config(tmp_path, sources):
    home, _ = sources
    result = BootstrapStore(tmp_path / "memory").execute({"action": "preview_migration", "source_config": str(home / "config.toml"), "memory_dirs": [], "import_id": "archive"})
    assert len(result["preview"]["files"]) == 2
    assert all("config.toml" not in item["source"] for item in result["preview"]["files"])


@pytest.mark.parametrize("change", ["edit", "add", "remove"])
def test_stale_source_preview_fails_before_creating_files(tmp_path, sources, change):
    _, memories = sources
    base = tmp_path / "memory"
    store = BootstrapStore(base)
    preview = store.execute(selection(sources))
    if change == "edit":
        (memories / "intentional.md").write_text("changed")
    elif change == "add":
        (memories / "added.md").write_text("new")
    else:
        (memories / "intentional.md").unlink()
    with pytest.raises(BootstrapError, match="Source content"):
        store.execute(apply_request(preview))
    assert not (base / "shared/imports").exists()


@pytest.mark.parametrize("conflict_timing", ["before", "after"])
def test_destination_conflicts_never_overwritten(tmp_path, sources, conflict_timing):
    base = tmp_path / "memory"
    target = base / "shared/imports/codex/instructions/AGENTS.md"
    store = BootstrapStore(base)
    if conflict_timing == "after":
        preview = store.execute(selection(sources))
    target.parent.mkdir(parents=True)
    target.write_bytes(b"Existing destination stays\r\n")
    if conflict_timing == "before":
        preview = store.execute(selection(sources))
        assert not preview["can_apply"]
    with pytest.raises(BootstrapError, match="conflict"):
        store.execute(apply_request(preview))
    assert target.read_bytes() == b"Existing destination stays\r\n"
    assert not (base / "shared/imports/codex/memory").exists()


@pytest.mark.parametrize("kind", ["symlink-file", "symlink-directory", "fifo", "hardlink"])
def test_refuse_unsafe_selected_source(tmp_path, sources, kind):
    _, memories = sources
    if kind == "symlink-file":
        (memories / "bad.md").symlink_to(memories / "intentional.md")
    elif kind == "symlink-directory":
        (memories / "alias").symlink_to(memories, target_is_directory=True)
    elif kind == "fifo":
        os.mkfifo(memories / "bad.md")
    else:
        os.link(memories / "intentional.md", memories / "bad.md")
    with pytest.raises(BootstrapError):
        BootstrapStore(tmp_path / "memory").execute(selection(sources))


def test_destination_symlink_and_tampered_preview(tmp_path, sources):
    base = tmp_path / "memory"
    store = BootstrapStore(base)
    preview = store.execute(selection(sources))
    preview["preview"]["files"][0]["destination"] = "../escape.md"
    with pytest.raises(BootstrapError, match="revision"):
        store.execute(apply_request(preview))
    (base / "shared").mkdir(parents=True, exist_ok=True)
    (base / "shared/imports").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(BootstrapError, match="Symlink"):
        store.execute(selection(sources))


def test_requires_intentional_sources_and_dedicated_memory(tmp_path, sources):
    home, _ = sources
    store = BootstrapStore(tmp_path / "memory")
    for payload in [
        {"source_home": str(home)},
        {"memory_dirs": []},
        {"source_home": str(home), "source_config": str(home / "config.toml"), "memory_dirs": []},
        {"source_home": str(home), "memory_dirs": [str(home)]},
        {"source_home": str(home), "memory_dirs": [str(home / "memories/sessions")]},
        {"source_home": str(home), "memory_dirs": [str(home / "memories/history")]},
        {"source_home": str(home), "memory_dirs": [str(home / "memories/logs")]},
        {"source_home": str(home), "memory_dirs": ["/"]},
        {"source_home": str(home), "memory_dirs": [str(tmp_path)]},
        {"source_home": str(home), "memory_dirs": [], "import_id": "../escape"},
    ]:
        with pytest.raises(ValueError):
            store.execute({"action": "preview_migration", **payload})


def test_stale_source_symlink_after_preview(tmp_path, sources):
    _, memories = sources
    store = BootstrapStore(tmp_path / "memory")
    preview = store.execute(selection(sources))
    original = memories / "intentional.md"
    original.unlink()
    original.symlink_to(tmp_path / "missing")
    with pytest.raises(BootstrapError):
        store.execute(apply_request(preview))
