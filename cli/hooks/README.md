# Claude Code Hooks for agent-memory

## Overview

These hooks integrate agent-memory with Claude Code's lifecycle events to provide persistent memory across two critical boundaries:

1. **Compaction** -- When context is compressed (~95% capacity or `/compact`), detailed context is lost. The compaction hooks save rich context before and restore key context after.
2. **Subagent spawning** -- When subagents are spawned via the Task tool, they start as blank slates. The subagent hooks load relevant institutional memory at spawn and save learnings back when the subagent finishes.

Together, these four hooks form a complete memory lifecycle:

| Hook | Script | Fires When | Direction |
|------|--------|------------|-----------|
| PreCompact | `pre-compact-save.sh` | Before compaction begins | Save to memory |
| SessionStart (compact) | `post-compact-restore.sh` | After compaction completes | Load from memory |
| SubagentStart | `subagent-start-load.sh` | When a subagent spawns | Load from memory |
| SubagentStop | `subagent-stop-save.sh` | When a subagent finishes | Save to memory |

Shared utilities live in `common.sh`, which is sourced by all hook scripts that need memory CLI discovery.

## Architecture

### PreCompact: `pre-compact-save.sh`

Fires before compaction. Receives JSON on stdin with session metadata:

```json
{
  "session_id": "...",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/working/directory",
  "hook_event_name": "PreCompact",
  "trigger": "auto|manual",
  "custom_instructions": "..."
}
```

Processing steps:

1. Parses stdin JSON to extract `session_id`, `transcript_path`, `trigger`, and `cwd`
2. Validates the transcript file exists
3. Tails the last 200 lines of the transcript JSONL file
4. Finds the most recent compaction boundary marker (`compact_boundary`) and only processes entries after it (avoids re-saving already-saved context)
5. Extracts structured data via embedded Python:
   - **User requests** -- last 10 unique user messages (truncated to 300 chars each)
   - **Key decisions** -- assistant messages containing decision language ("decided", "approach", "root cause", "the fix", etc.), last 5
   - **Files modified** -- file paths from Edit, Write, and NotebookEdit tool calls
   - **Errors encountered** -- tool result errors, last 5
   - **Commands run** -- Bash commands with descriptions, last 10
   - **Session metadata** -- session ID, trigger type, working directory, line count
6. Writes a structured memory entry via `memory new` with:
   - Name: `compaction-{session_id_first_12_chars}-{unix_timestamp}`
   - Category: `effort`
   - Confidence: `working`
   - Tags: `compaction, automated, session-context`
   - Flag: `--no-git` (avoids git operations during hook)
7. All output goes to stderr (PreCompact stdout is not injected anywhere)
8. Always exits 0 -- never breaks compaction, even on error

### SessionStart (compact): `post-compact-restore.sh`

Fires after compaction completes, when the SessionStart hook source is `compact`. Receives JSON on stdin with:

```json
{
  "session_id": "...",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/working/directory",
  "hook_event_name": "SessionStart",
  "source": "compact",
  "model": "..."
}
```

Processing steps:

1. Parses stdin JSON for session_id, cwd, transcript_path, source, and model
2. Locates the `memory` CLI (checks PATH, common install locations, and project virtualenvs)
3. Queries agent-memory in priority order:
   - **Compaction entries** -- `memory search "session compaction" --scope own --tag compaction --limit 3` (most specific)
   - **Effort entries** -- `memory ls` on the agent's effort/efforts directory (active work items, limit 5)
   - **General search** -- `memory search "session" --scope own --limit 3` (fallback, only if nothing found above)
4. Extracts modified files from the last 500 lines of the transcript JSONL (independent of memory queries)
5. Extracts a current task hint from the top-ranked compaction entry
6. Assembles structured output to stdout (target: <2K tokens):
   - Active session block (working directory, session ID, model, agent)
   - Recent memory entries with descriptions
   - Key decisions from most recent compaction snapshot
   - Context tags
   - Active efforts
   - Files modified
   - Current task hint
7. All diagnostics go to stderr; only the summary goes to stdout
8. Stdout is injected directly into Claude's post-compaction context
9. Always exits 0 -- never breaks session start
10. Cleans up temporary Python script files on exit

### SubagentStart: `subagent-start-load.sh`

