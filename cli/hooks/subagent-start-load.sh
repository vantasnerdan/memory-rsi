#!/usr/bin/env bash
# ============================================================================
# subagent-start-load.sh - SubagentStart hook for agent-memory integration
#
# Called by Claude Code's SubagentStart hook when a subagent spawns via the
# Task tool. Queries agent-memory for entries relevant to the subagent's type
# and outputs structured context as JSON on stdout. The additionalContext
# string is injected into the subagent's context, giving it institutional
# memory instead of starting as a blank slate.
#
# Input (JSON on stdin):
#   { "session_id", "transcript_path", "cwd", "hook_event_name",
#     "agent_type", "agent_id" }
#
# Output (JSON on stdout):
#   { "hookSpecificOutput": {
#       "hookEventName": "SubagentStart",
#       "additionalContext": "..."
#   }}
#
# Environment variables:
#   AGENT_ID           - Agent identity for scoped queries (required)
#   AGENT_MEMORY_PATH  - Base path for memory entries (default: memory)
#   MEMORY_BASE        - Override for --base flag (default: AGENT_MEMORY_PATH)
#
# Exit: Always 0. Never break subagent spawning.
#
# Logging:
#   Structured JSON Lines to hooks.log (via common.sh logging functions).
#   Human-readable diagnostics to stderr.
#
# Performance target: <5 seconds total.
#
# Version: 2026.02
# ============================================================================

set -uo pipefail

# --- Constants ---
TIMEOUT_SECONDS=8
MAX_SEARCH_RESULTS=3
MAX_LS_ENTRIES=5

# --- Safe JSON output functions ---
# These ensure stdout always contains valid JSON, even on error paths.

output_empty() {
    echo '{"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":""}}'
}

output_context() {
    local ctx="$1"
    python3 -c '
import json, sys
ctx = sys.argv[1]
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SubagentStart",
        "additionalContext": ctx
    }
}))
' "$ctx"
}

# --- Temporary directory for Python scripts ---
TMPDIR_SCRIPTS=""
HAS_OUTPUT=false

cleanup_and_exit() {
    if [[ -n "$TMPDIR_SCRIPTS" ]] && [[ -d "$TMPDIR_SCRIPTS" ]]; then
        rm -rf "$TMPDIR_SCRIPTS"
    fi
    # If we haven't written JSON output yet, write empty
    if [[ "$HAS_OUTPUT" == false ]]; then
        output_empty
    fi
    exit 0
}

trap 'cleanup_and_exit' EXIT ERR

TMPDIR_SCRIPTS=$(mktemp -d 2>/dev/null) || TMPDIR_SCRIPTS="/tmp/subagent-start-$$"
mkdir -p "$TMPDIR_SCRIPTS" 2>/dev/null || true

# --- Logging (stderr only, stdout is JSON) ---
log_info()  { echo "[subagent-start-load] $*" >&2; }
log_warn()  { echo "[subagent-start-load] WARNING: $*" >&2; }
log_error() { echo "[subagent-start-load] ERROR: $*" >&2; }

# --- Source shared utilities (find_memory_cli, REPO_DIR, logging functions) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

# --- Initialize structured logging ---
hook_log_init "SubagentStart"

# --- Check environment variables ---
ENV_WARNINGS="$(hook_check_env)"

# --- Write embedded Python scripts to temp files ---

cat > "${TMPDIR_SCRIPTS}/parse_input.py" << 'PYEOF'
import json, sys

try:
    data = json.loads(sys.argv[1]) if sys.argv[1].strip() else {}
except (json.JSONDecodeError, IndexError):
    data = {}

print(data.get("agent_type", ""))
print(data.get("agent_id", ""))
print(data.get("session_id", "unknown"))
print(data.get("cwd", ""))
print(data.get("transcript_path", ""))
PYEOF

cat > "${TMPDIR_SCRIPTS}/format_knowledge.py" << 'PYEOF'
import json, sys

try:
    results = json.loads(sys.argv[1])
