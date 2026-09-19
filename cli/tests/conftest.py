"""Keep CLI tests independent of the operator's real memory, config, and logs."""
import pytest

from agent_memory import config, config_cli, logging as memory_logging


@pytest.fixture(autouse=True)
def isolated_memory_environment(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for key in ("AGENT_ID", "AGENT_MEMORY_PATH", "AGENT_MEMORY_CACHE_PATH",
                "AGENT_MEMORY_REPO", "AGENT_MEMORY_DEFAULT_BRANCH"):
        monkeypatch.delenv(key, raising=False)
    directory = home / ".config/agent-memory"
    for module in (config, config_cli):
        monkeypatch.setattr(module, "CONFIG_DIR", directory)
        monkeypatch.setattr(module, "CONFIG_FILE", directory / "config.yaml")
    monkeypatch.setattr(memory_logging, "DEFAULT_LOG_DIR", home / ".agent-memory")
    monkeypatch.setenv("AGENT_MEMORY_LOG_PATH", str(home / ".agent-memory"))
