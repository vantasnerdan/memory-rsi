#!/usr/bin/env bash
# ============================================================================
# memory-md-guard.sh — PreToolUse hook for MEMORY.md reads and writes
#
# Fires before Read, Write, or Edit of any file. Checks if the target
# file is MEMORY.md. If so:
#
#   - On READ:  Injects additionalContext reminding to search deep memory
#   - On WRITE/EDIT: Offers the long-term store for reusable knowledge
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
    output_context "[memory-md-guard] MEMORY.md is a short index; when helpful, search the larger memory store with memory search \"<topic>\" --base ${MEMORY_BASE} and read relevant sections."
    hook_log_success

elif [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" ]]; then
    # This is a reminder, not a prerequisite to the requested write.
    output_write_context "[memory-md-guard] Keep MEMORY.md concise. For reusable engineering knowledge, the memory CLI can store details and leave a short pointer here; preserve the user's requested edits and avoid filing routine acknowledgements as lessons."
    hook_log_success
fi

exit 0