Fires when a Claude Code subagent is spawned via the Task tool, before it starts working. Receives JSON on stdin with:

```json
{
  "session_id": "...",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/working/directory",
  "hook_event_name": "SubagentStart",
  "agent_type": "data-ingestion",
  "agent_id": "agent-abc123"
}
```

Processing steps:

1. Parses stdin JSON for agent_type, agent_id, session_id, and cwd
2. Locates the `memory` CLI (same search pattern as restore hook)
3. Queries agent-memory in priority order:
   - **Agent-type entries** -- `memory search "$AGENT_TYPE" --tag "$AGENT_TYPE" --limit 3` (most targeted)
   - **Agent lessons** -- `memory search "$AGENT_TYPE lessons" --limit 3` (broader knowledge)
   - **Active efforts** -- `memory ls` on the agent's effort directory (current work context)
4. Formats results into a lean markdown summary
5. Outputs valid JSON to stdout with `additionalContext` field
6. The `additionalContext` string is injected directly into the subagent's context
7. All diagnostics go to stderr; stdout is always valid JSON
8. Always exits 0 -- never breaks subagent spawning
9. Graceful degradation -- if no memory found, outputs empty `additionalContext`

### SubagentStop: `subagent-stop-save.sh`

Fires when a Claude Code subagent completes its work. Receives JSON on stdin with:

```json
{
  "session_id": "...",
  "transcript_path": "/path/to/subagent/transcript.jsonl",
  "cwd": "/working/directory",
  "hook_event_name": "SubagentStop",
  "agent_type": "data-ingestion",
  "agent_id": "agent-abc123"
}
```

Processing steps:

1. Parses stdin JSON for agent_type, agent_id, session_id, cwd, and transcript_path
2. Validates transcript exists and has >=10 lines (skips trivial subagents)
3. Tails last 200 lines of the subagent's transcript JSONL
4. Extracts structured data via embedded Python (single-pass):
   - **Task** -- first substantial user message (what the subagent was asked to do)
   - **Key decisions** -- assistant text with decision language (same signals as compaction hook)
   - **Files modified** -- from Edit/Write/NotebookEdit tool calls
   - **Errors** -- tool_result blocks with is_error=true
   - **Result** -- last substantial assistant text (conclusion/report, truncated to 500 chars)
5. Writes memory entry via `memory new` with:
   - Name: `subagent-{agent_type}-{timestamp}-{pid}`
   - Category: `effort`
   - Tags: `subagent,{agent_type},automated`
   - Flag: `--no-git`
6. Refreshes SQLite cache (`memory cache build`, best-effort)
7. All output goes to stderr (SubagentStop does not inject context)
8. Always exits 0 -- never breaks subagent completion

## Prerequisites

1. **agent-memory CLI** installed and accessible. Install with:
   ```bash
   pip install -e /path/to/agent-memory
   ```
   The restore hook searches multiple locations: PATH, `~/.local/bin/memory`, `/usr/local/bin/memory`, and project virtualenvs (`agent-memory/.venv/bin/memory`, `agent-memory/venv/bin/memory`).

2. **`AGENT_ID` environment variable** set to identify the agent. Used for memory authorship and scoped queries. Required by both hooks — the save hook fails loudly if unset, and the restore hook skips memory queries.

3. **Python 3** available in PATH (used for JSON parsing and transcript extraction).

4. **Hook scripts must be executable**:
   ```bash
   chmod +x /path/to/agent-memory/hooks/*.sh
   ```

5. **`AGENT_MEMORY_PATH` environment variable** (optional). Sets the base directory for memory entries. Defaults to the `memory/` directory inside the agent-memory repository.

## Installation: Settings Configuration

Add the following to your `.claude/settings.json` (or `~/.claude/settings.json` for global configuration). Replace `/path/to/agent-memory` with the absolute path to your agent-memory installation:

```json
{
  "hooks": {
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/agent-memory/hooks/pre-compact-save.sh",
            "timeout": 30000
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/agent-memory/hooks/post-compact-restore.sh",
            "timeout": 15000
          }
        ]
      }
    ],
    "SubagentStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/agent-memory/hooks/subagent-start-load.sh",
            "timeout": 15000
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/agent-memory/hooks/subagent-stop-save.sh",
            "timeout": 15000
          }
        ]
      }
    ]
  }
}
```

