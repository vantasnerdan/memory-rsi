#!/usr/bin/env bash
# run.sh <key> <script> -- opt-in gate for the memory-rsi Claude Code hooks.
#
# Exits 0 with no output unless the plugin option hook_<key> is on
# (1|true|yes|on, any case) AND a memory base resolves:
#   memory_base option > MEMORY_BASE > AGENT_MEMORY_PATH (must be a directory).
# Then exports MEMORY_BASE (and AGENT_ID from the agent_id option when set)
# and runs <script> from claude/hooks/, else cli/hooks/, with stdin untouched.
set -uo pipefail

key="${1:-}"
script="${2:-}"
case "$key" in
	'' | *[!a-z_]*) echo "memory-rsi run.sh: invalid hook key '${key}'" >&2; exit 1 ;;
esac
case "$script" in
	'' | */* | *..*) echo "memory-rsi run.sh: invalid script name '${script}'" >&2; exit 1 ;;
esac

upper_key="$(printf '%s' "$key" | tr '[:lower:]' '[:upper:]')"
option_var="CLAUDE_PLUGIN_OPTION_HOOK_${upper_key}"
enabled="$(printf '%s' "${!option_var:-}" | tr '[:upper:]' '[:lower:]')"
case "$enabled" in
	1 | true | yes | on) ;;
	*) exit 0 ;;
esac

base="${CLAUDE_PLUGIN_OPTION_MEMORY_BASE:-${MEMORY_BASE:-${AGENT_MEMORY_PATH:-}}}"
base="${base/#\~/$HOME}"
if [[ -z "$base" || ! -d "$base" ]]; then
	SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../cli/hooks" 2>/dev/null && pwd)"
	if [[ -n "$SCRIPT_DIR" ]] && source "${SCRIPT_DIR}/common.sh" 2>/dev/null; then
		hook_log_init "claude-plugin:${key}"
		hook_log_skip "hook_${key} is on but no memory base directory resolved (memory_base option, MEMORY_BASE, AGENT_MEMORY_PATH)"
	fi
	exit 0
fi
export MEMORY_BASE="$base"
if [[ -n "${CLAUDE_PLUGIN_OPTION_AGENT_ID:-}" ]]; then
	export AGENT_ID="$CLAUDE_PLUGIN_OPTION_AGENT_ID"
fi

root="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
for dir in "${root}/claude/hooks" "${root}/cli/hooks"; do
	if [[ -f "${dir}/${script}" ]]; then
		exec bash "${dir}/${script}"
	fi
done
echo "memory-rsi run.sh: hook script '${script}' not found under ${root}" >&2
exit 1
