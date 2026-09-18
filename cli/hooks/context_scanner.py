"""Context scanner utilities for the Context Assembler.

Handles searching agent-memory, scanning skill directories, and scanning
rule directories. Separated from context_assembler.py for SRP compliance.

Each function takes simple inputs, returns simple outputs, and handles
its own errors (never crashes).
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("context_assembler")

MEMORY_CLI = Path.home() / ".local" / "bin" / "memory"
MAX_MEMORY_RESULTS = 5
MAX_SKILL_SNIPPETS = 8
MAX_RULE_SNIPPETS = 8


def search_memory(queries: list[str], memory_base: str) -> list[dict]:
    """Search agent-memory with multiple queries, deduplicate results."""
    results: list[dict] = []
    seen_keys: set[str] = set()
    limit_per_query = max(1, MAX_MEMORY_RESULTS // len(queries)) if queries else 0

    for query in queries[:3]:
        try:
            cmd = [
                str(MEMORY_CLI), "--json-output", "search",
                query,
                "--limit", str(limit_per_query),
                "--base", memory_base,
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=4,
            )
            if proc.returncode != 0:
                continue
            items = json.loads(proc.stdout)
            if not isinstance(items, list):
                continue
            for item in items:
                key = f"{item.get('path', '')}>{item.get('section', '')}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    results.append(item)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
            logger.warning("Memory search failed for '%s': %s", query, exc)

    return results[:MAX_MEMORY_RESULTS]


def scan_skills(cwd: str, keywords: list[str]) -> list[dict]:
    """Scan .claude/skills/ directory and match by keyword relevance."""
    skills_dir = Path(cwd) / ".claude" / "skills"
    if not skills_dir.is_dir():
        return []

    matched: list[dict] = []
    kw_lower = [k.lower() for k in keywords]

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        name = skill_dir.name
        name_lower = name.lower()

        # Score: how many keywords appear in the skill name
        score = sum(1 for kw in kw_lower if kw in name_lower)

        # Also check first 5 lines of SKILL.md for description
        desc = ""
        try:
            lines = skill_md.read_text().splitlines()[:5]
            desc = " ".join(lines).strip()
            desc_lower = desc.lower()
            score += sum(0.5 for kw in kw_lower if kw in desc_lower)
        except OSError:
            pass

        if score > 0:
            matched.append({
                "name": name,
                "description": desc[:120],
                "score": score,
            })

    matched.sort(key=lambda x: x["score"], reverse=True)
    return matched[:MAX_SKILL_SNIPPETS]


def scan_rules(cwd: str, keywords: list[str]) -> list[dict]:
    """Scan .claude/rules/ directory and match by keyword relevance."""
    rules_dir = Path(cwd) / ".claude" / "rules"
    if not rules_dir.is_dir():
        return []

    matched: list[dict] = []
    kw_lower = [k.lower() for k in keywords]

    for rule_file in sorted(rules_dir.iterdir()):
        if not rule_file.is_file() or rule_file.suffix != ".md":
            continue

        name = rule_file.stem
        name_lower = name.lower()

        score = sum(1 for kw in kw_lower if kw in name_lower)

        desc = ""
        try:
            lines = rule_file.read_text().splitlines()[:5]
            desc = " ".join(line.strip() for line in lines if line.strip()).strip()
            desc_lower = desc.lower()
            score += sum(0.5 for kw in kw_lower if kw in desc_lower)
        except OSError:
            pass

        if score > 0:
            matched.append({
                "name": rule_file.name,
                "description": desc[:120],
                "score": score,
            })

    matched.sort(key=lambda x: x["score"], reverse=True)
    return matched[:MAX_RULE_SNIPPETS]


def assemble_context(
    task_domain: str,
    memory_results: list[dict],
    skills: list[dict],
    rules: list[dict],
) -> str:
    """Build the structured context string for additionalContext."""
    parts: list[str] = []
    parts.append("=== Context Assembler (Haiku-powered) ===")
    parts.append("")
    parts.append("## Task Analysis")
    parts.append(f"Domain: {task_domain}")
    parts.append("")

    if memory_results:
        parts.append("## Relevant Memory")
        for r in memory_results:
            path = r.get("path", "")
            section = r.get("section", "")
            desc = r.get("section_description", "")
            snippet = r.get("snippet", "")
            line = f"- {path}"
            if section:
                line += f" > {section}"
            if desc:
                line += f" -- {desc}"
            parts.append(line)
            if snippet:
                for sl in snippet.strip().splitlines()[:2]:
                    cleaned = sl.strip()
                    if cleaned:
                        parts.append(f"  {cleaned}")
        parts.append("")

    if skills:
        parts.append("## Recommended Skills")
        for s in skills:
            parts.append(f"- {s['name']}: {s['description'][:80]}")
        parts.append("")

    if rules:
        parts.append("## Applicable Rules")
        for r in rules:
            parts.append(f"- {r['name']}: {r['description'][:80]}")
        parts.append("")

    parts.append("=== End Context Assembler ===")
    return "\n".join(parts)
