"""Allow `python -m agent_memory` as an alternative to the `memory` console script.

The DeepSeek Harness plugin (this repository's root package) prefers the
installed `memory` entry point but falls back to `python -m agent_memory`,
which requires this shim.
"""

from agent_memory.cli import cli

if __name__ == "__main__":
    cli(prog_name="memory")
