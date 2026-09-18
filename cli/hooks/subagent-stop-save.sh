#!/usr/bin/env bash
# ============================================================================
# subagent-stop-save.sh — SubagentStop hook for agent-memory integration
#
# Called by Claude Code's SubagentStop hook when a subagent completes work.
# Parses the subagent's transcript JSONL to extract task summary, key
# decisions, files modified, errors, and the result summary. Writes a
# structured memory entry via the agent-memory CLI so that subagent
# learnings persist across sessions.
#
# Input (JSON on stdin):
#   { "session_id", "transcript_path", "cwd", "hook_event_name",
#     "agent_type", "agent_id" }
#
# Output: NONE to stdout (SubagentStop hooks do not inject context).
#         All output goes to stderr.
#
# Environment variables:
#   AGENT_ID           — Agent identity for memory authorship (REQUIRED)
#   AGENT_MEMORY_PATH  — Base directory for memory entries (default: memory)
#   MEMORY_BASE        — Override for --base flag (default: AGENT_MEMORY_PATH)
#
# Exit codes:
#   0 — Always (never break subagent completion, even on error)
#
# Logging:
#   Structured JSON Lines to hooks.log (via common.sh logging functions).
#   Human-readable diagnostics to stderr.
#
# Performance: Processes last 200 lines of transcript, targets <10s completion.
# ============================================================================

set -uo pipefail

# ---------------------------------------------------------------------------
# Logging helpers (stderr only — SubagentStop does not inject context)
# ---------------------------------------------------------------------------
log_info()  { echo "[subagent-stop-save] $*" >&2; }
log_warn()  { echo "[subagent-stop-save] WARNING: $*" >&2; }
log_error() { echo "[subagent-stop-save] ERROR: $*" >&2; }

# ---------------------------------------------------------------------------
# Source shared utilities (find_memory_cli, REPO_DIR, logging functions)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

# ---------------------------------------------------------------------------
# Initialize structured logging
# ---------------------------------------------------------------------------
hook_log_init "SubagentStop"

# ---------------------------------------------------------------------------
# Graceful exit wrapper — never return non-zero
# ---------------------------------------------------------------------------
safe_exit() {
    local code="${1:-0}"
    if [ "$code" -ne 0 ]; then
        log_warn "Exiting gracefully despite error (exit 0 to not break subagent completion)"
    fi
    exit 0
}
trap 'safe_exit 1' ERR

# ---------------------------------------------------------------------------
# Check environment variables and log warnings
# ---------------------------------------------------------------------------
ENV_WARNINGS="$(hook_check_env)"
if [[ -n "$ENV_WARNINGS" ]]; then
    log_warn "Missing environment variables: $ENV_WARNINGS"
fi

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
if [ -z "${AGENT_ID:-}" ]; then
    log_error "AGENT_ID environment variable is not set. Required for memory authorship."
    hook_log_error "AGENT_ID not set" "env_warnings=${ENV_WARNINGS}"
    safe_exit 1
fi
MEMORY_BASE="${MEMORY_BASE:-${AGENT_MEMORY_PATH:-${REPO_DIR}/memory}}"
MAX_LINES=200

# ---------------------------------------------------------------------------
# Read and parse stdin JSON
# ---------------------------------------------------------------------------
STDIN_JSON=""
if ! STDIN_JSON=$(timeout 2 cat 2>/dev/null); then
    STDIN_JSON="{}"
fi

if [ -z "$STDIN_JSON" ] || [ "$STDIN_JSON" = "{}" ]; then
    log_error "No JSON received on stdin"
    hook_log_error "No JSON received on stdin" "env_warnings=${ENV_WARNINGS}"
    safe_exit 1
fi

PARSED="$(echo "$STDIN_JSON" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except (json.JSONDecodeError, ValueError):
    d = {}
print(d.get('session_id', 'unknown'))
print(d.get('transcript_path', ''))
print(d.get('agent_type', 'unknown'))
print(d.get('agent_id', 'unknown'))
print(d.get('cwd', ''))
" 2>/dev/null)" || {
    log_error "Failed to parse stdin JSON"
    hook_log_error "Failed to parse stdin JSON" "env_warnings=${ENV_WARNINGS}"
    safe_exit 1
}

SESSION_ID="$(echo "$PARSED" | sed -n '1p')"
TRANSCRIPT_PATH="$(echo "$PARSED" | sed -n '2p')"
AGENT_TYPE="$(echo "$PARSED" | sed -n '3p')"
AGENT_ID_FIELD="$(echo "$PARSED" | sed -n '4p')"
CWD="$(echo "$PARSED" | sed -n '5p')"

log_info "Session: $SESSION_ID | Agent: $AGENT_TYPE ($AGENT_ID_FIELD) | Transcript: $TRANSCRIPT_PATH"
hook_log_start "session=${SESSION_ID}" "agent_type=${AGENT_TYPE}" "env_warnings=${ENV_WARNINGS}"