except (json.JSONDecodeError, IndexError):
    sys.exit(1)

if not isinstance(results, list) or not results:
    sys.exit(1)

for r in results:
    path = r.get("path", "")
    section = r.get("section", "")
    desc = r.get("section_description", "")
    snippet = r.get("snippet", "")
    line = f"- {path}"
    if section:
        line += f" > {section}"
    if desc:
        line += f" -- {desc}"
    print(line)
    # Include first few lines of snippet for key patterns
    if snippet:
        snippet_lines = snippet.strip().split("\n")
        for sl in snippet_lines[:3]:
            cleaned = sl.strip()
            if cleaned:
                print(f"  {cleaned}")
PYEOF

cat > "${TMPDIR_SCRIPTS}/format_efforts.py" << 'PYEOF'
import json, sys

try:
    entries = json.loads(sys.argv[1])
    limit = int(sys.argv[2])
except (json.JSONDecodeError, IndexError, ValueError):
    sys.exit(1)

if not isinstance(entries, list) or not entries:
    sys.exit(1)

for entry in entries[:limit]:
    name = entry.get("file", "")
    desc = entry.get("description", "")
    confidence = entry.get("confidence", "")
    status = entry.get("status", "")
    line = f"- {name}"
    if desc:
        line += f" -- {desc}"
    qualifiers = []
    if confidence:
        qualifiers.append(confidence)
    if status:
        qualifiers.append(status)
    if qualifiers:
        line += f" [{', '.join(qualifiers)}]"
    print(line)
PYEOF

cat > "${TMPDIR_SCRIPTS}/format_lessons.py" << 'PYEOF'
import json, sys

try:
    results = json.loads(sys.argv[1])
except (json.JSONDecodeError, IndexError):
    sys.exit(1)

if not isinstance(results, list) or not results:
    sys.exit(1)

for r in results:
    path = r.get("path", "")
    section = r.get("section", "")
    desc = r.get("section_description", "")
    snippet = r.get("snippet", "")
    line = f"- {path}"
    if section:
        line += f" > {section}"
    if desc:
        line += f" -- {desc}"
    print(line)
    # Include snippet extract for actionable lessons
    if snippet:
        snippet_lines = snippet.strip().split("\n")
        for sl in snippet_lines[:2]:
            cleaned = sl.strip()
            if cleaned:
                print(f"  {cleaned}")
PYEOF

# --- Read stdin JSON ---
INPUT_JSON=""
if ! INPUT_JSON=$(timeout 2 cat 2>/dev/null); then
    INPUT_JSON="{}"
fi

# --- Parse input ---
parse_result=$(python3 "${TMPDIR_SCRIPTS}/parse_input.py" "$INPUT_JSON" 2>/dev/null) || parse_result=""

AGENT_TYPE=$(echo "$parse_result" | sed -n '1p')
SUBAGENT_ID=$(echo "$parse_result" | sed -n '2p')
SESSION_ID=$(echo "$parse_result" | sed -n '3p')
CWD=$(echo "$parse_result" | sed -n '4p')
TRANSCRIPT_PATH=$(echo "$parse_result" | sed -n '5p')

# Defaults
SESSION_ID="${SESSION_ID:-unknown}"
CWD="${CWD:-$(pwd)}"
AGENT_ID="${AGENT_ID:-}"
AGENT_MEMORY_PATH="${AGENT_MEMORY_PATH:-}"
MEMORY_BASE="${MEMORY_BASE:-${AGENT_MEMORY_PATH:-${REPO_DIR}/memory}}"

log_info "SubagentStart: type=${AGENT_TYPE:-unset} id=${SUBAGENT_ID:-unset} session=${SESSION_ID}"
hook_log_start "session=${SESSION_ID}" "agent_type=${AGENT_TYPE}" "env_warnings=${ENV_WARNINGS}"

# --- Early exit checks ---

