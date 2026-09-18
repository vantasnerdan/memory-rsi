#!/usr/bin/env bash
# ============================================================================
# pre-compact-save.sh — PreCompact hook for agent-memory integration
#
# Called by Claude Code's PreCompact hook before context window compaction.
# Receives JSON on stdin with session metadata, parses the session transcript
# JSONL file to extract key context (user requests, decisions, files modified,
# errors, commands), and writes a structured memory entry via the agent-memory
# CLI so that important session context survives compaction.
#
# Input (JSON on stdin):
#   { "session_id", "transcript_path", "cwd", "hook_event_name",
#     "trigger" (manual|auto), "custom_instructions" }
#
# Environment variables:
#   AGENT_ID           — Agent identity for memory authorship (REQUIRED)
#   AGENT_MEMORY_PATH  — Base directory for memory entries (default: repo's memory/)
#   MEMORY_BASE        — Override for --base flag (default: AGENT_MEMORY_PATH)
#
# Exit codes:
#   0 — Always (never break compaction, even on error)
#
# Logging:
#   Structured JSON Lines to hooks.log (via common.sh logging functions).
#   Human-readable diagnostics to stderr.
#
# Performance: Processes last 200 lines of transcript, targets <15s completion.
# ============================================================================

set -uo pipefail

# ---------------------------------------------------------------------------
# Logging helpers (stderr only — stdout is not injected by PreCompact)
# ---------------------------------------------------------------------------
log_info()  { echo "[pre-compact-save] $*" >&2; }
log_warn()  { echo "[pre-compact-save] WARNING: $*" >&2; }
log_error() { echo "[pre-compact-save] ERROR: $*" >&2; }

# ---------------------------------------------------------------------------
# Source shared utilities (find_memory_cli, REPO_DIR, logging functions)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

# ---------------------------------------------------------------------------
# Initialize structured logging
# ---------------------------------------------------------------------------
hook_log_init "PreCompact"

