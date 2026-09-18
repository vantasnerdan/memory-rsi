#!/usr/bin/env python3
"""Context Assembler -- Claude-powered intelligent context injection.

Uses `claude -p` with tool access to search agent-memory, read skills
and rules, and synthesize a focused briefing for subagent injection.

Called from subagent-start-load.sh:
    python3 hooks/context_assembler.py \\
        --transcript-path PATH --agent-type TYPE \\
        --agent-id ID --cwd DIR

Output: JSON to stdout ({"context": "...", "sources": [...], "model": "..."}).
Errors: stderr only. Never crashes -- returns partial results.

Issue: finml-sage/agent-memory#66
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("context_assembler")
logger.addHandler(logging.StreamHandler(sys.stderr))
logger.setLevel(logging.INFO)

TOTAL_TIMEOUT_S = 45.0
MAX_PROMPT_CHARS = 4000
CLAUDE_CLI = Path.home() / ".local" / "bin" / "claude"

# Models to try in order. Haiku first (fast/cheap), sonnet as fallback.
MODELS = ["haiku", "sonnet"]

# Tools the assembler agent is allowed to use.
# Bash restricted to memory CLI commands. Read/Grep for skill/rule files.
ALLOWED_TOOLS = "Bash(memory search:*),Bash(memory ls:*),Read,Grep"

# Import scanner utilities for regex fallback path
_HOOKS_DIR = Path(__file__).resolve().parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from context_scanner import (  # noqa: E402
    assemble_context,
    scan_rules,
    scan_skills,
    search_memory,
)


def extract_prompt(transcript_path: str) -> str | None:
    """Extract task prompt from the PARENT transcript's last Agent tool call.

    The SubagentStart hook fires BEFORE the subagent's own transcript is
    created, so we read the parent transcript and find the last Agent
    tool_use block to get the prompt that was passed to the subagent.
    """
    try:
        path = Path(transcript_path)
        if not path.exists():
            logger.warning("Transcript not found: %s", transcript_path)
            return None

        # Read last 200 lines of parent transcript (Agent call is near the end)
        lines: list[str] = []
        with open(path) as f:
            for line in f:
                lines.append(line)
                if len(lines) > 200:
                    lines.pop(0)

        # Find the LAST Agent tool call
        last_prompt = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("type") != "assistant":
                continue

            content = entry.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue

            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "Agent"
                ):
                    prompt = block.get("input", {}).get("prompt", "")
                    if prompt:
                        last_prompt = prompt

        if last_prompt:
            return last_prompt[:MAX_PROMPT_CHARS]

        logger.warning("No Agent tool call found in parent transcript")
        return None

    except (OSError, KeyError) as exc:
        logger.warning("Failed to extract prompt: %s", exc)
        return None


def _build_system_prompt(agent_type: str, cwd: str) -> str:
    """Build the system prompt for the assembler agent."""
    memory_base = os.environ.get(
        "AGENT_MEMORY_PATH",
        os.environ.get("MEMORY_BASE", "memory"),
    )
    return (
        "You are a context assembler. Find relevant context for a "
        f"{agent_type} subagent. Be fast — you have 20 seconds total.\n\n"
        "STEP 1 — Extract 3-5 search keywords from the task description.\n\n"
        "STEP 2 — Search and surface context using these tools:\n"
        f"  memory search \"<keyword>\" --base {memory_base} --limit 3\n"
        f"  memory ls {memory_base}\n"
        f"  Read files: {cwd}/.claude/skills/*/SKILL.md for relevant skills\n"
        f"  Grep: search {cwd}/.claude/rules/ for relevant rule content\n"
        "Run 2-3 memory searches max. Read 2-3 skill/rule files max.\n\n"
        "STEP 3 — Synthesize a briefing (under 2000 chars) with:\n"
        "- Prior work and decisions from memory\n"
        "- Skills the subagent should read (by name)\n"
        "- Rules that apply (by filename)\n"
        "- Key context for the task\n\n"
        "Output ONLY the briefing. No preamble, no wrapper."
    )


def call_claude_assembler(
    prompt_text: str,
    agent_type: str,
    cwd: str,
) -> tuple[str | None, str | None]:
    """Call claude CLI with tool access to search and synthesize context.

    Tries haiku first, falls back to sonnet if haiku fails.
    Returns (context_string, model_used) or (None, None) on failure.
    """
    if not CLAUDE_CLI.exists():
        logger.warning("claude CLI not found at %s", CLAUDE_CLI)
        return None, None

    system_prompt = _build_system_prompt(agent_type, cwd)

    user_prompt = (
        f"Find relevant context for this {agent_type} task:\n"
        f"---\n{prompt_text[:2000]}\n---"
    )

    for model in MODELS:
        try:
            proc = subprocess.run(
                [
                    str(CLAUDE_CLI), "-p",
                    "--model", model,
                    "--allowedTools", ALLOWED_TOOLS,
                    "--system-prompt", system_prompt,
                    "--max-budget-usd", "0.25",
                    user_prompt,
                ],
                capture_output=True, text=True,
                timeout=TOTAL_TIMEOUT_S,
            )
            if proc.returncode != 0:
                logger.warning(
                    "claude -p (model=%s) failed (exit %d): %s",
                    model, proc.returncode, proc.stderr[:200],
                )
                continue

            output = proc.stdout.strip()
            if not output:
                logger.warning("claude -p (model=%s) returned empty output", model)
                continue

            logger.info("claude -p (model=%s) returned %d bytes", model, len(output))
            return output, model

        except subprocess.TimeoutExpired:
            logger.warning("claude -p (model=%s) timed out after %ds", model, TOTAL_TIMEOUT_S)
            continue
        except OSError as exc:
            logger.warning("claude -p (model=%s) failed: %s", model, exc)
            continue

    return None, None


def fallback_keywords(prompt_text: str) -> dict:
    """Extract keywords from prompt without Claude (regex heuristic).

    Used when claude -p fails or times out.
    """
    words = re.findall(r"[a-z][a-z0-9_-]+", prompt_text.lower())
    stopwords = {
        "the", "and", "for", "that", "this", "with", "from", "your",
        "have", "will", "are", "was", "been", "being", "were", "not",
        "but", "what", "when", "how", "which", "where", "who", "can",
        "should", "would", "could", "into", "about", "after", "before",
        "each", "every", "some", "any", "all", "most", "more", "also",
        "then", "than", "just", "only", "very", "too", "use", "using",
    }
    filtered = [w for w in words if w not in stopwords and len(w) > 2]

    freq: dict[str, int] = {}
    for w in filtered:
        freq[w] = freq.get(w, 0) + 1
    top = sorted(freq, key=freq.get, reverse=True)[:5]  # type: ignore[arg-type]

    return {
        "search_queries": top[:3] if top else ["context"],
        "relevant_skill_keywords": top[:3],
        "relevant_rule_keywords": top[:2],
        "task_domain": "general",
    }


def run(
    transcript_path: str,
    agent_type: str,
    agent_id: str,
    cwd: str,
) -> dict:
    """Main entry point. Returns dict with context, sources, model."""
    start = time.monotonic()

    prompt_text = extract_prompt(transcript_path)
    if not prompt_text:
        logger.info("No prompt extracted; returning empty context")
        return {"context": "", "sources": [], "model": None}

    # Primary path: claude -p with tool access (search + synthesize)
    context, model = call_claude_assembler(prompt_text, agent_type, cwd)
    if context:
        elapsed = time.monotonic() - start
        logger.info("Assembled via %s: %d bytes (%.1fs)", model, len(context), elapsed)
        return {
            "context": f"=== Context Assembler ({model}) ===\n\n{context}\n\n=== End Context Assembler ===",
            "sources": ["claude-cli", "agent-memory", "skills", "rules"],
            "model": model,
        }

    # Fallback path: regex keywords + mechanical assembly
    logger.info("claude -p failed; falling back to regex + scanner")
    analysis = fallback_keywords(prompt_text)
    sources: list[str] = ["fallback_keywords"]

    queries = analysis.get("search_queries", [])
    skill_kw = analysis.get("relevant_skill_keywords", [])
    rule_kw = analysis.get("relevant_rule_keywords", [])
    task_domain = analysis.get("task_domain", "general")

    memory_base = os.environ.get(
        "AGENT_MEMORY_PATH",
        os.environ.get("MEMORY_BASE", "memory"),
    )
    memory_results = search_memory(queries, memory_base)
    if memory_results:
        sources.append("agent-memory")

    skills = scan_skills(cwd, skill_kw)
    if skills:
        sources.append("skills")

    rules = scan_rules(cwd, rule_kw)
    if rules:
        sources.append("rules")

    context = assemble_context(task_domain, memory_results, skills, rules)

    elapsed = time.monotonic() - start
    logger.info("Assembled via fallback: %d bytes (%.1fs)", len(context), elapsed)

    return {
        "context": f"=== Context Assembler (fallback) ===\n\n{context}\n\n=== End Context Assembler ===",
        "sources": sources,
        "model": None,
    }


def main() -> None:
    """CLI entry point -- parse args, run, output JSON to stdout."""
    parser = argparse.ArgumentParser(
        description="Context Assembler for SubagentStart hook",
    )
    parser.add_argument(
        "--transcript-path", required=True,
        help="Path to parent transcript JSONL file",
    )
    parser.add_argument(
        "--agent-type", required=True,
        help="Subagent type (e.g., memory_agent, github_agent)",
    )
    parser.add_argument(
        "--agent-id", required=True,
        help="Subagent ID from hook input",
    )
    parser.add_argument(
        "--cwd", required=True,
        help="Working directory for skill/rule scanning",
    )
    args = parser.parse_args()

    try:
        result = run(
            transcript_path=args.transcript_path,
            agent_type=args.agent_type,
            agent_id=args.agent_id,
            cwd=args.cwd,
        )
    except Exception as exc:
        logger.error("Unhandled error: %s", exc)
        result = {"context": "", "sources": [], "model": None}

    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