# No memory CLI -> empty context
if ! find_memory_cli; then
    log_warn "Memory CLI not available; outputting empty context"
    output_empty
    HAS_OUTPUT=true
    hook_log_skip "memory CLI not available" "session=${SESSION_ID}" "agent_type=${AGENT_TYPE}" "env_warnings=${ENV_WARNINGS}"
    exit 0
fi

# No AGENT_ID -> output a note so the subagent sees the misconfiguration
if [[ -z "$AGENT_ID" ]]; then
    log_warn "AGENT_ID not set; memory queries require agent identity"
    output_context "Note: AGENT_ID environment variable is not set. Set AGENT_ID for institutional memory loading."
    HAS_OUTPUT=true
    hook_log_skip "AGENT_ID not set" "session=${SESSION_ID}" "agent_type=${AGENT_TYPE}" "env_warnings=${ENV_WARNINGS}"
    exit 0
fi

# No agent_type from stdin -> empty context (we need it to query relevant entries)
if [[ -z "$AGENT_TYPE" ]]; then
    log_warn "No agent_type in stdin JSON; cannot scope memory queries"
    output_empty
    HAS_OUTPUT=true
    hook_log_skip "No agent_type in stdin" "session=${SESSION_ID}" "env_warnings=${ENV_WARNINGS}"
    exit 0
fi

# --- Query functions ---
# Each function writes JSON results to stdout and returns 0 on success.
# All diagnostics go to stderr.

query_agent_type_entries() {
    # Search for entries tagged with the agent type
    local result
    result=$(timeout "$TIMEOUT_SECONDS" \
        env AGENT_ID="$AGENT_ID" $MEMORY_CMD --json-output search \
            "$AGENT_TYPE" \
            --scope own \
            --tag "$AGENT_TYPE" \
            --limit "$MAX_SEARCH_RESULTS" \
            --base "$MEMORY_BASE" \
        2>/dev/null) || return 1

    if [[ -z "$result" ]] || [[ "$result" == "[]" ]]; then
        return 1
    fi

    echo "$result"
    return 0
}

query_agent_lessons() {
    # Search for lesson/knowledge entries related to the agent type
    local result
    result=$(timeout "$TIMEOUT_SECONDS" \
        env AGENT_ID="$AGENT_ID" $MEMORY_CMD --json-output search \
            "$AGENT_TYPE lessons" \
            --scope own \
            --limit "$MAX_SEARCH_RESULTS" \
            --base "$MEMORY_BASE" \
        2>/dev/null) || return 1

    if [[ -z "$result" ]] || [[ "$result" == "[]" ]]; then
        return 1
    fi

    echo "$result"
    return 0
}

query_active_efforts() {
    # List effort entries for the agent (keyed by AGENT_ID, not AGENT_TYPE)
    local result=""
    local paths_to_try=(
        "${MEMORY_BASE}/${AGENT_ID}/effort"
        "${MEMORY_BASE}/${AGENT_ID}/efforts"
    )

    for path in "${paths_to_try[@]}"; do
        result=$(timeout "$TIMEOUT_SECONDS" \
            $MEMORY_CMD --json-output ls "$path" \
            2>/dev/null) || continue

        if [[ -n "$result" ]] && [[ "$result" != "[]" ]]; then
            echo "$result"
            return 0
        fi
    done

    return 1
}

# --- Format functions (call temp Python scripts) ---

format_knowledge_entries() {
    local json_data="$1"
    python3 "${TMPDIR_SCRIPTS}/format_knowledge.py" "$json_data"
}

format_effort_entries() {
    local json_data="$1"
    python3 "${TMPDIR_SCRIPTS}/format_efforts.py" "$json_data" "$MAX_LS_ENTRIES"
}

format_lesson_entries() {
    local json_data="$1"
    python3 "${TMPDIR_SCRIPTS}/format_lessons.py" "$json_data"
}