# ---------------------------------------------------------------------------
# Graceful exit wrapper — never return non-zero
# ---------------------------------------------------------------------------
safe_exit() {
    local code="${1:-0}"
    if [ "$code" -ne 0 ]; then
        log_warn "Exiting gracefully despite error (exit 0 to not break compaction)"
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
print(d.get('trigger', 'unknown'))
print(d.get('cwd', ''))
" 2>/dev/null)" || {
    log_error "Failed to parse stdin JSON"
    hook_log_error "Failed to parse stdin JSON" "env_warnings=${ENV_WARNINGS}"
    safe_exit 1
}

SESSION_ID="$(echo "$PARSED" | sed -n '1p')"
TRANSCRIPT_PATH="$(echo "$PARSED" | sed -n '2p')"
TRIGGER="$(echo "$PARSED" | sed -n '3p')"
CWD="$(echo "$PARSED" | sed -n '4p')"

log_info "Session: $SESSION_ID | Trigger: $TRIGGER | Transcript: $TRANSCRIPT_PATH"
hook_log_start "session=${SESSION_ID}" "env_warnings=${ENV_WARNINGS}"

# ---------------------------------------------------------------------------
# Validate transcript file
# ---------------------------------------------------------------------------
if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
    log_warn "Transcript file not found or empty path: '$TRANSCRIPT_PATH'"
    hook_log_error "Transcript file not found: ${TRANSCRIPT_PATH}" "session=${SESSION_ID}"
    safe_exit 1
fi

# ---------------------------------------------------------------------------
# Check that memory CLI is available (shared discovery from common.sh)
# ---------------------------------------------------------------------------
if ! find_memory_cli; then
    log_warn "memory CLI not found in PATH or virtualenvs. Install with: pipx install agent-memory"
    hook_log_error "memory CLI not found" "session=${SESSION_ID}"
    safe_exit 1
fi
log_info "Using memory CLI: $MEMORY_CMD"

# ---------------------------------------------------------------------------
# Extract context from transcript using Python (handles nested JSONL safely)
# ---------------------------------------------------------------------------
BODY="$(tail -n "$MAX_LINES" "$TRANSCRIPT_PATH" | python3 -c '
import json
import sys

lines = sys.stdin.readlines()

user_requests = []
key_decisions = []
files_modified = set()
errors_encountered = []
commands_run = []

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

    # Reset accumulators on compaction boundary (only keep post-boundary context)
    if entry_type == "system" and subtype == "compact_boundary":
        user_requests.clear()
        key_decisions.clear()
        files_modified.clear()
        errors_encountered.clear()
        commands_run.clear()
        continue

    # Skip timing data
    if entry_type == "system" and subtype == "turn_duration":
        continue

    message = entry.get("message", {})
    content = message.get("content", "")

    # --- User messages ---
    if entry_type == "user":
        if isinstance(content, str) and content.strip():
            text = content.strip()
            # Truncate long user messages
            if len(text) > 300:
                text = text[:297] + "..."
            user_requests.append(text)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            if len(text) > 300:
                                text = text[:297] + "..."
                            user_requests.append(text)
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

    # --- Assistant messages ---
    elif entry_type == "assistant":
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")

                # Text responses — potential decisions/conclusions
                if block_type == "text":
                    text = block.get("text", "").strip()
                    if text and len(text) > 100:
                        # Heuristic: lines with decision language (narrow signals to reduce noise)
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

                # Tool use — file modifications and commands
                elif block_type == "tool_use":
                    tool_name = block.get("name", "")
                    tool_input = block.get("input", {})

                    if tool_name in ("Edit", "Write", "NotebookEdit"):
                        fp = tool_input.get("file_path", "")
                        if fp:
                            files_modified.add(fp)

                    elif tool_name == "Bash":
                        cmd = tool_input.get("command", "")
                        desc = tool_input.get("description", "")
                        if cmd:
                            entry_text = cmd
                            if len(entry_text) > 200:
                                entry_text = entry_text[:197] + "..."
                            if desc:
                                entry_text = f"{desc}: {entry_text}"
                            commands_run.append(entry_text)

# --- Build structured markdown body ---
sections = []

if user_requests:
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for r in user_requests:
        key = r[:100]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    lines_out = ["## User Requests", "Tasks and requests from the user during this session."]
    for r in unique[-10:]:  # Last 10 unique requests
        lines_out.append(f"- {r}")
    sections.append("\n".join(lines_out))

if key_decisions:
    lines_out = ["## Key Decisions", "Key decisions and conclusions reached during this session."]
    # Take last 5 decisions (most recent = most relevant)
    for d in key_decisions[-5:]:
        lines_out.append(f"- {d}")
    sections.append("\n".join(lines_out))

if files_modified:
    lines_out = ["## Files Modified", "Files created or edited during this session."]
    for f in sorted(files_modified):
        lines_out.append(f"- `{f}`")
    sections.append("\n".join(lines_out))

if errors_encountered:
    lines_out = ["## Errors Encountered", "Errors encountered during this session."]
    for e in errors_encountered[-5:]:  # Last 5 errors
        lines_out.append(f"- {e}")
    sections.append("\n".join(lines_out))

if commands_run:
    lines_out = ["## Commands Run", "Shell commands executed during this session."]
    for c in commands_run[-10:]:  # Last 10 commands
        lines_out.append(f"- `{c}`")
    sections.append("\n".join(lines_out))

# Always add session metadata
meta_lines = ["## Session Metadata", "Runtime context for this session."]
sid = sys.argv[1] if len(sys.argv) > 1 else "unknown"
trig = sys.argv[2] if len(sys.argv) > 2 else "unknown"
wdir = sys.argv[3] if len(sys.argv) > 3 else "unknown"
meta_lines.append(f"- **Session ID:** {sid}")
meta_lines.append(f"- **Trigger:** {trig}")
meta_lines.append(f"- **Working directory:** {wdir}")
meta_lines.append(f"- **Entries processed:** {len(lines)} transcript lines (tail)")
sections.append("\n".join(meta_lines))

body = "\n\n".join(sections)

if not body.strip():
    body = "## Summary\n\nNo extractable context found in transcript tail."

print(body)
' "$SESSION_ID" "$TRIGGER" "$CWD" 2>/dev/null)" || {
    log_error "Python transcript extraction failed"
    hook_log_error "Python transcript extraction failed" "session=${SESSION_ID}"
    safe_exit 1
}

