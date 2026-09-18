---
name: shared_agent-memory-usage
description: CLI commands, patterns, and integration guide for agent-memory
agent: shared
context: fork
---

# agent-memory CLI

Progressive disclosure memory management for autonomous agents. Git-backed markdown with YAML frontmatter. Structure IS the memory -- discovery over retrieval.

**Repository**: https://github.com/axis-marbell/agent-memory-cli
**Binary**: `memory` (installed via `pipx install git+ssh://git@github.com/axis-marbell/agent-memory-cli.git@v0.2.0`)

## Table of Contents

- [CLI Commands](#cli-commands)
- [Progressive Disclosure Pattern](#progressive-disclosure-pattern)
- [Frontmatter Schema](#frontmatter-schema)
- [Directory Structure](#directory-structure)
- [BM25 Search vs Ripgrep Grep](#bm25-search-vs-ripgrep-grep)
- [Agent Usage Patterns](#agent-usage-patterns)
- [Environment Variables](#environment-variables)
- [JSON Output Mode](#json-output-mode)
- [Lifecycle Hooks](#lifecycle-hooks)
- [Integration Gotchas](#integration-gotchas)
- [CLAUDE.md Integration](#claudemd-integration)

## CLI Commands

All commands accept `--json-output` as a global flag before the subcommand name.

### 1. init -- Create Agent Directory Structure

```bash
memory init <agent-id> --base memory
```

Creates standard subdirectories for a new agent:

```
memory/<agent-id>/
  atlas/        # Reference knowledge ("what I know")
  efforts/      # Project/task tracking ("what I'm working on")
  calendar/     # Time-based entries ("what happened when")
  moc/          # Maps of content (indices, navigation)
```

### 2. ls -- List Entries (Level 1)

```bash
memory ls <directory>
```

Lists `.md` files in a directory with frontmatter descriptions and confidence levels. Only reads frontmatter, not file bodies.

```
  swarm-messaging.md -- "How the swarm messaging protocol works" [established]
  deployment-guide.md -- "Step-by-step deployment for ASP nodes" [working]
```

If no `.md` files exist, lists subdirectories instead.

### 3. toc -- Table of Contents (Level 2)

```bash
memory toc <file>
```

Shows all `##` section headers with their description lines. Reads frontmatter + headers only, not full section content.

```
  Delivery Guarantees -- At-least-once delivery with idempotent message IDs.
  Acknowledgment Flow -- The receiver sends an ACK back through the swarm channel.
  Retry Strategy -- Exponential backoff with jitter, max 3 retries.
```

The `.md` extension is optional -- the tool resolves it automatically.

### 4. section -- Read Section Content (Level 3)

```bash
memory section <file> "<title>"
```

Extracts full content of a single section. Title matching is **case-insensitive partial match**, so `"delivery"` matches `"Delivery Guarantees"`.

If multiple sections match, the tool asks you to be more specific. If none match, it lists available sections.

### 5. validate -- Check Entry Format

```bash
# Single file
memory validate <file>

# Entire directory (recursive)
memory validate memory/
```

Checks frontmatter schema compliance (required fields, enum values, section description format). Reports passes, errors, and warnings per file. Exit code 1 if any errors found.

**Note**: `validate` takes a positional `PATH` argument (file or directory), NOT a `--base` flag. This is different from most other commands. Running `memory validate --base memory` will fail -- use `memory validate memory/` instead.

### 6. search -- BM25 Relevance-Ranked Search

```bash
memory search "<query>" --base memory [options]
```

Section-level BM25 search. Each `##` section is a document. Returns ranked results with scores and best-passage snippets.

| Option | Default | Description |
|--------|---------|-------------|
| `--scope` | `all` | `all`, `own`, `shared`, `agent:<id>` |
| `--field` | `content` | Rank by `description`, `tags`, or `content` |
| `--category` | - | Filter: `atlas`, `efforts`, `calendar`, `moc` |
| `--confidence` | - | Filter: `established`, `working`, `exploratory` |
| `--author` | - | Filter by author agent ID |
| `--status` | - | Filter: `active`, `archived`, `draft` |
| `--tag` | - | Filter by tag |
| `--limit` | 10 | Max results |
| `--no-cache` | - | Bypass SQLite cache, read files directly |
| `--base` | `memory` | Base directory |

```
[1] sage/atlas/swarm-messaging.md > Delivery Guarantees  (score: 2.41)
    At-least-once delivery with idempotent message IDs...
```

### 7. new -- Create Memory Entry

```bash
AGENT_ID=my-agent memory new <name> \
  -d "Description for frontmatter" \
  -b "## Section Title\nSection content." \
  -c atlas \
  --confidence working \
  -t "tag1,tag2" \
  --base memory
```

Creates `memory/<agent-id>/<category>/<kebab-name>.md` with auto-generated frontmatter (`created`, `updated` set to current UTC). Then runs `git add`, `commit`, `pull --rebase`, `push`.

| Option | Description |
|--------|-------------|
| `-d` / `--description` | **Required.** Frontmatter description. |
| `-b` / `--body` | Body content. Use `-` to read from stdin. |
| `-c` / `--category` | `atlas`, `efforts`, `calendar`, `moc`, or empty |
| `--confidence` | `established`, `working`, `exploratory` |
| `-t` / `--tags` | Comma-separated tags |
| `--status` | `active` (default), `archived`, `draft` |
| `--shared` | Write to `memory/shared/` instead of agent dir |
| `--no-git` | Skip git commit/push |
| `--author` | Override AGENT_ID env var |
| `--base` | Base directory (default: `AGENT_MEMORY_PATH` or `memory`) |

### 8. update -- Update Existing Entry

```bash
memory update <file> [options]
```

Updates an existing entry's frontmatter fields and/or body. Auto-bumps the `updated` timestamp. Preserves `created` and `author`.

| Option | Description |
|--------|-------------|
| `-b` / `--body` | Replace body content. Use `-` for stdin. |
| `-t` / `--tags` | Replace all tags |
| `--add-tags` | Append tags (no duplicates) |
| `--confidence` | Update confidence level |
| `--status` | Update status |
| `--no-git` | Skip git commit/push |
| `--base` | Base directory for git repo root detection |

### 9. clone -- Clone Memory Repository

```bash
AGENT_MEMORY_REPO=https://github.com/org/memory.git \
AGENT_MEMORY_PATH=~/.agent-memory \
memory clone
```

Clones the memory repository. Skips if already cloned with the same remote. Requires `AGENT_MEMORY_REPO` and `AGENT_MEMORY_PATH` environment variables.

### 10. sync -- Sync Local Memory with Remote

```bash
AGENT_ID=my-agent AGENT_MEMORY_PATH=~/.agent-memory memory sync
```

Full sync workflow: commits uncommitted local changes, pulls (merge strategy), pushes, reports changed files with frontmatter descriptions, runs non-blocking validation on changed files.

| Option | Description |
|--------|-------------|
| `--pull-only` | Only pull, skip push |
| `--push-only` | Only push, skip pull |

### 11. grep -- Ripgrep-Based Search

```bash
memory grep "<pattern>" --base memory [options]
```

Shells out to `rg` for exact/regex pattern matching. Results include frontmatter metadata for each matched file.

| Option | Description |
|--------|-------------|
| `--scope` | `all`, `own`, `shared`, `agent:<id>` |
| `-C` / `--context` | Context lines around matches |
| `-A` / `--after-context` | Lines after each match |
| `-B` / `--before-context` | Lines before each match |
| `-i` / `--ignore-case` | Case-insensitive |
| `-F` / `--fixed-strings` | Literal match (not regex) |
| `--tag` | Find entries with specific frontmatter tag |
| `--links-to` | Find entries referencing `[[name]]` wiki-links |
| `--base` | Base directory for memory entries (default: `memory`) |

Specialized modes:

```bash
memory grep --tag deployment               # Find entries tagged "deployment"
memory grep --links-to swarm-architecture   # Find wiki-link references
memory grep "shift(1)" --scope own          # Regex search in own entries
```

### 12. cache -- Manage SQLite Index Cache

```bash
memory cache build --base memory    # Build or refresh index cache
memory cache status --base memory   # Show cache statistics
memory cache clear --base memory    # Delete the cache database file
```

The SQLite index cache accelerates BM25 search at scale (designed for 100k files). Uses WAL mode, `mtime_ns` invalidation, and lazy DataFrame rebuild.

| Subcommand | Description |
|------------|-------------|
| `build` | Scans all `.md` files, parses frontmatter and `##` sections, writes to SQLite. Use `--verify-hash` for SHA-256 content hash staleness detection (slower, more accurate). |
| `status` | Shows file count, section count, term count, cache path, and last build time. |
| `clear` | Deletes the cache database file entirely. |

All subcommands accept `--base TEXT` (default: `memory`). Cache path is controlled by the `AGENT_MEMORY_CACHE_PATH` environment variable.

## Progressive Disclosure Pattern

The core design principle: never load an entire file to decide if it contains what you need.

| Level | Command | What You See | What Gets Loaded |
|-------|---------|-------------|------------------|
| 0 | `memory init` | Directory structure | Nothing (creates dirs) |
| 1 | `memory ls <dir>` | File names + descriptions | Frontmatter only |
| 2 | `memory toc <file>` | Section headers + descriptions | Frontmatter + headers |
| 3 | `memory section <file> "title"` | Full section content | One section |

**Level 1** tells you which file to look at. **Level 2** tells you which section to read. **Level 3** gives you the content. Each level is independently useful.

## Frontmatter Schema

Every memory entry starts with YAML frontmatter between `---` delimiters.

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `description` | string | One-line summary (~120 chars) |
| `author` | string | Agent ID that created the entry |
| `created` | datetime | ISO 8601 creation timestamp (auto-set by `new`) |
| `updated` | datetime | ISO 8601 last update timestamp (auto-bumped by `update`) |

### Optional Fields

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `tags` | list[str] | any | Categorization tags |
| `confidence` | enum | `established`, `working`, `exploratory` | Knowledge maturity |
| `category` | enum | `atlas`, `efforts`, `calendar`, `moc` | Entry type |
| `status` | enum | `active`, `archived`, `draft` | Lifecycle state |
| `related` | list[str] | `[[wiki-links]]` | Cross-references |
| `supersedes` | string | `[[wiki-link]]` | Entry this replaces |

### Confidence Levels

| Level | Meaning | When to Use |
|-------|---------|-------------|
| `established` | Verified, stable knowledge | After validation or long use |
| `working` | Believed correct, not fully verified | Default for most new entries |
| `exploratory` | Hypothesis or early research | For speculative content |

### Section Description Convention

Every `##` section header must have a plain prose description as its first non-empty line. This enables Level 2 disclosure (toc) to show meaningful summaries.

```markdown
## Delivery Guarantees
At-least-once delivery with idempotent message IDs.    <-- valid

## Bad Example
- This starts with a list marker                       <-- INVALID
```

Valid descriptions: non-empty, do not start with `#`, backtick, `|`, `-`, `*`, or whitespace followed by `-` (indented lists). Run `memory validate <file>` as the authoritative check -- the validator is stricter than this summary.

### Example Entry

```markdown
---
description: How the swarm messaging protocol handles delivery
author: my-agent
created: 2026-02-12T10:00:00Z
updated: 2026-02-12T14:30:00Z
tags: [protocol, messaging, a2a]
related: ["[[swarm-architecture]]", "[[message-queue-design]]"]
confidence: established
category: atlas
status: active
---
# Swarm Messaging Protocol

## Delivery Guarantees
At-least-once delivery with idempotent message IDs.

Each message carries a UUID that the receiver tracks.
Duplicate deliveries are detected and silently dropped.

## Acknowledgment Flow
The receiver sends an ACK message back through the same channel.
```

## Directory Structure

```
memory/
  {agent-id}/           # Per-agent scope (only this agent writes here)
    atlas/              # Reference knowledge
    efforts/            # Projects and tasks
    calendar/           # Time-based entries
    moc/                # Maps of content (indices)
  shared/               # Cross-agent knowledge (any agent can write)
```

### Categories

| Category | Purpose | Examples |
|----------|---------|---------|
| `atlas` | Stable reference knowledge | API docs, architecture, tool guides |
| `efforts` | Active projects and tasks | Sprint goals, implementation plans |
| `calendar` | Time-bound events and logs | Session summaries, incident reports |
| `moc` | Maps of content (indices) | Topic indices, cross-references |

### Agent Scoping

Agents write only to their own directory and `shared/`. The `--scope` flag on search/grep controls where to look:

- `--scope own` -- only `memory/{AGENT_ID}/`
- `--scope shared` -- only `memory/shared/`
- `--scope agent:other-agent` -- only `memory/other-agent/`
- `--scope all` -- everything under `memory/`

## BM25 Search vs Ripgrep Grep

Two search tools serve different purposes. Use the right one for the task.

### When to Use `search` (BM25)

BM25 is a **relevance ranking** algorithm. It answers: "which sections are most relevant to this topic?"

- Natural language queries: `memory search "deployment best practices"`
- Topic exploration: "What do we know about X?"
- When you want ranked results by relevance
- When you want section-level granularity with snippets
- When you want to filter by frontmatter fields (category, confidence, author, tags)

BM25 is pure Python -- no external dependencies.

### When to Use `grep` (ripgrep)

Ripgrep is an **exact/regex pattern matcher**. It answers: "where does this exact text or pattern appear?"

- Exact string search: `memory grep "AGENT_MEMORY_PATH"`
- Regex patterns: `memory grep "error\s+code:\s+\d+"`
- Finding wiki-link references: `memory grep --links-to swarm-architecture`
- Finding entries by tag: `memory grep --tag deployment`
- When you need line numbers and surrounding context
- When you want file-level results with frontmatter metadata

Ripgrep requires `rg` to be installed on the system.

### Decision Matrix

| Need | Use |
|------|-----|
| "What do we know about deployment?" | `search "deployment"` |
| "Where is AGENT_MEMORY_PATH referenced?" | `grep "AGENT_MEMORY_PATH"` |
| "Find the most relevant API documentation" | `search "API documentation" --category atlas` |
| "Which entries link to swarm-architecture?" | `grep --links-to swarm-architecture` |
| "Find entries about error handling, ranked" | `search "error handling"` |
| "Find the exact string 'exit code 1'" | `grep -F "exit code 1"` |

## Agent Usage Patterns

### Cold-Start Context Loading

When an agent starts a task and needs to load relevant context from memory. This is the most important pattern -- it prevents redundant research.

```bash
# Step 1: Orient -- what's available?
memory ls memory/my-agent/atlas
memory ls memory/shared

# Step 2: Search for task-relevant knowledge
memory search "swarm messaging protocol" --base memory --limit 5

# Step 3: Drill into top results
memory toc memory/my-agent/atlas/swarm-messaging.md
memory section memory/my-agent/atlas/swarm-messaging.md "delivery"
```

### Saving Findings After Research

After completing research, persist findings so future sessions and other agents benefit.

```bash
AGENT_ID=my-agent memory new api-rate-limits \
  -d "Rate limit behavior and retry strategies for Moltbook API" \
  -c atlas --confidence working -t "moltbook,api,rate-limiting" \
  -b "## Rate Limits
The Moltbook API enforces 60 requests/minute per agent.

## Retry Strategy
Exponential backoff with jitter. Start at 1s, max 30s." \
  --base memory
```

### Searching Before Starting Work

Before beginning any task, check if the knowledge already exists.

```bash
memory search "moltbook API authentication" --base memory
memory grep "moltbook.*auth" --base memory -i        # exact/regex fallback
memory search "authentication" --scope agent:other-agent --base memory  # check other agents
```

### Updating Knowledge After Discovery

```bash
memory update memory/my-agent/atlas/swarm-messaging.md --add-tags "a2a,wake-protocol"
memory update memory/my-agent/atlas/swarm-messaging.md --confidence established
memory update memory/my-agent/atlas/swarm-messaging.md -b "## Updated Content\nNew body here."
```

### Piping Content from Stdin

For long-form content, use `-b -` to read body from stdin:

```bash
cat <<'EOF' | AGENT_ID=my-agent memory new detailed-analysis \
  -d "Analysis of agent communication patterns" \
  -c atlas --confidence working -b - --base memory
## Communication Patterns
Agents use three primary patterns...
EOF
```

## Environment Variables

| Variable | Required For | Description |
|----------|-------------|-------------|
| `AGENT_ID` | `new`, `search --scope own`, `sync`, `grep --scope own` | Agent identity string |
| `AGENT_MEMORY_REPO` | `clone` | Git remote URL for memory repo |
| `AGENT_MEMORY_PATH` | `clone`, `sync`; optional for `new` | Local clone path (default fallback: `memory`) |
| `AGENT_MEMORY_CACHE_PATH` | `cache`, `search` (optional) | SQLite cache file location. Defaults to `{base}/.agent-memory-cache/index.db` (per `resolve_cache_path` in `cache.py`) |
| `MEMORY_BASE` | hooks (optional) | Override for the `--base` flag in hook scripts. Resolved by `common.sh` as: `MEMORY_BASE` > `AGENT_MEMORY_PATH` > `${REPO_DIR}/memory`. Set this when the memory directory is not at the default location relative to the repo root. |

## JSON Output Mode

All commands support `--json-output` for machine-readable output. Place the flag **before** the subcommand name:

```bash
memory --json-output ls memory/my-agent/atlas
memory --json-output search "deployment" --base memory
memory --json-output toc memory/my-agent/atlas/swarm-messaging.md
memory --json-output validate memory/
```

JSON mode is useful for agents that need to parse results programmatically rather than reading human-formatted text.

## Lifecycle Hooks

Four shell scripts in the `hooks/` directory integrate agent-memory with Claude Code lifecycle events. A shared `common.sh` provides the `find_memory_cli` function and `REPO_DIR` resolution.

### Hook Scripts

| Script | Claude Code Event | Purpose | Output |
|--------|-------------------|---------|--------|
| `pre-compact-save.sh` | PreCompact | Parses session transcript, saves key context as a memory entry before compaction | stderr only (stdout not injected by PreCompact) |
| `post-compact-restore.sh` | SessionStart | Queries memory for recent entries, outputs lean context summary | stdout (injected into fresh context window) |
| `subagent-start-load.sh` | SubagentStart | Loads relevant institutional memory for the spawning subagent | JSON stdout with `additionalContext` field |
| `subagent-stop-save.sh` | SubagentStop | Parses subagent transcript, saves learnings as a memory entry | stderr only (SubagentStop does not inject context) |

### Hook Configuration

Hooks are configured in `.claude/settings.json`:

```json
{
  "hooks": {
    "PreCompact": [{ "hooks": [{ "type": "command", "command": "/path/to/hooks/pre-compact-save.sh", "timeout": 30000 }] }],
    "SessionStart": [{ "matcher": "compact", "hooks": [{ "type": "command", "command": "/path/to/hooks/post-compact-restore.sh", "timeout": 15000 }] }],
    "SubagentStart": [{ "hooks": [{ "type": "command", "command": "/path/to/hooks/subagent-start-load.sh", "timeout": 15000 }] }],
    "SubagentStop": [{ "hooks": [{ "type": "command", "command": "/path/to/hooks/subagent-stop-save.sh", "timeout": 15000 }] }]
  }
}
```

**SessionStart `matcher` field**: The `"matcher": "compact"` is critical -- it ensures the hook only fires after compaction (context window reset), not on every fresh session start. Without it, agents receive memory context injection on cold starts where it is unnecessary and wastes context budget. Other hooks use `"matcher": ""` (empty string) to fire unconditionally.

### Required Environment Variables for Hooks

All hooks require `AGENT_ID` and optionally use `AGENT_MEMORY_PATH` and `MEMORY_BASE`. These must be exported in `~/.bashrc` **above** the `PS1` guard line (see Integration Gotchas below).

### Hook Behavior

- All hooks exit 0 unconditionally (never break the lifecycle event, even on error).
- `pre-compact-save.sh` and `subagent-stop-save.sh` process the last 200 lines of transcript.
- `post-compact-restore.sh` targets < 2K tokens and has an 8-second timeout.
- `subagent-start-load.sh` targets < 5 seconds.
- `common.sh` searches `~/.local/bin/memory`, venv paths, and `$PATH` for the CLI binary.

## Integration Gotchas

Lessons from deploying agent-memory across multiple agent environments.

### PS1 Guard Blocks Env Vars in Non-Interactive Shells

Hook scripts run in non-interactive shell contexts. The default `~/.bashrc` contains a guard:

```bash
[ -z "$PS1" ] && return
```

Any `export` lines below this guard are **never evaluated** by hooks. Place all agent-memory env vars (`AGENT_ID`, `AGENT_MEMORY_PATH`, `AGENT_MEMORY_CACHE_PATH`) **above** this line.

### BM25 Search Requires Section Content

Frontmatter-only entries (no body content) return zero results from `memory search`. BM25 indexes `##` section bodies. Entries must have at least one `##` section with prose content to be searchable.

### Section Description Validation is Strict

The first non-empty line after a `##` header must be plain prose. Starting with a list item (`-`, `*`), code fence, table (`|`), heading (`#`), backtick, or indented list (whitespace + `-`) will fail validation.

### Use Heredoc for Hook Testing

When testing hooks manually by piping JSON on stdin, use heredoc syntax (`<<'EOF'`), not echo pipes. Shell quoting in echo pipes corrupts JSON special characters.

```bash
# Correct
/path/to/hooks/pre-compact-save.sh <<'EOF'
{"session_id": "test", "transcript_path": "/dev/null", "cwd": "/tmp", "hook_event_name": "PreCompact"}
EOF

# Incorrect (quoting issues)
echo '{"session_id": "test"}' | /path/to/hooks/pre-compact-save.sh
```

### Pull Strategy Varies by Command

The `new` and `update` commands use `git pull --rebase` before push (via `git_commit_and_push`). The `sync` command uses `git_pull_merge` (merge strategy, `pull --no-rebase`). This distinction matters: rebase produces a linear history for single-entry writes, while merge is safer for the broader sync workflow where multiple files may have diverged.

### Reinstall CLI After Source Updates

After pulling new CLI source code (`git pull` on the agent-memory repo), the pipx-installed `memory` binary is stale until reinstalled. Run:

```bash
pipx install --force /path/to/agent-memory
```

Symptoms of a stale CLI: missing subcommands, unexpected validation errors, unrecognized flags. This affects any installation method that caches the build (pipx, pip install from local path). Always reinstall after updating the source.

### Use --no-git for Concurrent Subagent Writes

When multiple subagents write to the same memory repo concurrently, use `--no-git` on all `memory new` and `memory update` calls. Without this flag, each command runs `git add`, `commit`, and `push` individually, causing merge conflicts when two subagents write at the same time.

**Pattern**: Subagents use `--no-git` for all writes. Git operations happen at a single coordinated point -- either via `memory sync` at the orchestrator level, or automatically through the lifecycle hooks (`pre-compact-save.sh` and `subagent-stop-save.sh` both use `--no-git` internally and defer git sync to `memory sync`).

```bash
# Subagent write (no git)
AGENT_ID=sage memory new finding -d "Description" -c atlas -b "## Content" --no-git --base memory

# Orchestrator sync (single git operation)
AGENT_ID=sage memory sync
```

## CLAUDE.md Integration

To integrate agent-memory into sub-agent workflows, add a **Context Loading** step to each agent's CLAUDE.md or agent definition. This goes at the beginning of the workflow, before specialist work starts.

### Recommended CLAUDE.md Addition

Add this block to each agent's CLAUDE.md or agent definition file:

```markdown
## Context Loading (Before Specialist Work)

Before starting any task, check agent-memory for existing knowledge:
1. Search: `memory search "<task-relevant query>" --base memory --limit 5`
2. If results found, drill in: `memory toc <file>` then `memory section <file> "<title>"`
3. If no results, proceed with fresh research -- but save findings afterward
4. After work: `AGENT_ID=<id> memory new <name> -d "<desc>" -c atlas -b "<body>" --base memory`
```

### Agent Definition Skill Reference

Add `shared_agent-memory-usage` to each agent's `skills:` line in their `.md` definition so they can reference CLI syntax and patterns.

### Workflow Integration Point

Context loading fits as **Step 0**, before any specialist work:

```
0. Context Loading (agent-memory) -- search before researching
1. Read your skills
2. Understand the task
3. Execute the work
4. Save findings to memory (if new knowledge produced)
5. Update your skills
```

This prevents re-researching topics already documented in prior sessions.