# --- Main context assembly ---
main() {
    local context_parts=()

    # Header
    context_parts+=("=== Agent Memory Context (loaded by SubagentStart hook) ===")
    context_parts+=("")
    context_parts+=("## Agent Type: ${AGENT_TYPE}")
    context_parts+=("")

    local has_content=false

    # Query 1: Agent-type specific entries (most targeted)
    local type_json=""
    if type_json=$(query_agent_type_entries); then
        local formatted
        if formatted=$(format_knowledge_entries "$type_json"); then
            context_parts+=("## Relevant Knowledge")
            context_parts+=("$formatted")
            context_parts+=("")
            has_content=true
            log_info "Found agent-type entries for '${AGENT_TYPE}'"
        fi
    fi

    # Query 2: Agent lessons/knowledge
    local lessons_json=""
    if lessons_json=$(query_agent_lessons); then
        local formatted
        if formatted=$(format_lesson_entries "$lessons_json"); then
            context_parts+=("## Key Patterns")
            context_parts+=("$formatted")
            context_parts+=("")
            has_content=true
            log_info "Found lesson entries for '${AGENT_TYPE}'"
        fi
    fi

    # Query 3: Active efforts
    local effort_json=""
    if effort_json=$(query_active_efforts); then
        local formatted
        if formatted=$(format_effort_entries "$effort_json"); then
            context_parts+=("## Active Efforts")
            context_parts+=("$formatted")
            context_parts+=("")
            has_content=true
            log_info "Found effort entries"
        fi
    fi

    # --- Context Assembler (Haiku-powered, optional) ---
    # Uses PARENT transcript (TRANSCRIPT_PATH) to find the last Agent tool call prompt.
    # The subagent's own transcript doesn't exist yet at SubagentStart time.
    if [[ -n "$TRANSCRIPT_PATH" ]] && [[ -f "$TRANSCRIPT_PATH" ]]; then
        local ASSEMBLER_JSON=""
        ASSEMBLER_JSON=$(timeout 50 python3 "${SCRIPT_DIR}/context_assembler.py" \
            --transcript-path "$TRANSCRIPT_PATH" \
            --agent-type "$AGENT_TYPE" \
            --agent-id "$SUBAGENT_ID" \
            --cwd "$CWD" \
            2>/dev/null) || ASSEMBLER_JSON=""

        if [[ -n "$ASSEMBLER_JSON" ]]; then
            local ASSEMBLER_CONTEXT=""
            ASSEMBLER_CONTEXT=$(echo "$ASSEMBLER_JSON" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    ctx = d.get("context", "")
    if ctx:
        print(ctx)
except Exception:
    pass
' 2>/dev/null) || ASSEMBLER_CONTEXT=""

            if [[ -n "$ASSEMBLER_CONTEXT" ]]; then
                context_parts+=("$ASSEMBLER_CONTEXT")
                has_content=true
                log_info "Context Assembler added $(echo "$ASSEMBLER_CONTEXT" | wc -c) bytes"
            fi
        fi
    else
        log_info "No parent transcript available; skipping Context Assembler"
    fi

    # If no content found from any query, output empty
    if [[ "$has_content" == false ]]; then
        log_info "No relevant memory entries found for agent_type '${AGENT_TYPE}'"
        output_empty
        HAS_OUTPUT=true
        hook_log_success "session=${SESSION_ID}" "agent_type=${AGENT_TYPE}" "context_bytes=0" "env_warnings=${ENV_WARNINGS}"
        return
    fi

    context_parts+=("=== End Agent Memory Context ===")

    # Join all parts with newlines
    local full_context=""
    for part in "${context_parts[@]}"; do
        if [[ -z "$full_context" ]]; then
            full_context="$part"
        else
            full_context="${full_context}
${part}"
        fi
    done

    # Output valid JSON with the assembled context
    output_context "$full_context"
    HAS_OUTPUT=true
    hook_log_success "session=${SESSION_ID}" "agent_type=${AGENT_TYPE}" "context_bytes=${#full_context}" "env_warnings=${ENV_WARNINGS}"
}

# --- Entry point ---
# Route all diagnostic output to stderr
exec 3>&2

main 2>&3

exit 0
