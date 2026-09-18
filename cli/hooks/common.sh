#!/usr/bin/env bash
# ============================================================================
# common.sh — Shared functions for agent-memory hook scripts
#
# Source this file from hook scripts to get common utilities:
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "${SCRIPT_DIR}/common.sh"
#
# Provides:
#   find_memory_cli    — Locates the memory CLI binary, sets MEMORY_CMD
#   MEMORY_CMD         — Path to memory CLI (set by find_memory_cli)
#   REPO_DIR           — Resolved path to the agent-memory repository root
#   hook_log_init      — Initialize logging (resolve log file path, record start time)
#   hook_log           — Append a JSON Lines entry to hooks.log
#   hook_log_start     — Log hook start event
#   hook_log_success   — Log hook success with duration
#   hook_log_error     — Log hook error with duration and message
#   hook_log_skip      — Log hook skip with reason
#   hook_check_env     — Check and log required/optional environment variables
#   HOOK_LOG_FILE      — Path to hooks.log (set by hook_log_init)
#   HOOK_START_MS      — Epoch milliseconds at hook start (set by hook_log_init)
#
# Version: 2026.02
# ============================================================================

# ---------------------------------------------------------------------------
# REPO_DIR — resolve once, reusable by all hooks
# ---------------------------------------------------------------------------
# SCRIPT_DIR must be set by the sourcing script before sourcing this file.
# Falls back to the directory of this file if not set.
if [[ -z "${SCRIPT_DIR:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---------------------------------------------------------------------------
# MEMORY_CMD — set by find_memory_cli()
# ---------------------------------------------------------------------------
MEMORY_CMD=""

# ---------------------------------------------------------------------------
# find_memory_cli — Locate the memory CLI binary
#
# Searches PATH, common install locations, and project virtualenvs.
# Sets MEMORY_CMD to the resolved path on success.
#
# Returns:
#   0 — found, MEMORY_CMD is set
#   1 — not found
# ---------------------------------------------------------------------------
find_memory_cli() {
    # Check common locations in priority order
    local candidates=(
        "memory"
        "${HOME}/.local/bin/memory"
        "/usr/local/bin/memory"
    )

    # Check virtualenvs relative to the repo this script lives in,
    # AND relative to HOME (may differ if HOME != repo parent)
    local project_venvs=(
        "${REPO_DIR}/.venv/bin/memory"
        "${REPO_DIR}/venv/bin/memory"
        "${HOME}/projects/agent-memory/.venv/bin/memory"
        "${HOME}/projects/agent-memory/venv/bin/memory"
    )

    for cmd in "${candidates[@]}"; do
        if command -v "$cmd" &>/dev/null; then
            MEMORY_CMD="$cmd"
            return 0
        fi
    done

    for cmd in "${project_venvs[@]}"; do
        if [[ -x "$cmd" ]]; then
            MEMORY_CMD="$cmd"
            return 0
        fi
    done

    return 1
}

# ---------------------------------------------------------------------------
# Structured logging functions — JSON Lines to hooks.log
#
# Log file location matches Kelvin's Python logging module:
#   AGENT_MEMORY_LOG_PATH (default: ~/.agent-memory/) + hooks.log
#
# Format: one JSON object per line with fields:
#   ts, level, hook, agent, outcome, [session, agent_type, context_bytes,
#   entries_written, duration_ms, error, env_warnings, ...]
# ---------------------------------------------------------------------------

HOOK_LOG_FILE=""
HOOK_START_MS=""

# ---------------------------------------------------------------------------
# _epoch_ms — Current time in milliseconds (portable)
# ---------------------------------------------------------------------------
_epoch_ms() {
    python3 -c 'import time; print(int(time.time() * 1000))' 2>/dev/null || \
        echo "$(date +%s)000"
}

# ---------------------------------------------------------------------------
# hook_log_init — Initialize logging for a hook invocation
#
# Args:
#   $1 — hook name (e.g. PreCompact, SessionStart, SubagentStart, SubagentStop)
#
# Sets:
#   HOOK_LOG_FILE — path to hooks.log
#   HOOK_START_MS — epoch milliseconds at init time
#   HOOK_NAME     — the hook name for subsequent log calls
# ---------------------------------------------------------------------------
HOOK_NAME=""

hook_log_init() {
    HOOK_NAME="${1:?hook_log_init requires a hook name}"
    local log_dir="${AGENT_MEMORY_LOG_PATH:-${HOME}/.agent-memory}"
    mkdir -p "$log_dir" 2>/dev/null || true
    HOOK_LOG_FILE="${log_dir}/hooks.log"
    HOOK_START_MS="$(_epoch_ms)"
}

# ---------------------------------------------------------------------------
# hook_log — Append a JSON Lines entry to hooks.log
#
# Args:
#   Named fields passed as key=value pairs. Required: outcome.
#   All other fields are optional and added to the JSON object.
#
# Usage:
#   hook_log outcome=ok session="abc123" entries_written=1
#   hook_log outcome=error error="CLI not found"
# ---------------------------------------------------------------------------
hook_log() {
    # If log file not initialized, silently skip
    [[ -z "$HOOK_LOG_FILE" ]] && return 0

    local outcome="" session="" agent_type="" context_bytes=""
    local entries_written="" duration_ms="" error="" env_warnings=""

    # Parse key=value arguments
    local arg
    for arg in "$@"; do
        case "$arg" in
            outcome=*)        outcome="${arg#outcome=}" ;;
            session=*)        session="${arg#session=}" ;;
            agent_type=*)     agent_type="${arg#agent_type=}" ;;
            context_bytes=*)  context_bytes="${arg#context_bytes=}" ;;
            entries_written=*) entries_written="${arg#entries_written=}" ;;
            duration_ms=*)    duration_ms="${arg#duration_ms=}" ;;
            error=*)          error="${arg#error=}" ;;
            env_warnings=*)   env_warnings="${arg#env_warnings=}" ;;
        esac
    done

    # Build JSON via Python for proper escaping
    python3 -c '
