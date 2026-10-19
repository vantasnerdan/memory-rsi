#!/usr/bin/env bash
# policy-context.sh -- SessionStart hook (run through run.sh, key "policy").
# Reads the editable memory policy with `memory policy` (action read) and
# prints it as SessionStart additionalContext JSON. Prints nothing on failure.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../cli/hooks" && pwd)"
source "${SCRIPT_DIR}/common.sh"
hook_log_init "SessionStart:policy"

if [[ -z "${MEMORY_BASE:-}" ]]; then
	hook_log_skip "MEMORY_BASE not set"
	exit 0
fi
if ! find_memory_cli; then
	hook_log_skip "memory CLI not found"
	exit 0
fi
if ! policy_json="$("$MEMORY_CMD" policy --request '{"action":"read"}' --base "$MEMORY_BASE" 2>/dev/null)"; then
	hook_log_error "memory policy read failed"
	exit 0
fi

printf '%s' "$policy_json" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
body = (data.get("body") or "").strip()
if not data.get("ok") or not body:
    sys.exit(0)
header = [
    "## Editable memory policy",
    "Read from the memory base with `memory policy` (revision {}{}). It supplements, and never overrides, higher-priority instructions, safety rules or permissions.".format(
        data.get("revision", "unknown"),
        "; truncated" if data.get("truncated") else "",
    ),
]
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n\n".join(header + [body])}}))
' && hook_log_success
exit 0