**Important notes:**
- Paths must be absolute. Relative paths will not resolve correctly.
- The PreCompact timeout is 30 seconds. The save hook targets completion in under 15 seconds but has headroom for slow I/O.
- The SessionStart matcher is `"compact"` -- this ensures the restore hook only fires after compaction, not on normal session starts.
- The restore hook uses an internal timeout of 8 seconds per memory query to stay well within the 15-second hook timeout.
- The SubagentStart matcher `""` matches all agent types. Use a specific name (e.g., `"data-ingestion"`) to target specific agents.
- The SubagentStart hook outputs JSON to stdout -- the `additionalContext` field is injected into the subagent's context.
- The SubagentStop hook does NOT output to stdout -- it only writes to agent-memory and logs to stderr.
- If your settings.json already has a `hooks` key, merge the entries into the existing structure.

## Installation: CLAUDE.md Section

Add this section to your project's `CLAUDE.md` (or the agent's instruction file) so Claude Code knows how to cooperate with the hooks:

```markdown
# Compact Instructions

When compacting, always preserve:
- Key decisions and their rationale
- File paths modified and current state
- Test results and verification status
- Error patterns discovered and resolutions
- Active task context and next steps

The PreCompact hook saves detailed context to agent-memory automatically.
After compaction, restored context appears at the top of your fresh context window.
Use `memory search` or `memory section` to retrieve deeper historical context.
```

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AGENT_ID` | Yes | (none — required) | Agent identity for memory authorship and scoped queries |
| `AGENT_MEMORY_PATH` | No | Repo's `memory/` directory | Base directory for memory entries |
| `MEMORY_BASE` | No | Value of `AGENT_MEMORY_PATH` | Override for the `--base` flag passed to memory CLI |

The save hook requires `AGENT_ID` and exits with an error if unset. The restore hook leaves it empty if unset, which disables memory queries (only session metadata is restored).

`MEMORY_BASE` takes precedence over `AGENT_MEMORY_PATH` when both are set. If neither is set, all hooks resolve to the `memory/` directory relative to the agent-memory repository root (determined from the script location).

## How It Works (Technical)

The full data flow during a compaction event:

```
1. Context window fills to ~95%
   OR user runs /compact
       |
       v
2. Claude Code fires PreCompact hook
       |
       v
3. pre-compact-save.sh receives session JSON on stdin
   - Parses session_id, transcript_path, trigger, cwd
       |
       v
4. Script tails last 200 lines of transcript JSONL
   - Finds last compact_boundary marker
   - Only processes entries after that boundary
       |
       v
5. Embedded Python extracts structured data:
   - User requests (last 10 unique, 300 char limit)
   - Key decisions (last 5, detected by signal words)
   - Files modified (from Edit/Write/NotebookEdit tool calls)
   - Errors (last 5, from tool_result is_error blocks)
   - Commands (last 10, from Bash tool calls)
       |
       v
6. Writes to agent-memory:
   memory new "compaction-{session12}-{timestamp}" \
     -d "Pre-compaction context snapshot" \
     -c effort --confidence working --status active \
     -t "compaction,automated,session-context" \
     -b "$BODY" --no-git --base "$MEMORY_BASE"
       |
       v
7. Compaction proceeds -- context is compressed
       |
       v
8. Claude Code fires SessionStart hook (source=compact)
       |
       v
9. post-compact-restore.sh runs
   - Locates memory CLI (PATH, common locations, virtualenvs)
   - Queries for compaction entries (search --tag compaction)
   - Queries for effort entries (ls on effort directory)
   - Falls back to broad search if nothing found
   - Extracts modified files from transcript (last 500 lines)
   - Extracts current task hint from top compaction entry
       |
       v
10. Outputs lean structured summary to stdout (<2K tokens):
    - Active session (cwd, session ID, model, agent)
    - Recent memory entries
    - Key decisions
    - Context tags
    - Active efforts
    - Files modified
    - Current task hint
       |
       v
11. Stdout is injected into Claude's post-compaction context
       |
       v
12. Agent continues with key context preserved
```

### Subagent Lifecycle

```
1. Orchestrator spawns subagent via Task tool
       |
       v
