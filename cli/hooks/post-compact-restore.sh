#!/usr/bin/env bash
#
# post-compact-restore.sh - Restore agent context after Claude Code context compaction
#
# Called by Claude Code's SessionStart hook AFTER context compaction completes.
# Queries agent-memory for recent session context and outputs a lean summary
# to stdout. Anything written to stdout is injected directly into Claude's
# fresh post-compaction context.
#
# Input: JSON on stdin with session_id, transcript_path, cwd, hook_event_name,
#        source, and model fields.
#
# Output: Structured context summary on stdout (target: <2K tokens).
#         All errors and diagnostics go to stderr only.
#
# Environment variables:
#   AGENT_ID           - Agent identity (required for scoped queries)
#   AGENT_MEMORY_PATH  - Base path for memory entries (default: memory)
#   MEMORY_BASE        - Override for --base flag (default: AGENT_MEMORY_PATH)
#
# Exit: Always 0. Never break session start.
#
# Logging:
#   Structured JSON Lines to hooks.log (via common.sh logging functions).
#   Human-readable diagnostics to stderr.
#
# Version: 2026.02

set -uo pipefail

# --- Constants ---
TIMEOUT_SECONDS=8
MAX_SEARCH_RESULTS=3
MAX_LS_ENTRIES=5

# --- Source shared utilities (find_memory_cli, REPO_DIR, logging functions) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

# --- Initialize structured logging ---
hook_log_init "SessionStart"

# --- Check environment variables ---
ENV_WARNINGS="$(hook_check_env)"

# --- Helper: directory for temporary Python scripts ---
TMPDIR_SCRIPTS=""
cleanup_tmpdir() {
    if [[ -n "$TMPDIR_SCRIPTS" ]] && [[ -d "$TMPDIR_SCRIPTS" ]]; then
        rm -rf "$TMPDIR_SCRIPTS"
    fi
}
trap 'cleanup_tmpdir; exit 0' EXIT ERR

TMPDIR_SCRIPTS=$(mktemp -d 2>/dev/null) || TMPDIR_SCRIPTS="/tmp/post-compact-$$"
mkdir -p "$TMPDIR_SCRIPTS" 2>/dev/null || true

# --- Write embedded Python scripts to temp files ---
# This avoids heredoc-in-command-substitution warnings.

cat > "${TMPDIR_SCRIPTS}/parse_input.py" << 'PYEOF'
import json, sys

try:
    data = json.loads(sys.argv[1]) if sys.argv[1].strip() else {}
except (json.JSONDecodeError, IndexError):
    data = {}

print(data.get("session_id", "unknown"))
print(data.get("cwd", ""))
print(data.get("transcript_path", ""))
print(data.get("source", ""))
print(data.get("model", ""))
PYEOF

cat > "${TMPDIR_SCRIPTS}/format_compaction.py" << 'PYEOF'
import json, sys

try:
    results = json.loads(sys.argv[1])
except (json.JSONDecodeError, IndexError):
    sys.exit(1)

if not isinstance(results, list) or not results:
    sys.exit(1)

print("## Recent Memory Entries")
for r in results:
    path = r.get("path", "")
    section = r.get("section", "")
    desc = r.get("section_description", "")
    display = f"- {path}"
    if section:
        display += f" > {section}"
    if desc:
        display += f" -- {desc}"
    print(display)

# Extract details from the highest-ranked (most recent/relevant) entry
top = results[0]
snippet = top.get("snippet", "")
fm = top.get("file_frontmatter", {})

# Key Decisions - extract from snippet if present
if snippet:
    print()
    print("## Key Decisions This Session")
    # Truncate snippet to keep output lean
    lines = snippet.strip().split("\n")
    for line in lines[:5]:
        cleaned = line.strip()
        if cleaned:
            if not cleaned.startswith("-"):
                cleaned = "- " + cleaned
            print(cleaned)

# Tags as context clues
tags = fm.get("tags", [])
if tags:
    print()
    print("## Context Tags")
    print(f"- {', '.join(tags)}")
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

print("## Active Efforts")
for entry in entries[:limit]:
    name = entry.get("file", "")
    desc = entry.get("description", "")
    confidence = entry.get("confidence", "")
    line = f"- {name}"
    if desc:
        line += f" -- {desc}"
    if confidence:
        line += f" [{confidence}]"
    print(line)
PYEOF

cat > "${TMPDIR_SCRIPTS}/format_search.py" << 'PYEOF'
import json, sys

try:
    results = json.loads(sys.argv[1])
except (json.JSONDecodeError, IndexError):
    sys.exit(1)

if not isinstance(results, list) or not results:
    sys.exit(1)

