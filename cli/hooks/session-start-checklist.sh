#!/usr/bin/env bash
# ============================================================================
# session-start-checklist.sh — Automated session start checklist
#
# Fires on every SessionStart (not just post-compact).
# Automates the deterministic steps from session-lifecycle.md:
#   1. Restart ACP
#   2. Check swarm inbox
#   3. Refresh the memory clone (git pull --ff-only)
# Outputs reminders for judgment-dependent steps.
#
# Environment variables (set in .bashrc or equivalent):
#   AGENT_ID            — Agent name (e.g., "my-agent")
#   SWARM_ID            — Swarm UUID (required for inbox check)
#   SWARM_CLI           — Path to swarm CLI (default: searches PATH)
#   SESSION_STATE_PATH  — Path to session-state.md (default: auto-detected from $HOME)
#   AGENT_MEMORY_PATH   — Path to the memory dir inside the clone; its parent is
#                         the repo refreshed in step 4. Unset = step 4 skipped.
#
# Graduated from rule to hook per Composition Gap issue #44/47.
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

hook_log_init "SessionStartChecklist"
hook_log_start

# ---------------------------------------------------------------------------
# Step 1: Check / restart ACP
# Non-root agents cannot restart system services (PolicyKit blocks it). When
# the service is already active, treat that as success — restart-attempt
# failure is not service failure.
# ---------------------------------------------------------------------------
ACP_STATUS="unknown"
if command -v systemctl &>/dev/null; then
    # First: is the service already running cleanly? (most common case for non-root agents)
    if systemctl is-active --quiet acp 2>/dev/null; then
        # Try to restart to pick up any config changes; non-root will fail with PolicyKit,
        # which is fine — the service is already running with the correct config.
        if systemctl restart acp 2>/dev/null; then
            ACP_STATUS="restarted"
        else
            ACP_STATUS="active"
        fi
    else
        # Service is not active — try to start/restart
        if systemctl restart acp 2>/dev/null; then
            ACP_STATUS="restarted"
        else
            ACP_STATUS="failed"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Step 2: Check swarm inbox (summary only — agent reads full messages)
# ---------------------------------------------------------------------------
SWARM_CMD="${SWARM_CLI:-}"
if [[ -z "$SWARM_CMD" ]]; then
    # Search PATH, then common locations
    SWARM_CMD=$(command -v swarm 2>/dev/null || echo "")
    [[ -z "$SWARM_CMD" ]] && [[ -x "/opt/agent-swarm-protocol/venv/bin/swarm" ]] && \
        SWARM_CMD="/opt/agent-swarm-protocol/venv/bin/swarm"
fi

UNREAD_COUNT=0
if [[ -n "$SWARM_CMD" ]] && [[ -x "$SWARM_CMD" ]] && [[ -n "${SWARM_ID:-}" ]]; then
    UNREAD_COUNT=$("$SWARM_CMD" messages -s "$SWARM_ID" --status unread --count 2>/dev/null | grep -oP '\d+' | head -1 || echo "0")
    [[ -z "$UNREAD_COUNT" ]] && UNREAD_COUNT=0
elif [[ -z "${SWARM_ID:-}" ]]; then
    UNREAD_COUNT="no-swarm-id"
fi

# ---------------------------------------------------------------------------
# Resolve session-state path
# ---------------------------------------------------------------------------
if [[ -n "${SESSION_STATE_PATH:-}" ]]; then
    STATE_PATH="$SESSION_STATE_PATH"
else
    # Auto-detect from the current user's home directory.
    if [[ -f "$HOME/session-state.md" ]]; then
        STATE_PATH="$HOME/session-state.md"
    else
        # Claude Code project memory path pattern.
        STATE_PATH=$(find "$HOME/.claude/projects/" \
            -name "session-state.md" -path "*/memory/*" 2>/dev/null | head -1)
        [[ -z "$STATE_PATH" ]] && STATE_PATH="(not found — set SESSION_STATE_PATH)"
    fi
fi

# ---------------------------------------------------------------------------
# Step 3: Refresh the memory clone.
#
# Freshness of a memory clone is otherwise a SIDE EFFECT OF WRITING: the CLI
# only pulls inside `memory new` / `memory update`. No read command (ls, toc,
# section, search, grep) touches git at all. So an agent that reads all day and
# writes nothing drifts arbitrarily far behind origin and is never told — every
# "I did not find X" may be answered from a snapshot of unknown age.
#
# --ff-only is deliberate: it can never rebase, conflict, or touch local
# commits. If it fails, that failure IS the signal and gets printed — silence
# from a broken refresh is indistinguishable from a healthy one.
# ---------------------------------------------------------------------------
MEM_REPO="${AGENT_MEMORY_PATH:-}"
MEM_REPO="${MEM_REPO%/memory}"
MEM_STATUS="skipped (AGENT_MEMORY_PATH unset)"
if [[ -n "$MEM_REPO" && -d "$MEM_REPO/.git" ]]; then
    MEM_BEFORE=$(git -C "$MEM_REPO" rev-parse --short HEAD 2>/dev/null)
    if MEM_ERR=$(timeout 45 git -C "$MEM_REPO" pull --ff-only 2>&1); then
        MEM_AFTER=$(git -C "$MEM_REPO" rev-parse --short HEAD 2>/dev/null)
        if [[ "$MEM_BEFORE" == "$MEM_AFTER" ]]; then
            MEM_STATUS="already current (${MEM_AFTER})"
        else
            MEM_COUNT=$(git -C "$MEM_REPO" rev-list --count "${MEM_BEFORE}..${MEM_AFTER}" 2>/dev/null)
            MEM_STATUS="updated ${MEM_BEFORE} -> ${MEM_AFTER} (${MEM_COUNT} commit(s) pulled)"
        fi
    else
        MEM_STATUS="*** REFRESH FAILED — memory reads may be STALE *** : $(echo "$MEM_ERR" | tr '\n' ' ' | cut -c1-160)"
    fi
