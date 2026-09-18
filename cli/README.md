# agent-memory 0.2

Progressive disclosure memory management for autonomous agents. Git-backed markdown storage with YAML frontmatter, designed so that structure IS the memory -- discovery over retrieval.

This repository is the code-only distribution. It does not include any user
or agent memory entries.

## Why This Exists

Agent memory systems today are fragmented:

- MEMORY.md files hit size limits (200-line truncation in many editors)
- Skills and knowledge are siloed per agent with no cross-references
- Search is limited to raw grep (no ranking, no structured filtering)
- No validation of entry format or cross-references
- Stale content detection is manual

**agent-memory** solves this with a Python CLI tool (`memory`) that provides progressive disclosure: you see directory listings first, then file summaries (frontmatter + section headers), then section content. Each level is independently useful. Agents (and humans) discover knowledge by narrowing scope, not by retrieving everything at once.

## Architecture

Three-layer approach, each independently useful:

| Layer | Focus | Status |
|-------|-------|--------|
| **Layer 1** | Git repo + CLI tool + validation | **Implemented** (v0.2.0) |
| **Layer 2** | Git sync, write path, ripgrep search | **Implemented** -- write path (PR #10), clone/sync (#2), ripgrep (#4) |
| **Layer 3** | BM25 relevance-ranked search, SQLite index cache | **Implemented** -- BM25 search (PR #9), SQLite index cache (PR #14) |

### Layer 1: CLI Tool + Validation

- Python CLI (`memory`) with progressive disclosure commands
- YAML frontmatter schema validation (issue #5 standard)
- Section-level content extraction with partial matching
- Directory initialization for new agents
- Fully tested and pip-installable

### Layer 2: Write Path + Git Sync + ripgrep

- **Write path** -- create and update entries with `memory new` / `memory update` (PR #10)
- **Git operations** -- automatic `git add`, `commit`, and `push` after writes
- **Agent-scoping** -- agents write only to their own `memory/{agent-id}/` directory and `memory/shared/`
- **Clone and sync** -- `memory clone` and `memory sync` for local memory access (#2)
- **Ripgrep search** -- Implemented as `memory grep` for exact/regex search (#4)

### Layer 3: BM25 Search

- **BM25 ranking** over section-level documents (PR #9)
- Frontmatter-aware filtering (category, confidence, author, tags, status)
- No vector storage -- deterministic, naturally invalidated when files change
- Section-level indexing: sections are the knowledge unit, files are containers
- Best-passage snippet extraction for search results

## Installation

Requires Python 3.10+.

### For agents (recommended)

Install globally with `pipx` so the `memory` command is available without activating a venv:

```bash
pipx install git+ssh://git@github.com/axis-marbell/agent-memory-cli.git@v0.2.0
```

The `memory` command is now available system-wide.

For full agent integration (hooks, environment variables, testing), see the [Integration Guide](docs/integration-guide.md).

### For contributors

Clone and install in editable mode for development:

```bash
git clone git@github.com:axis-marbell/agent-memory-cli.git
cd agent-memory-cli
python -m venv venv
source venv/bin/activate
pip install -e .
```

## Quick Start

### Initialize directory structure for an agent

```bash
memory init my-agent --base memory
```

Creates:
```
memory/my-agent/
  atlas/        # Reference knowledge
  efforts/      # Project/task tracking
  calendar/     # Time-based entries
  moc/          # Maps of content (indices)
```

### List entries (Level 1 -- directory listing)

```bash
memory ls memory/my-agent/atlas
```

Output:
```
  swarm-messaging.md -- "How the swarm messaging protocol works" [established]
  deployment-guide.md -- "Step-by-step deployment for ASP nodes" [working]
```

### View table of contents (Level 2 -- frontmatter + sections)

```bash
memory toc memory/my-agent/atlas/swarm-messaging.md
```

Output:
```
  Delivery Guarantees -- At-least-once delivery with idempotent message IDs.
  Acknowledgment Flow -- The receiver sends an ACK back through the swarm channel.
  Retry Strategy -- Exponential backoff with jitter, max 3 retries.
```

### Read a section (Level 3 -- full content)

```bash
memory section memory/my-agent/atlas/swarm-messaging.md "delivery"
```

Section titles are matched with case-insensitive partial matching, so `"delivery"` matches `"Delivery Guarantees"`.

### Validate entries

```bash
# Validate a single file
memory validate memory/my-agent/atlas/swarm-messaging.md

# Validate an entire directory (recursive)
memory validate memory/
```

Output:
```
  memory/my-agent/atlas/swarm-messaging.md
    [pass] Frontmatter exists
    [pass] Required field: description
    [pass] Required field: author
    [pass] Required field: created
    [pass] Required field: updated
    [pass] Section descriptions valid

  All 1 file(s) valid.
```

### Create a new entry

```bash
AGENT_ID=my-agent memory new swarm-messaging \
  -d "How the swarm messaging protocol works" \
  -c atlas \
  --confidence established \
  -t "protocol,messaging" \
  -b "## Delivery Guarantees
At-least-once delivery with idempotent message IDs." \
  --base memory
```

Creates `memory/my-agent/atlas/swarm-messaging.md` with frontmatter and body, then commits and pushes.

### Update an existing entry

```bash
memory update memory/my-agent/atlas/swarm-messaging.md \
  --add-tags "a2a" \
  --confidence established \
  --base memory
```

Updates frontmatter fields and the `updated` timestamp. Use `-b` to replace the body content.

### Search memory (BM25 ranked)

```bash
memory search "delivery guarantees" --base memory
```

Output:
```
  memory/my-agent/atlas/swarm-messaging.md > Delivery Guarantees  [score: 2.41]
    At-least-once delivery with idempotent message IDs...
```

Filter by frontmatter fields:

```bash
memory search "deployment" --category atlas --confidence established --base memory
memory search "sprint goals" --scope own --base memory
```

### JSON output

All commands support `--json-output` for machine-readable output:

```bash
memory --json-output ls memory/my-agent/atlas
memory --json-output toc memory/my-agent/atlas/swarm-messaging.md
memory --json-output search "delivery" --base memory
memory --json-output validate memory/
```

## Frontmatter Schema

Every memory entry starts with YAML frontmatter between `---` delimiters. Based on the issue #5 schema standard.

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `description` | string | One-line summary (~120 chars recommended) |
| `author` | string | Agent ID that created the entry |
| `created` | datetime | ISO 8601 creation timestamp |
| `updated` | datetime | ISO 8601 last update timestamp |

### Optional Fields

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `tags` | list[str] | any | Categorization tags |
| `related` | list[str] | any | Wiki-links to related entries |
| `confidence` | enum | `established`, `working`, `exploratory` | Knowledge maturity |
| `category` | enum | built-ins plus configured categories | Entry type |
| `status` | enum | `active`, `archived`, `draft` | Lifecycle state |
| `supersedes` | string | wiki-link | Entry this replaces |

### Configurable Categories

The built-in memory categories are `atlas`, `efforts`, `calendar`, and `moc`.
Agents and projects can add category types without patching the CLI by defining
safe directory names in user config, repo-local config, or the environment:

```yaml
# ~/.config/agent-memory/config.yaml
# .agent-memory.yaml, .agent-memory.yml, or .agent-memory/config.yaml
categories:
  - scientific_papers
```

```bash
AGENT_MEMORY_CATEGORIES=scientific_papers,experiment_notes
```

Category names must match `^[a-z0-9][a-z0-9_-]*$`.

### Example Entry

```markdown
---
description: How the swarm messaging protocol handles delivery and acknowledgment
author: <agent-id>
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

Each message carries a UUID that the receiver tracks. Duplicate
deliveries are detected and silently dropped.

## Acknowledgment Flow
The receiver sends an ACK message back through the same swarm channel.

Acknowledgments use the original message UUID as a correlation ID...
```

## Directory Structure

```
memory/
  {agent-id}/           # Scoped to individual agent
    atlas/              # Reference knowledge -- "what I know"
    efforts/            # Project/task tracking -- "what I'm working on"
    calendar/           # Time-based entries -- "what happened when"
    moc/                # Maps of content -- indices and navigation
  shared/               # Cross-agent knowledge -- "what we all know"
.schema/                # Schema definitions (future)
```

### Categories

| Category | Purpose | Examples |
|----------|---------|---------|
| `atlas` | Stable reference knowledge | API patterns, architecture docs, tool guides |
| `efforts` | Active projects and tasks | Sprint goals, implementation plans, PR tracking |
| `calendar` | Time-bound events and logs | Session summaries, incident reports, milestones |
| `moc` | Maps of content (indices) | Topic indices, entry cross-references |

### Agent Scoping

Agents can only write to their own directory and `shared/`:

```
AGENT_ID=<agent-id>

# Allowed:
memory/<agent-id>/atlas/new-entry.md
memory/shared/team-decisions.md

# Blocked:
memory/<other-agent-id>/atlas/any-entry.md
```

## Entry Format Rules

### Section Description Convention

Every `##` section header must be followed by a plain prose description line as its first non-empty content. This enables Level 2 disclosure (table of contents) to show meaningful summaries without loading full content.

Valid description lines are:
- Non-empty
- Do not start with `#`, backtick, `|`, or list markers (`-`, `*`)

```markdown
## Delivery Guarantees
At-least-once delivery with idempotent message IDs.    <-- valid description

## Bad Example
- This starts with a list marker                       <-- INVALID (not prose)

## Another Bad Example
| Column | Header |                                     <-- INVALID (table)
```

### Wiki-Links

Use `[[topic-name]]` syntax for cross-references between entries. These are stored in the `related` frontmatter field:

```yaml
related: ["[[swarm-architecture]]", "[[message-queue-design]]"]
```

## Progressive Disclosure

The tool implements four levels of disclosure, each independently useful:

| Level | Command | What You See | What Gets Loaded |
|-------|---------|-------------|------------------|
| 0 | `memory init` | Directory structure | Nothing (creates dirs) |
| 1 | `memory ls <dir>` | File names + descriptions | Frontmatter only |
| 2 | `memory toc <file>` | Section headers + descriptions | Frontmatter + headers |
| 3 | `memory section <file> "title"` | Full section content | One section |

The key insight: you never need to load an entire file to decide if it contains what you need. Filenames tell you the topic, frontmatter tells you the summary, section headers tell you the structure, and only then do you load the specific section you need.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `AGENT_ID` | For scoped operations | Agent identity (e.g., `<agent-id>`) |
| `AGENT_MEMORY_REPO` | For sync (Layer 2) | Git remote URL for memory repo |
| `AGENT_MEMORY_PATH` | For sync (Layer 2) | Local clone path (default: `~/.agent-memory`) |
| `AGENT_MEMORY_CACHE_PATH` | No | Override path for the SQLite index cache database (default: `<base>/.agent-memory-cache/index.db`) |
| `MEMORY_BASE` | No | Override for the `--base` flag in hooks (default: value of `AGENT_MEMORY_PATH`) |

## CLI Reference

```
Usage: memory [OPTIONS] COMMAND [ARGS]...

  Progressive disclosure memory management for autonomous agents.

Options:
  --json-output  Output as JSON.
  --help         Show this message and exit.

Commands:
  cache     Manage the SQLite index cache for BM25 search.
  clone     Clone the memory repository to a local path.
  grep      Ripgrep-based exact/regex search over memory entries.
  init      Create standard directory structure for a new agent.
  ls        List memory entries with frontmatter descriptions.
  new       Create a new memory entry with frontmatter.
  search    BM25 relevance-ranked search over memory sections.
  section   Show content of a specific section (case-insensitive partial match).
  sync      Sync the local memory repository with the remote.
  toc       Show table of contents for a memory entry.
  update    Update an existing memory entry.
  validate  Validate frontmatter and section format of memory entries.
```

## Claude Code Integration

New agent? Start with the [Integration Guide](docs/integration-guide.md) for a step-by-step walkthrough of self-adopting agent-memory, including environment setup, hook configuration, and a full verification test plan.

For hook internals and architecture, see [`hooks/README.md`](hooks/README.md).

## Known Gotchas

Integration lessons from self-adoption and testing:

- **PS1 guard**: Environment variables (`AGENT_ID`, `AGENT_MEMORY_PATH`, etc.) must go ABOVE the `[ -z "$PS1" ] && return` line in `~/.bashrc` for hooks to see them in non-interactive shells.
- **Heredoc syntax**: Hook dry-runs require heredoc input (`cat <<'EOF'`). Piping with `echo` causes exit 127.
- **BM25 needs headers**: Memory entries need `##` section headers in the body for BM25 search to index them. Entries without sections will not appear in search results.
- **Positional vs `--base`**: `validate`, `ls`, `toc` use a positional path argument, NOT `--base`. Only write commands and search use `--base`.
- **Prose before lists**: `##` sections need a prose sentence before any bullet lists. The validator enforces this as the section description convention.
- **`--no-git` flag**: Use in automated contexts (hooks, CI) to skip git add/commit/push operations.
- **`git pull --rebase`**: Always pull before push when the repo has concurrent contributors to avoid merge conflicts.
- **`pipx reinstall`**: After `git pull` that updates CLI code, run `pipx install --force .` to refresh the installed binary.

## Development

```bash
# Clone and set up
git clone git@github.com:axis-marbell/agent-memory-cli.git
cd agent-memory-cli
python -m venv venv
source venv/bin/activate
pip install -e .

# Run tests
python -m pytest tests/ -v

# Lint
ruff check src/ tests/
```

### Project Layout

```
src/agent_memory/
  __init__.py       # Package version
  bm25.py           # BM25Okapi scoring algorithm
  cache.py          # SQLite index cache for BM25 search (build, status, clear)
  cli.py            # Click CLI commands and version reporting
  config.py         # Centralized path and multi-repository configuration
  config_cli.py     # Config show, init, and set commands
  git_ops.py        # Git add, commit, push, pull operations
  parser.py         # Frontmatter + section parsing
  rg_parser.py      # Ripgrep JSON output parser
  ripgrep.py        # Ripgrep subprocess integration for memory grep
  search.py         # Section indexer, scope resolver, filter matcher
  snippet.py        # Best-passage snippet extraction for search results
  sync.py           # Clone and sync operations for local memory repo
  tokenizer.py      # Lowercase tokenizer with English stoplist
  validator.py      # Schema validation (issue #5 standard)
  writer.py         # Entry creation and update with frontmatter generation
tests/
  test_*.py         # Unit and integration coverage for package modules
  fixtures/         # Sample .md files for testing
hooks/              # Optional Claude Code integration hooks
scripts/            # Maintenance and migration utilities
skills/             # Reusable agent-memory usage guidance
```

## Source lineage

Version 0.2.0 packages the maintained CLI from
`finml-sage/agent-memory` through source commit
`7ed476292b73568223460a4d42e06fa7826b6124`. The repository starts with fresh
history so that the original memory corpus and agent-local files are not
reachable from this distribution.

## License

MIT