print("## Recent Memory (general)")
for r in results:
    path = r.get("path", "")
    section = r.get("section", "")
    desc = r.get("section_description", "")
    line = f"- {path}"
    if section:
        line += f" > {section}"
    if desc:
        line += f" -- {desc}"
    print(line)
PYEOF

cat > "${TMPDIR_SCRIPTS}/extract_files.py" << 'PYEOF'
import json, sys

try:
    path = sys.argv[1]
    seen = set()
    files = []

    with open(path, "r") as f:
        # Read last portion of file for recent activity
        lines = f.readlines()
        # Take last 500 lines max for performance
        for line in lines[-500:]:
            try:
                entry = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            # Look for tool calls that modify files
            content = entry.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        inp = block.get("input", {})
                        if isinstance(inp, dict):
                            fp = inp.get("file_path", "")
                            if fp and fp not in seen:
                                seen.add(fp)
                                files.append(fp)

    # Print last 10 unique files
    for f in files[-10:]:
        print(f)

except Exception:
    pass
PYEOF

cat > "${TMPDIR_SCRIPTS}/extract_task.py" << 'PYEOF'
import json, sys

try:
    results = json.loads(sys.argv[1])
    if results:
        top = results[0]
        snippet = top.get("snippet", "")
        section = top.get("section", "")
        # Use section title as a task hint if it looks like one
        if section and "compaction" not in section.lower():
            print("## Current Task")
            print(f"- {section}")
        elif snippet:
            # Try first meaningful line of snippet
            for line in snippet.strip().split("\n"):
                line = line.strip()
                if line and len(line) > 10:
                    print("## Current Task")
                    print(f"- {line[:200]}")
                    break
except Exception:
    pass
PYEOF

# --- Read stdin JSON ---
INPUT_JSON=""
if ! INPUT_JSON=$(timeout 2 cat 2>/dev/null); then
    INPUT_JSON="{}"
fi

# --- Parse input ---
parse_result=$(python3 "${TMPDIR_SCRIPTS}/parse_input.py" "$INPUT_JSON" 2>/dev/null) || parse_result=""

SESSION_ID=$(echo "$parse_result" | sed -n '1p')
CWD=$(echo "$parse_result" | sed -n '2p')
TRANSCRIPT_PATH=$(echo "$parse_result" | sed -n '3p')
SOURCE=$(echo "$parse_result" | sed -n '4p')
MODEL=$(echo "$parse_result" | sed -n '5p')

# Defaults
SESSION_ID="${SESSION_ID:-unknown}"
CWD="${CWD:-$(pwd)}"
AGENT_ID="${AGENT_ID:-}"
AGENT_MEMORY_PATH="${AGENT_MEMORY_PATH:-}"
MEMORY_BASE="${MEMORY_BASE:-${AGENT_MEMORY_PATH:-${REPO_DIR}/memory}}"

hook_log_start "session=${SESSION_ID}" "env_warnings=${ENV_WARNINGS}"

# --- Query functions ---
# Each function writes results to stdout and returns 0 on success.
# All stderr goes to stderr (not stdout).

query_compaction_entries() {
    # Search for recent compaction-tagged entries
    local result
    result=$(timeout "$TIMEOUT_SECONDS" \
        env AGENT_ID="$AGENT_ID" $MEMORY_CMD --json-output search \
            "session compaction" \
            --scope own \
            --tag compaction \
            --limit "$MAX_SEARCH_RESULTS" \
            --base "$MEMORY_BASE" \
        2>/dev/null) || return 1

    # Validate we got actual JSON array with results
    if [[ -z "$result" ]] || [[ "$result" == "[]" ]]; then
        return 1
    fi

    echo "$result"
    return 0
}