# ---------------------------------------------------------------------------
# Validate we got something useful
# ---------------------------------------------------------------------------
if [ -z "$BODY" ]; then
    log_warn "Extracted empty body from transcript, skipping memory write"
    hook_log_skip "Empty body extracted from transcript" "session=${SESSION_ID}"
    safe_exit 0
fi

BODY_BYTES="${#BODY}"

# ---------------------------------------------------------------------------
# Generate unique entry name and temp file path
# ---------------------------------------------------------------------------
TIMESTAMP="$(date +%s)"
SHORT_SESSION="${SESSION_ID:0:12}"
ENTRY_NAME="compaction-${SHORT_SESSION}-${TIMESTAMP}"
TEMP_FILE="/tmp/haiku-curation-${TIMESTAMP}.txt"

# ---------------------------------------------------------------------------
# Write raw context to temp file (for background curation)
# ---------------------------------------------------------------------------
echo "$BODY" > "$TEMP_FILE" 2>/dev/null || true
log_info "Raw context written to temp file: $TEMP_FILE"

# ---------------------------------------------------------------------------
# Write raw context to _archive/ (not efforts/) with --no-git
# This preserves the raw dump for audit trail while keeping it out of
# the BM25 search index (underscore-prefix convention).
# ---------------------------------------------------------------------------
log_info "Writing archive entry: $ENTRY_NAME"

# Ensure _archive/ directory exists under the agent's efforts/ dir
ARCHIVE_DIR="${MEMORY_BASE}/${AGENT_ID}/efforts/_archive"
mkdir -p "$ARCHIVE_DIR" 2>/dev/null || true

MEMORY_OUTPUT=""
if MEMORY_OUTPUT=$(AGENT_ID="$AGENT_ID" $MEMORY_CMD new \
    "$ENTRY_NAME" \
    -d "Pre-compaction context snapshot (raw, archived)" \
    -c efforts \
    --confidence working \
    --status archived \
    -t "compaction,automated,session-context,raw-dump" \
    -b "$BODY" \
    --no-git \
    --base "$MEMORY_BASE" 2>&1); then
    # Move the entry from efforts/ to efforts/_archive/
    CREATED_FILE="${MEMORY_BASE}/${AGENT_ID}/efforts/${ENTRY_NAME}.md"
    ARCHIVE_DEST="${ARCHIVE_DIR}/${ENTRY_NAME}.md"
    if [ -f "$CREATED_FILE" ]; then
        mv "$CREATED_FILE" "$ARCHIVE_DEST" 2>/dev/null || true
        log_info "Archived raw entry: $ARCHIVE_DEST"
    fi
else
    log_error "memory new command failed (archive write)"
    echo "$MEMORY_OUTPUT" | while IFS= read -r line; do
        [ -n "$line" ] && log_error "memory: $line"
    done
    # Non-fatal: curation can still proceed from temp file
fi

# ---------------------------------------------------------------------------
# Spawn background curation via Haiku (non-blocking)
# ---------------------------------------------------------------------------
CURATE_SCRIPT="${SCRIPT_DIR}/curate_context.py"
if [ -f "$CURATE_SCRIPT" ] && [ -f "$TEMP_FILE" ]; then
    log_info "Spawning background Haiku curation"
    nohup python3 "$CURATE_SCRIPT" \
        --input "$TEMP_FILE" \
        --agent "$AGENT_ID" \
        --source precompact \
        > /dev/null 2>&1 &
    log_info "Background curation PID: $!"
else
    log_warn "Curation script not found or temp file missing; skipping Haiku curation"
fi

log_info "Done (curation running in background)"
hook_log_success "session=${SESSION_ID}" "entries_written=1" "context_bytes=${BODY_BYTES}" "env_warnings=${ENV_WARNINGS}"
safe_exit 0