import json, sys, os
from datetime import datetime, timezone

entry = {
    "ts": datetime.now(timezone.utc).isoformat(),
    "level": "error" if sys.argv[1] == "error" else "info",
    "hook": sys.argv[2],
    "agent": os.environ.get("AGENT_ID", "unknown"),
    "outcome": sys.argv[1],
}
# Add optional fields (only if non-empty)
pairs = sys.argv[3:]
for p in pairs:
    if "=" not in p:
        continue
    k, v = p.split("=", 1)
    if not v:
        continue
    # Numeric fields
    if k in ("context_bytes", "entries_written", "duration_ms"):
        try:
            if "." in v:
                entry[k] = round(float(v), 1)
            else:
                entry[k] = int(v)
        except ValueError:
            entry[k] = v
    else:
        entry[k] = v

print(json.dumps(entry, default=str))
' "$outcome" "$HOOK_NAME" \
  "session=${session}" \
  "agent_type=${agent_type}" \
  "context_bytes=${context_bytes}" \
  "entries_written=${entries_written}" \
  "duration_ms=${duration_ms}" \
  "error=${error}" \
  "env_warnings=${env_warnings}" \
  >> "$HOOK_LOG_FILE" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# _elapsed_ms — Calculate milliseconds since HOOK_START_MS
# ---------------------------------------------------------------------------
_elapsed_ms() {
    local now
    now="$(_epoch_ms)"
    if [[ -n "$HOOK_START_MS" ]] && [[ -n "$now" ]]; then
        echo $(( now - HOOK_START_MS ))
    else
        echo "0"
    fi
}

# ---------------------------------------------------------------------------
# hook_log_start — Log that a hook has started
#
# Args:
#   key=value pairs for additional context (e.g. session=abc123)
# ---------------------------------------------------------------------------
hook_log_start() {
    hook_log outcome=start "$@"
}

# ---------------------------------------------------------------------------
# hook_log_success — Log successful hook completion with duration
#
# Args:
#   key=value pairs for additional context
# ---------------------------------------------------------------------------
hook_log_success() {
    local dur
    dur="$(_elapsed_ms)"
    hook_log outcome=ok "duration_ms=${dur}" "$@"
}

# ---------------------------------------------------------------------------
# hook_log_error — Log hook error with duration and message
#
# Args:
#   $1 — error message (required)
#   Remaining args: key=value pairs for additional context
# ---------------------------------------------------------------------------
hook_log_error() {
    local msg="${1:?hook_log_error requires an error message}"
    shift
    local dur
    dur="$(_elapsed_ms)"
    hook_log outcome=error "duration_ms=${dur}" "error=${msg}" "$@"
}

# ---------------------------------------------------------------------------
# hook_log_skip — Log that a hook was skipped with a reason
#
# Args:
#   $1 — skip reason (required)
#   Remaining args: key=value pairs for additional context
# ---------------------------------------------------------------------------
hook_log_skip() {
    local reason="${1:?hook_log_skip requires a reason}"
    shift
    local dur
    dur="$(_elapsed_ms)"
    hook_log outcome=skip "duration_ms=${dur}" "error=${reason}" "$@"
}

# ---------------------------------------------------------------------------
# hook_check_env — Check required/optional env vars and log warnings
#
# Checks AGENT_ID, AGENT_MEMORY_PATH, and AGENT_MEMORY_CACHE_PATH.
# Returns a comma-separated string of missing variable names (empty if all set).
#
# Usage:
#   local warnings
#   warnings=$(hook_check_env)
#   if [[ -n "$warnings" ]]; then
#       hook_log outcome=ok env_warnings="$warnings"
#   fi
# ---------------------------------------------------------------------------
hook_check_env() {
    local missing=()
    [[ -z "${AGENT_ID:-}" ]] && missing+=("AGENT_ID")
    [[ -z "${AGENT_MEMORY_PATH:-}" ]] && missing+=("AGENT_MEMORY_PATH")
    [[ -z "${AGENT_MEMORY_CACHE_PATH:-}" ]] && missing+=("AGENT_MEMORY_CACHE")

    if [[ ${#missing[@]} -gt 0 ]]; then
        local IFS=","
        echo "${missing[*]}"
    fi
}