2. Claude Code fires SubagentStart hook
       |
       v
3. subagent-start-load.sh receives agent JSON on stdin
   - Parses agent_type, agent_id, session_id
       |
       v
4. Queries agent-memory:
   - Agent-type tagged entries (most specific)
   - Agent lessons (broader knowledge)
   - Active efforts (current work)
       |
       v
5. Outputs JSON with additionalContext to stdout
       |
       v
6. additionalContext injected into subagent's context
       |
       v
7. Subagent works with institutional memory loaded
       |
       v
8. Subagent completes → Claude Code fires SubagentStop hook
       |
       v
9. subagent-stop-save.sh receives agent JSON on stdin
   - Parses transcript_path (subagent's own transcript)
       |
       v
10. Tails last 200 lines of subagent transcript
    - Extracts task, decisions, files, errors, result
       |
       v
11. Writes to agent-memory:
    memory new "subagent-{agent_type}-{timestamp}-{pid}" \
      -t "subagent,{agent_type},automated" \
      --no-git --base "$MEMORY_BASE"
       |
       v
12. Learnings persisted for future subagent spawns
```

## Troubleshooting

**Hooks do not fire:**
- Verify the paths in `settings.json` are absolute and correct
- Verify the scripts are executable (`chmod +x`)
- Check that the hook event names are exactly `PreCompact` and `SessionStart`
- For the restore hook, confirm the matcher is `"compact"` (not `"Compact"` or empty)

**Memory write fails (save hook):**
- Check that `AGENT_ID` is set in the environment
- Check that the `memory` CLI is in PATH (`which memory`)
- Try running `memory new test-entry -d "test" --base /path/to/memory` manually to verify the CLI works
- Check stderr output: the save hook logs to stderr with `[pre-compact-save]` prefix

**Restore output is empty after compaction:**
- Verify the agent has previous compaction entries: `memory search "compaction" --scope own --tag compaction --base /path/to/memory`
- Check that `AGENT_ID` is set -- without it, the restore hook skips memory queries entirely
- Verify `MEMORY_BASE` or `AGENT_MEMORY_PATH` points to the correct memory directory

**Restore hook is slow:**
- Each memory query has an 8-second internal timeout
- The overall hook timeout is 15 seconds
- If memory queries are consistently slow, check disk I/O and memory index health

**Subagent hooks don't fire:**
- Verify `SubagentStart` and `SubagentStop` are the exact event names in settings.json
- Verify the scripts are executable
- The SubagentStart hook must output valid JSON to stdout -- check with a manual test (below)

**Subagent start hook returns empty context:**
- Check that `AGENT_ID` is set
- Check that there are memory entries tagged with the agent_type
- Run `memory search "agent-type-name" --tag "agent-type-name" --base /path/to/memory` to verify entries exist

**Subagent stop hook doesn't save:**
- Check that `AGENT_ID` is set (required, fails loudly if missing)
- Check that the subagent transcript has >=10 lines (trivial transcripts are skipped)
- Verify the transcript_path in the hook input points to the subagent's transcript file

**General debugging:**
- All errors go to stderr; hooks always exit 0 (they never break Claude Code)
- The compaction save hook logs with `[pre-compact-save]` prefix
- The compaction restore hook sends diagnostics to file descriptor 3 (stderr)
- The subagent start hook logs with `[subagent-start-load]` prefix
- The subagent stop hook logs with `[subagent-stop-save]` prefix
- To test the save hook: `echo '{"session_id":"test","transcript_path":"/path/to/transcript.jsonl","trigger":"manual","cwd":"/tmp"}' | AGENT_ID=test ./pre-compact-save.sh`
- To test the restore hook: `echo '{"session_id":"test","source":"compact","cwd":"/tmp"}' | AGENT_ID=test ./post-compact-restore.sh`
- To test the subagent start hook: `echo '{"agent_type":"test","agent_id":"test-123","session_id":"abc","cwd":"/tmp"}' | AGENT_ID=test ./subagent-start-load.sh`
- To test the subagent stop hook: `echo '{"agent_type":"test","agent_id":"test-123","session_id":"abc","cwd":"/tmp","transcript_path":"/dev/null"}' | AGENT_ID=test ./subagent-stop-save.sh`
