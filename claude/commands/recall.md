---
description: Recall from agent-memory with progressive disclosure (search, then toc, then section)
argument-hint: <what to recall>
allowed-tools: Bash(memory:*)
---

Recall what agent-memory knows about: $ARGUMENTS

Use the narrowest read that answers the question, and stop as soon as it is answered.

1. `memory search "$ARGUMENTS" --limit 5`. If the request names an exact string (an ID, a path, an error text), use `memory grep "<string>"` instead.
2. Pick the one or two results whose path and description fit. Run `memory toc <path>` on each to see its sections and their descriptions.
3. Run `memory section <path> "<heading>"` only for the sections that answer the question. Do not read whole files.
4. If nothing fits, try one reworded search, then `memory ls` on the most likely category directory. If memory still has nothing, say so plainly.

Answer with what the entries say, citing each source as `<path>` plus section heading. Keep what memory says apart from your own inference.