fi

# ---------------------------------------------------------------------------
# Step 4: Monitor liveness inventory.
#
# A persistent Monitor watches something precisely so its silence can be read as
# "nothing happened". That reading is only valid while the monitor is ALIVE. A
# monitor that has DIED emits nothing at all, and its silence is byte-identical
# to a healthy quiet watch — so the agent keeps trusting a rail that is gone.
# An in-script silence guard
# cannot cover this: the guard runs INSIDE the loop, so a dead loop never fires
# it. Liveness has to be probed from outside.
#
# Also catches the opposite fault: STACKED duplicates racing one .seen file,
# which can silently DROP an event when one instance's rewrite clobbers
# another's append.
#
# Declarative and opt-in: reads $HOME/.claude/expected-monitors.conf, one
# "pgrep-pattern|label" per line (# comments allowed). No file = step skipped,
# so this stays a no-op for any agent that has not declared monitors.
#
# pgrep never matches its own process, and the harness wrapper (which carries
# the same script path in its cmdline) is filtered out by its shell-snapshots
# signature — so the count is real instances, not self-matches. That distinction
# is the whole reason this is not a naive `pgrep -c`.
# ---------------------------------------------------------------------------
MON_CONF="${MONITOR_EXPECT_FILE:-$HOME/.claude/expected-monitors.conf}"
MON_LINE="skipped (no expected-monitors.conf)"
MON_ALERT=""
if [[ -f "$MON_CONF" ]]; then
    MON_PARTS=()
    MON_BAD=0
    MON_TOTAL=0
    while IFS='|' read -r MON_PAT MON_LABEL; do
        MON_PAT="${MON_PAT#"${MON_PAT%%[![:space:]]*}"}"   # ltrim
        [[ -z "$MON_PAT" || "$MON_PAT" == \#* ]] && continue
        [[ -z "$MON_LABEL" ]] && MON_LABEL="$MON_PAT"
        MON_TOTAL=$((MON_TOTAL + 1))
        MON_N=0
        for MON_PID in $(pgrep -f -- "$MON_PAT" 2>/dev/null); do
            MON_CMD=$(tr '\0' ' ' < "/proc/${MON_PID}/cmdline" 2>/dev/null)
            [[ "$MON_CMD" == *shell-snapshots* ]] && continue   # harness wrapper, not an instance
            MON_N=$((MON_N + 1))
        done
        if [[ "$MON_N" -eq 0 ]]; then
            MON_PARTS+=("${MON_LABEL}: *** DEAD ***")
            MON_BAD=$((MON_BAD + 1))
        elif [[ "$MON_N" -gt 1 ]]; then
            MON_PARTS+=("${MON_LABEL}: *** STACKED x${MON_N} ***")
            MON_BAD=$((MON_BAD + 1))
        else
            MON_PARTS+=("${MON_LABEL}: ok")
        fi
    done < "$MON_CONF"
    # Join with " · ". Note: IFS joining only uses the FIRST char of IFS, so a
    # multi-char separator cannot be done that way — build it explicitly.
    MON_JOINED=""
    for MON_P in "${MON_PARTS[@]}"; do
        [[ -n "$MON_JOINED" ]] && MON_JOINED+=" · "
        MON_JOINED+="$MON_P"
    done
    MON_LINE="${MON_TOTAL} declared — ${MON_JOINED}"
    if [[ "$MON_BAD" -gt 0 ]]; then
        MON_ALERT="    !! ${MON_BAD} monitor(s) not healthy. A DEAD monitor's silence is NOT evidence of quiet.
    !! Restart: pgrep first, then exactly ONE Monitor call per script. STACKED = kill all, start one."
    fi
fi

# ---------------------------------------------------------------------------
# Output — this goes to stdout and the agent sees it
# ---------------------------------------------------------------------------
cat <<CHECKLIST

=== SESSION START CHECKLIST (automated) ===
[1] ACP: ${ACP_STATUS}
[2] Swarm inbox: ${UNREAD_COUNT} unread message(s)
[3] Memory clone: ${MEM_STATUS}
[4] Monitors: ${MON_LINE}
${MON_ALERT}
=== MANUAL STEPS REQUIRED ===
[5] Read session-state.md: ${STATE_PATH}
[6] Search memory for current task context: memory search "<topic>"
[7] Review the current task and pending handoffs.
=== END CHECKLIST ===

CHECKLIST

hook_log_success "acp=${ACP_STATUS}" "unread=${UNREAD_COUNT}" "memory=${MEM_STATUS}"
