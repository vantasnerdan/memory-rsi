#!/usr/bin/env python3
"""Stop hook: nudge when a session has zero self-optimization actions.

Scans the session transcript for skill updates, rule changes, or memory
saves. If none found, injects a reminder. Skips when stop_hook_active
is true (prevents infinite re-trigger loops).
"""
import json
import sys

NUDGE = (
    "\n\U0001f504 Composition Gap Check: This session has not yet updated "
    "skills, optimized rules, or saved to memory. Before continuing, consider:\n"
    "- Did you learn something worth saving to a skill?\n"
    "- Should a rule be added or refined?\n"
    "- Is there context worth filing to agent-memory?\n"
    "- Did you discover a better method worth documenting?\n"
    "If this session was purely mechanical (config, ops, simple fix), "
    "this nudge can be safely ignored.\n"
)
SKILL_PATH = ".claude/skills/"
RULE_PATH = ".claude/rules/"
MEMORY_CMDS = ("memory new", "memory update")
WRITE_TOOLS = ("Write", "Edit")
MAX_LINES = 200
PASS = "{}"


def _check_block(block: dict) -> bool:
    """Return True if a tool_use block is a self-optimization action."""
    if not isinstance(block, dict) or block.get("type") != "tool_use":
        return False
    name = block.get("name", "")
    inp = block.get("input", {})
    if not isinstance(inp, dict):
        return False
    if name in WRITE_TOOLS:
        fp = inp.get("file_path", "")
        if SKILL_PATH in fp or RULE_PATH in fp:
            return True
    if name == "Bash":
        cmd = inp.get("command", "")
        return any(mc in cmd for mc in MEMORY_CMDS)
    return False


def has_self_optimization(transcript_path: str) -> bool:
    """Read transcript JSONL tail and check for self-optimization tool calls."""
    try:
        with open(transcript_path, "r") as f:
            lines = f.readlines()[-MAX_LINES:]
    except (OSError, IOError):
        return True  # Unreadable transcript -- don't nudge, don't crash

    for raw_line in lines:
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if _check_block(block):
                return True
    return False


def main() -> None:
    try:
        raw = sys.stdin.read().strip()
        hook_input = json.loads(raw) if raw else {}
    except Exception:
        print(PASS)
        return
    # Prevent infinite loops: second Stop after our nudge has this flag set
    if hook_input.get("stop_hook_active", False):
        print(PASS)
        return
    tp = hook_input.get("transcript_path", "")
    if not tp or has_self_optimization(tp):
        print(PASS)
    else:
        print(json.dumps({"decision": "block", "reason": NUDGE}))


if __name__ == "__main__":
    main()
