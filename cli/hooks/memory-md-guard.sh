#!/usr/bin/env bash
# ============================================================================
# memory-md-guard.sh — PreToolUse hook for MEMORY.md reads and writes
#
# Fires before Read, Write, or Edit of any file. Checks if the target
# file is MEMORY.md. If so:
#
#   - On READ:  Injects additionalContext reminding to search deep memory
#   - On WRITE/EDIT: Injects additionalContext reminding to file to long-term
#                    storage first, then update MEMORY.md with pointers only
#
# Hook config (settings.json):
#   "PreToolUse": [{
#     "matcher": "Read|Write|Edit",
#     "hooks": [{
#       "type": "command",
#       "command": ". $HOME/.bashrc && /path/to/hooks/memory-md-guard.sh",
#       "timeout": 5000
#     }]
#   }]
#
# Stdin: JSON with tool_input.file_path (Read/Write/Edit all provide this)
# Stdout: JSON with hookSpecificOutput (additionalContext for all cases)
# Exit 0 always (never break the tool call chain)
#
# Version: 2026.02.2
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

# Initialize logging
hook_log_init "memory-md-guard"
hook_log_start

# Portable base path — uses AGENT_MEMORY_PATH if set, else 'memory' (relative)
MEMORY_BASE="${AGENT_MEMORY_PATH:-memory}"

# Read stdin (tool input JSON)
INPUT="$(cat)"

# --- Output helpers (JSON, same pattern as subagent-start-load.sh) ---

# Inject context into the conversation (Read guidance)
output_context() {
    local ctx="$1"
    python3 -c '
import json, sys
ctx = sys.argv[1]
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": ctx
    }
}))
' "$ctx"
}

# Inject write guidance into the conversation (Write/Edit reminder)
output_write_context() {
    local ctx="$1"
    python3 -c '
import json, sys
ctx = sys.argv[1]
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": ctx
    }
}))
' "$ctx"
}

# --- Parse input ---

PARSED=$(python3 -c '
import json, sys
try:
    data = json.loads(sys.argv[1])
except (json.JSONDecodeError, IndexError):
    data = {}
fp = ""
tn = ""
ti = data.get("tool_input", {})
if isinstance(ti, dict):
    fp = ti.get("file_path", "")
tn = data.get("tool_name", "")
print(f"{tn}\t{fp}")
' "$INPUT" 2>/dev/null) || true

TOOL_NAME="${PARSED%%	*}"
FILE_PATH="${PARSED#*	}"

# Early exit if no file_path or not MEMORY.md (end-anchored match)
if [[ -z "$FILE_PATH" ]] || [[ "$FILE_PATH" != */MEMORY.md ]]; then
    hook_log_skip "not MEMORY.md"
    exit 0
fi

if [[ "$TOOL_NAME" == "Read" ]]; then
    # Inject context reminder — allow the read but add guidance
    output_context "[memory-md-guard] STEP 0: Load skill shared_agent-memory-usage for full CLI syntax and patterns. THEN: MEMORY.md is a hot cache index (200-line limit), not the full memory store. Search deep memory: memory search \"<topic>\" --base ${MEMORY_BASE}. Drill in: memory toc <file> then memory section <file> \"<heading>\". MEMORY.md snippets point to full entries — use the CLI for complete context."
    hook_log_success

elif [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" ]]; then
    # Inject reminder — allow the write but guide the agent to file first
    output_write_context "[memory-md-guard] STEP 0: Load skill shared_agent-memory-usage for full CLI syntax and patterns. THEN: Before writing to MEMORY.md, file new content to long-term storage first: memory new \"<name>\" -d \"<desc>\" -c atlas --confidence working -b \"<body>\" --base ${MEMORY_BASE}, or memory update \"<path>\" -b \"<body>\" --base ${MEMORY_BASE}. THEN update MEMORY.md with only a brief pointer/snippet referencing the filed entry. MEMORY.md is a 200-line hot cache index — keep entries concise. If you have already filed via CLI, proceed with adding a pointer here."
    hook_log_success
fi

exit 0