# ---------------------------------------------------------------------------
# Validate transcript file
# ---------------------------------------------------------------------------
if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
    log_warn "Transcript file not found or empty path: '$TRANSCRIPT_PATH'"
    hook_log_error "Transcript file not found: ${TRANSCRIPT_PATH}" "session=${SESSION_ID}" "agent_type=${AGENT_TYPE}"
    safe_exit 1
fi

# Check line count — skip trivial subagents (<10 lines)
LINE_COUNT="$(wc -l < "$TRANSCRIPT_PATH" 2>/dev/null || echo 0)"
if [ "$LINE_COUNT" -lt 10 ]; then
    log_info "Transcript has only $LINE_COUNT lines (<10), skipping trivial subagent"
    hook_log_skip "Trivial subagent (${LINE_COUNT} lines)" "session=${SESSION_ID}" "agent_type=${AGENT_TYPE}"
    safe_exit 0
fi

# ---------------------------------------------------------------------------
# Check that memory CLI is available (shared discovery from common.sh)
# ---------------------------------------------------------------------------
if ! find_memory_cli; then
    log_warn "memory CLI not found in PATH or virtualenvs. Install with: pip install -e $REPO_DIR"
    hook_log_error "memory CLI not found" "session=${SESSION_ID}" "agent_type=${AGENT_TYPE}"
    safe_exit 1
fi
log_info "Using memory CLI: $MEMORY_CMD"

# ---------------------------------------------------------------------------
# Extract context from transcript using Python (single-pass, handles JSONL)
# ---------------------------------------------------------------------------
BODY="$(tail -n "$MAX_LINES" "$TRANSCRIPT_PATH" | python3 -c '
import json
import sys

lines = sys.stdin.readlines()

task_description = ""
key_decisions = []
files_modified = set()
errors_encountered = []
last_assistant_text = ""

for line in lines:
    line = line.strip()
    if not line:
        continue
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        continue

    entry_type = entry.get("type", "")
    subtype = entry.get("subtype", "")

    # Skip timing data
    if entry_type == "system" and subtype == "turn_duration":
        continue

    message = entry.get("message", {})
    content = message.get("content", "")

    # --- User messages: capture the first substantial one as the task ---
    if entry_type == "user":
        texts = []
        if isinstance(content, str) and content.strip():
            texts.append(content.strip())
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            texts.append(text)
                    # Check for tool_result errors
                    elif block.get("type") == "tool_result" and block.get("is_error"):
                        err_content = block.get("content", "")
                        if isinstance(err_content, str):
                            err_text = err_content.strip()
                        elif isinstance(err_content, list):
                            err_text = " ".join(
                                b.get("text", "") for b in err_content
                                if isinstance(b, dict) and b.get("type") == "text"
                            ).strip()
                        else:
                            err_text = str(err_content)
                        if err_text:
                            if len(err_text) > 200:
                                err_text = err_text[:197] + "..."
                            errors_encountered.append(err_text)

        for text in texts:
            if not task_description and len(text) > 20:
                task_description = text
                if len(task_description) > 500:
                    task_description = task_description[:497] + "..."

    # --- Assistant messages ---
    elif entry_type == "assistant":
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")

                # Text responses -- potential decisions/conclusions
                if block_type == "text":
                    text = block.get("text", "").strip()
                    if text:
                        # Track last substantial assistant text for result summary
                        if len(text) > 50:
                            last_assistant_text = text

                        # Heuristic: decision language (narrow signals)
                        if len(text) > 100:
                            lower = text.lower()
                            decision_signals = [
                                "decided to", "decision:", "conclusion:",
                                "the approach is", "going with", "chosen",
                                "the plan is", "the fix is", "root cause",
                                "resolved by", "strategy:", "solution:",
                            ]
                            if any(sig in lower for sig in decision_signals):
                                snippet = text
                                if len(snippet) > 400:
                                    snippet = snippet[:397] + "..."
                                key_decisions.append(snippet)

                # Tool use -- file modifications
                elif block_type == "tool_use":
                    tool_name = block.get("name", "")
                    tool_input = block.get("input", {})

                    if tool_name in ("Edit", "Write", "NotebookEdit"):
                        fp = tool_input.get("file_path", "")
                        if fp:
                            files_modified.add(fp)

# --- Build structured markdown body ---
sections = []

# Task section
task_lines = ["## Task", ""]
if task_description:
    task_lines.append(task_description)
else:
    task_lines.append("(No task description extracted)")
sections.append("\n".join(task_lines))

# Agent section
agent_type = sys.argv[1] if len(sys.argv) > 1 else "unknown"
agent_id = sys.argv[2] if len(sys.argv) > 2 else "unknown"
session_id = sys.argv[3] if len(sys.argv) > 3 else "unknown"
cwd = sys.argv[4] if len(sys.argv) > 4 else "unknown"

agent_lines = ["## Agent", "Subagent identity and session context."]
agent_lines.append(f"- Type: {agent_type}")
agent_lines.append(f"- ID: {agent_id}")
agent_lines.append(f"- Session: {session_id}")
sections.append("\n".join(agent_lines))

