---
description: Check the agent-memory CLI, the memory base it resolves and the plugin's opt-in hooks
allowed-tools: Bash(memory:*), Bash(command -v memory)
---

Report the memory-rsi setup for this machine. Read only, unless the user confirms the install in step 4.

1. Run `memory --version` and `command -v memory`. Report the version and the binary path.
2. Run `memory config show`. Report `base_path`, `agent_id` and the `(source: ...)` printed beside each.
3. Warn plainly when the base is a fallback rather than a choice: source `cwd auto-detect` or `default`. Recommend pinning it with `memory config set base_path <absolute path>` or the `AGENT_MEMORY_PATH` environment variable.
4. If `memory` is missing or older than the plugin's version (the `version` in `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`), offer this command and run it only after the user says yes:

   ```bash
   pipx install --force "${CLAUDE_PLUGIN_ROOT}/cli"
   ```

5. Explain the hooks: all are off by default. They are switched on per hook in `/plugin` (memory-rsi, Configure options): `hook_restore`, `hook_precompact`, `hook_subagent_load`, `hook_subagent_save`, `hook_policy`. Each needs `memory_base` (or `MEMORY_BASE` / `AGENT_MEMORY_PATH`) to name an existing directory; `agent_id` sets `AGENT_ID` for them. Quote the option descriptions so the user sees what each hook writes or injects.
6. Show the permission rule that lets the commands run the CLI without a prompt, for `.claude/settings.json` or `~/.claude/settings.json`:

   ```json
   { "permissions": { "allow": ["Bash(memory:*)"] } }
   ```

End with one line: ready, or the single next action the user should take.
