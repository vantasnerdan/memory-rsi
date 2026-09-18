#!/usr/bin/env python3
"""Background curation script for PreCompact and SubagentStop hooks.

Reads raw session context from a temp file, calls `claude -p --model haiku`
to determine if anything is worth remembering, and files curated entries
to agent-memory if valuable.

Called by hooks via:
    nohup python3 curate_context.py \
        --input /tmp/haiku-curation-XXXX.txt \
        --agent my-agent \
        --source precompact > /dev/null 2>&1 &

On ANY failure: logs to stderr, leaves temp file in place. No regression.

Issue: finml-sage/agent-memory#67
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("curate_context")
logger.addHandler(logging.StreamHandler(sys.stderr))
logger.setLevel(logging.INFO)

CLAUDE_CLI = Path.home() / ".local" / "bin" / "claude"
MEMORY_CLI = Path.home() / ".local" / "bin" / "memory"
LOCKFILE = Path("/tmp/haiku-curation.lock")
HAIKU_TIMEOUT_S = 60
MEMORY_BASE = os.environ.get(
    "MEMORY_BASE",
    os.environ.get("AGENT_MEMORY_PATH", "memory"),
)


CURATION_PROMPT = """\
You are a memory curation agent. Given session context \
from an AI agent's work, decide if anything is worth \
remembering in a FUTURE session.

Output a JSON object with these fields:
- valuable: boolean (true=worth remembering, false=not)
- description: one-line summary for search index, \
max 120 chars (empty string if not valuable)
- tags: comma-separated relevant tags \
(empty string if not valuable)
- body: curated memory content, 2-5 sentences max \
(empty string if not valuable)

NOT valuable (set valuable=false):
- Routine task completions (merged PR, posted issue)
- File modification lists (git has this)
- Error logs unless they reveal a reusable pattern
- Agent IDs, session metadata, timestamps
- Router decisions (which specialist to use)
- Routine deployments or config changes

VALUABLE (set valuable=true):
- Decisions with reasoning NOT obvious from code/git
- Corrections received from teammates
- Patterns discovered (reusable knowledge)
- Gotchas that would save time in future sessions
- Cross-agent insights
- Tool limitations discovered

Output ONLY the JSON object. No explanation, no preamble.