# Key Decisions
if key_decisions:
    dec_lines = ["## Key Decisions", "Key decisions and conclusions reached during this session."]
    for d in key_decisions[-5:]:
        dec_lines.append(f"- {d}")
    sections.append("\n".join(dec_lines))

# Files Modified
if files_modified:
    file_lines = ["## Files Modified", "Files created or edited during this session."]
    for f in sorted(files_modified):
        file_lines.append(f"- `{f}`")
    sections.append("\n".join(file_lines))

# Errors
if errors_encountered:
    err_lines = ["## Errors", "Errors encountered during subagent execution."]
    for e in errors_encountered[-5:]:
        err_lines.append(f"- {e}")
    sections.append("\n".join(err_lines))

# Result summary (last substantial assistant text, truncated to 500 chars)
result_lines = ["## Result", ""]
if last_assistant_text:
    result_text = last_assistant_text
    if len(result_text) > 500:
        result_text = result_text[:497] + "..."
    result_lines.append(result_text)
else:
    result_lines.append("(No result summary extracted)")
sections.append("\n".join(result_lines))

# Session Metadata
meta_lines = ["## Session Metadata", "Runtime context for this subagent invocation."]
meta_lines.append(f"- Entries processed: {len(lines)} transcript lines (tail)")
meta_lines.append(f"- Working directory: {cwd}")
sections.append("\n".join(meta_lines))

body = "\n\n".join(sections)
print(body)
' "$AGENT_TYPE" "$AGENT_ID_FIELD" "$SESSION_ID" "$CWD" 2>/dev/null)" || {
    log_error "Python transcript extraction failed"
    hook_log_error "Python transcript extraction failed" "session=${SESSION_ID}" "agent_type=${AGENT_TYPE}"
    safe_exit 1
}

# ---------------------------------------------------------------------------
# Validate we got something useful
# ---------------------------------------------------------------------------
if [ -z "$BODY" ]; then
    log_warn "Extracted empty body from transcript, skipping memory write"
    hook_log_skip "Empty body extracted from transcript" "session=${SESSION_ID}" "agent_type=${AGENT_TYPE}"
    safe_exit 0
fi

BODY_BYTES="${#BODY}"

# ---------------------------------------------------------------------------
# Generate unique entry name
# ---------------------------------------------------------------------------
TIMESTAMP="$(date +%s)"
ENTRY_NAME="subagent-${AGENT_TYPE}-${TIMESTAMP}-$$"

# ---------------------------------------------------------------------------
# Build description from task (first line of task_description)
# ---------------------------------------------------------------------------
FIRST_LINE="$(echo "$BODY" | sed -n '/^## Task$/,/^## /{/^## Task$/d;/^## /d;/^$/d;p;}' | head -n1)"
if [ -z "$FIRST_LINE" ] || [ "$FIRST_LINE" = "(No task description extracted)" ]; then
    DESCRIPTION="Subagent completion: ${AGENT_TYPE}"
else
    # Truncate for description field
    TRUNCATED="${FIRST_LINE:0:120}"
    DESCRIPTION="Subagent completion: ${AGENT_TYPE} -- ${TRUNCATED}"
fi

# ---------------------------------------------------------------------------
# Write memory entry to _archive/ (keeps audit trail, excluded from BM25)
# ---------------------------------------------------------------------------
log_info "Writing archive entry: $ENTRY_NAME"

ARCHIVE_DIR="${MEMORY_BASE}/${AGENT_ID}/efforts/_archive"
mkdir -p "$ARCHIVE_DIR" 2>/dev/null || true

MEMORY_OUTPUT=""
if MEMORY_OUTPUT=$(AGENT_ID="$AGENT_ID" $MEMORY_CMD new \
    "$ENTRY_NAME" \
    -d "$DESCRIPTION" \
    -c efforts \
    --confidence working \
    --status archived \
    -t "subagent,${AGENT_TYPE},automated,raw-dump" \
    -b "$BODY" \
    --no-git \
    --base "$MEMORY_BASE" 2>&1); then
    # Move the entry from efforts/ to efforts/_archive/
    CREATED_FILE="${MEMORY_BASE}/${AGENT_ID}/efforts/${ENTRY_NAME}.md"
    ARCHIVE_DEST="${ARCHIVE_DIR}/${ENTRY_NAME}.md"
    if [ -f "$CREATED_FILE" ]; then
        mv "$CREATED_FILE" "$ARCHIVE_DEST" 2>/dev/null || true
        log_info "Archived entry: $ARCHIVE_DEST"
    fi
else
    log_error "memory new command failed (archive write)"
    echo "$MEMORY_OUTPUT" | while IFS= read -r line; do
        [ -n "$line" ] && log_error "memory: $line"
    done
    hook_log_error "memory new command failed" "session=${SESSION_ID}" "agent_type=${AGENT_TYPE}" "context_bytes=${BODY_BYTES}"
    safe_exit 1
fi

log_info "Done"
hook_log_success "session=${SESSION_ID}" "agent_type=${AGENT_TYPE}" "entries_written=1" "context_bytes=${BODY_BYTES}" "env_warnings=${ENV_WARNINGS}"
safe_exit 0