query_effort_entries() {
    # List recent effort entries for this agent
    local effort_path="${MEMORY_BASE}/${AGENT_ID}/effort"
    # Also try plural form used in STANDARD_DIRS
    local efforts_path="${MEMORY_BASE}/${AGENT_ID}/efforts"

    local result=""
    for path in "$effort_path" "$efforts_path"; do
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

query_recent_search() {
    # Broader search for recent session-related entries
    local result
    result=$(timeout "$TIMEOUT_SECONDS" \
        env AGENT_ID="$AGENT_ID" $MEMORY_CMD --json-output search \
            "session" \
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

# --- Format functions (call temp Python scripts) ---

format_compaction_entries() {
    local json_data="$1"
    python3 "${TMPDIR_SCRIPTS}/format_compaction.py" "$json_data"
}

format_effort_entries() {
    local json_data="$1"
    python3 "${TMPDIR_SCRIPTS}/format_efforts.py" "$json_data" "$MAX_LS_ENTRIES"
}

format_recent_search() {
    local json_data="$1"
    python3 "${TMPDIR_SCRIPTS}/format_search.py" "$json_data"
}

# --- Detect modified files from transcript (if available) ---
extract_modified_files() {
    if [[ -z "$TRANSCRIPT_PATH" ]] || [[ ! -f "$TRANSCRIPT_PATH" ]]; then
        return 1
    fi

    local files
    files=$(python3 "${TMPDIR_SCRIPTS}/extract_files.py" "$TRANSCRIPT_PATH" 2>/dev/null)

    if [[ -n "$files" ]]; then
        echo "## Files Modified"
        while IFS= read -r filepath; do
            echo "- $filepath"
        done <<< "$files"
        return 0
    fi

    return 1
}

# --- Main output assembly ---
main() {
    # Header
    echo "=== Restored Context After Compaction ==="
    echo ""

    # Active Session block (always present)
    echo "## Active Session"
    echo "- Working directory: ${CWD}"
    echo "- Session: ${SESSION_ID}"
    [[ -n "$MODEL" ]] && echo "- Model: ${MODEL}"
    [[ -n "$AGENT_ID" ]] && echo "- Agent: ${AGENT_ID}"
    echo ""

    # Check if memory CLI is available
    if ! find_memory_cli; then
        echo "## Note"
        echo "- Memory CLI not available; context restoration limited to session metadata."
        echo "- Install agent-memory or set PATH to include the memory CLI."
        echo ""
        echo "=== End Restored Context ==="
        hook_log_skip "memory CLI not available" "session=${SESSION_ID}" "env_warnings=${ENV_WARNINGS}"
        return
    fi

    # Skip memory queries if no AGENT_ID
    if [[ -z "$AGENT_ID" ]]; then
        echo "## Note"
        echo "- AGENT_ID not set; memory queries require agent identity."
        echo "- Set AGENT_ID environment variable for full context restoration."
        echo ""
        echo "=== End Restored Context ==="
        hook_log_skip "AGENT_ID not set" "session=${SESSION_ID}" "env_warnings=${ENV_WARNINGS}"
        return
    fi

    local has_content=false
    local context_bytes=0

    # Query 1: Compaction-tagged entries (most specific)
    local compaction_json=""
    if compaction_json=$(query_compaction_entries); then
        local formatted
        if formatted=$(format_compaction_entries "$compaction_json"); then
            echo "$formatted"
            echo ""
            has_content=true
            context_bytes=$(( context_bytes + ${#formatted} ))
        fi
    fi

    # Query 2: Effort entries (active work)
    local effort_json=""
    if effort_json=$(query_effort_entries); then
        local formatted
        if formatted=$(format_effort_entries "$effort_json"); then
            echo "$formatted"
            echo ""
            has_content=true
            context_bytes=$(( context_bytes + ${#formatted} ))
        fi
    fi

    # Query 3: Fallback broader search (only if we got nothing specific)
    if [[ "$has_content" == false ]]; then
        local search_json=""
        if search_json=$(query_recent_search); then
            local formatted
            if formatted=$(format_recent_search "$search_json"); then
                echo "$formatted"
                echo ""
                has_content=true
                context_bytes=$(( context_bytes + ${#formatted} ))
            fi
        fi
    fi

    # Modified files from transcript
    local files_section=""
    if files_section=$(extract_modified_files 2>/dev/null); then
        echo "$files_section"
        echo ""
        context_bytes=$(( context_bytes + ${#files_section} ))
    fi

    # Current task hint from the most recent compaction entry
    if [[ -n "$compaction_json" ]]; then
        local task_hint
        task_hint=$(python3 "${TMPDIR_SCRIPTS}/extract_task.py" "$compaction_json" 2>/dev/null) || true
        if [[ -n "$task_hint" ]]; then
            echo "$task_hint"
            echo ""
            context_bytes=$(( context_bytes + ${#task_hint} ))
        fi
    fi

    # Minimal fallback if nothing was found
    if [[ "$has_content" == false ]]; then
        echo "## Note"
        echo "- No recent memory entries found for agent '${AGENT_ID}'."
        echo "- This may be a fresh session or memory entries may use different tags."
        echo ""
        hook_log_success "session=${SESSION_ID}" "context_bytes=0" "env_warnings=${ENV_WARNINGS}"
    else
        hook_log_success "session=${SESSION_ID}" "context_bytes=${context_bytes}" "env_warnings=${ENV_WARNINGS}"
    fi

    echo "=== End Restored Context ==="
}

# --- Entry point ---
# Send all diagnostic output to stderr
exec 3>&2

main 2>&3

exit 0