Session context:
"""

CURATION_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "valuable": {"type": "boolean"},
            "description": {"type": "string"},
            "tags": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["valuable", "description", "tags", "body"],
    }
)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Background curation via Haiku for agent-memory hooks",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to temp file with raw session context",
    )
    parser.add_argument(
        "--agent",
        required=True,
        help="Agent ID for memory authorship",
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=["precompact", "subagent"],
        help="Which hook spawned this curation",
    )
    parser.add_argument(
        "--agent-type",
        default="",
        help="Subagent type (only for --source subagent)",
    )
    return parser.parse_args()


def should_skip_filter(
    source: str,
    agent_type: str,
    raw_text: str,
) -> bool:
    """Pre-Haiku filter: skip trivially uninteresting transcripts."""
    if source == "subagent":
        if agent_type.lower() == "router":
            logger.info("Skipping router subagent (pre-filter)")
            return True
        line_count = len(raw_text.strip().splitlines())
        if line_count < 50:
            logger.info(
                "Skipping short subagent transcript (%d lines < 50)",
                line_count,
            )
            return True
    return False


def call_haiku(raw_text: str) -> str | None:
    """Call claude -p --model haiku with the curation prompt.

    Returns stdout on success, None on failure.
    """
    if not CLAUDE_CLI.exists():
        logger.error("claude CLI not found at %s", CLAUDE_CLI)
        return None

    prompt = CURATION_PROMPT + raw_text

    try:
        proc = subprocess.run(
            [
                str(CLAUDE_CLI),
                "-p",
                "--model",
                "haiku",
                "--output-format",
                "json",
                "--json-schema",
                CURATION_SCHEMA,
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=HAIKU_TIMEOUT_S,
        )
        if proc.returncode != 0:
            logger.error(
                "claude -p failed (exit %d): %s",
                proc.returncode,
                proc.stderr[:300],
            )
            return None

        output = proc.stdout.strip()
        if not output:
            logger.warning("claude -p returned empty output")
            return None

        return output

    except subprocess.TimeoutExpired:
        logger.error("claude -p timed out after %ds", HAIKU_TIMEOUT_S)
        return None
    except OSError as exc:
        logger.error("claude -p failed: %s", exc)
        return None


# Sentinel: distinguishes "Haiku said SKIP" from "Haiku returned unparseable"
HAIKU_SKIP = "SKIP"
HAIKU_PARSE_ERROR = "PARSE_ERROR"


def parse_haiku_response(response: str) -> dict | str:
    """Parse Haiku's response from --output-format json envelope.

    The claude CLI with --output-format json wraps the response in:
    {"type":"result", ..., "structured_output": {...}, "result": "..."}

    Returns:
        dict: Parsed curation data with description/tags/body fields
        HAIKU_SKIP: Haiku determined nothing is valuable
        HAIKU_PARSE_ERROR: Response was not parseable
    """
    cleaned = response.strip()

    # Parse the outer JSON envelope from claude --output-format json
    try:
        envelope = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Failed to parse claude JSON envelope: %.200s", cleaned)
        return HAIKU_PARSE_ERROR

    if not isinstance(envelope, dict):
        logger.warning("Claude response is not a JSON object")
        return HAIKU_PARSE_ERROR

    # Check for errors
    if envelope.get("is_error"):
        logger.warning("Claude returned error: %s", envelope.get("result", ""))
        return HAIKU_PARSE_ERROR

    # Extract structured output (from --json-schema)
    data = envelope.get("structured_output")
    if data is None:
        # Fallback: try the "result" field as raw text
        result_text = envelope.get("result", "").strip()
        if not result_text:
            logger.warning("No structured_output and empty result")
            return HAIKU_PARSE_ERROR
        # Try parsing result as JSON
        try:
            data = json.loads(result_text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Could not parse result as JSON: %.200s", result_text)
            return HAIKU_PARSE_ERROR

    if not isinstance(data, dict):
        logger.warning("Structured output is not a dict")
        return HAIKU_PARSE_ERROR

    # Check the valuable flag
    if not data.get("valuable", False):
        logger.info("Haiku determined content is not valuable")
        return HAIKU_SKIP

    # Validate required content fields
    for field in ("description", "tags", "body"):
        if not data.get(field):
            logger.warning("Haiku response missing content in field: %s", field)
            return HAIKU_PARSE_ERROR

    return data


def file_memory_entry(
    agent_id: str,
    source: str,
    agent_type: str,
    data: dict,
) -> bool:
    """File a curated memory entry via the memory CLI."""
    timestamp = int(time.time())
    if source == "subagent" and agent_type:
        safe_type = agent_type.replace(" ", "-").lower()
        entry_name = f"curated-subagent-{safe_type}-{timestamp}"
    else:
        entry_name = f"curated-{source}-{timestamp}"

    description = str(data["description"])[:120]
    tags = str(data["tags"])
    body = str(data["body"])

    env = os.environ.copy()
    env["AGENT_ID"] = agent_id

    cmd = [
        str(MEMORY_CLI),
        "new",
        entry_name,
        "-d",
        description,
        "-c",
        "atlas",
        "--confidence",
        "working",
        "-t",
        tags,
        "-b",
        body,
        "--no-git",
        "--base",
        MEMORY_BASE,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        if proc.returncode != 0:
            logger.error(
                "memory new failed (exit %d): %s",
                proc.returncode,
                proc.stderr[:300],
            )
            return False
        logger.info("Filed curated entry: %s", entry_name)
        return True
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.error("memory new failed: %s", exc)
        return False


def rebuild_cache(agent_id: str) -> None:
    """Rebuild the BM25 cache after filing an entry."""
    env = os.environ.copy()
    env["AGENT_ID"] = agent_id
    try:
        subprocess.run(
            [str(MEMORY_CLI), "cache", "build", "--base", MEMORY_BASE],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        logger.info("Cache rebuilt")
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Cache rebuild failed (non-fatal): %s", exc)


def main() -> None:
    """Main entry point for background curation."""
    args = parse_args()
    input_path = Path(args.input)

    if not input_path.exists():
        logger.error("Input file not found: %s", args.input)
        sys.exit(1)

    raw_text = input_path.read_text()
    if not raw_text.strip():
        logger.info("Input file is empty, cleaning up")
        input_path.unlink(missing_ok=True)
        return

    # Pre-Haiku filter
    if should_skip_filter(args.source, args.agent_type, raw_text):
        input_path.unlink(missing_ok=True)
        return

    # Acquire lockfile to prevent concurrent writes
    lock_fd = None
    try:
        lock_fd = open(LOCKFILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        logger.info("Acquired curation lock")

        # Call Haiku
        response = call_haiku(raw_text)
        if response is None:
            logger.warning("Haiku call failed; leaving temp file for manual review")
            return

        # Parse response
        result = parse_haiku_response(response)
        if result == HAIKU_SKIP:
            logger.info("Haiku says SKIP; deleting temp file")
            input_path.unlink(missing_ok=True)
            return
        if result == HAIKU_PARSE_ERROR:
            logger.warning(
                "Haiku returned unparseable response; "
                "leaving temp file for manual review"
            )
            return

        # File the curated entry
        if file_memory_entry(args.agent, args.source, args.agent_type, result):
            rebuild_cache(args.agent)
            input_path.unlink(missing_ok=True)
        else:
            logger.error("Failed to file entry; leaving temp file")

    except OSError as exc:
        logger.error("Lock/IO error: %s", exc)
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except OSError:
                pass


if __name__ == "__main__":
    main()
