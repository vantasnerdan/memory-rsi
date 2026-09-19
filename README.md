# memory-rsi

**Rewards > gates.** Help agents choose excellent behavior before and during work:
shared memory contracts describe useful outcomes, achievements, and credible evidence.
Validation supports honest achievement; permissions and safety remain boundaries.
This is reward-shaped guidance, not a change to a model's training reward.

A [DeepSeek Harness](https://www.npmjs.com/package/@deepseek-ai/dsh) (DSH) plugin that turns the
[agent-memory CLI](https://github.com/axis-marbell/agent-memory-cli) into first-class model tools,
including template-based plans and editable, persistent agent instructions.

agent-memory is progressive-disclosure memory management for autonomous agents: Git-backed markdown
with YAML frontmatter, where *structure IS the memory* — discovery over retrieval. This repository
**vendors the full CLI** (under [`cli/`](cli/)) and wraps its entire command surface as DSH tools, so
your agent gets persistent, searchable, Git-synced memory out of the box.

## Tools

| Tool | Wraps | Purpose |
|---|---|---|
| `memory_plan` | `memory plan` | Review/edit templates; create shared plans; record evidence, reviews, exceptions, and achievement progress |
| `memory_policy` | `memory policy` | Read/update persistent prompt policy, inspect history/rollback, preview/sync managed instruction sections |
| `memory_ls` | `memory ls` | Progressive-disclosure directory listing (directories → entry summaries) |
| `memory_toc` | `memory toc` | One entry's frontmatter summary + section titles |
| `memory_section` | `memory section` | Read a single section by (partial) title — the narrowest read |
| `memory_search` | `memory search` | BM25-ranked section search with frontmatter filters and best-passage snippets |
| `memory_grep` | `memory grep` | ripgrep exact/regex search for identifiers and exact strings |
| `memory_new` | `memory new` | Create a validated entry (body via stdin); auto git commit/push |
| `memory_update` | `memory update` | Replace body, adjust tags/confidence/status; auto git commit/push |
| `memory_validate` | `memory validate` | Frontmatter schema + cross-reference validation |
| `memory_init` | `memory init` | Initialize `memory/{agent-id}/` for a new agent |
| `memory_sync` | `memory sync` | Pull/push the memory repo |
| `memory_clone` | `memory clone` | Clone the configured memory repo |
| `memory_cache` | `memory cache build\|status\|clear` | Manage the SQLite BM25 index cache |
| `memory_log` | `memory log` | CLI usage log (recent commands, durations, errors) |

The plugin injects reward-first contract guidance and re-reads the editable policy
at **each DSH prompt assembly**. Agent and human edits therefore affect subsequent
assemblies after the updated plugin is loaded; they do not rewrite prior messages.
It supplements the existing prompt, not the deployment's immutable instructions.

## Shared plans and editable instructions

```js
memory_plan({action: "templates"})
memory_plan({action: "review", request: '{"template_id":"coding"}'})
// Review returns the full template, its revision, and runnable create/update examples.
// Fill the create example for the actual task; pass action separately in the tool.
memory_policy({action: "read"})
```

Plans pin complete template snapshots. Subagents receive the same plan ID, latest
revision, and assigned work-item IDs; scoped revision-checked updates prevent lost
work. Achievements reward outcomes with evidence, not tool counts. Built-in coding
contracts emphasize AST/LSP capability, separation of concerns, single responsibility,
focused files/functions, validation, and authorized GitOps.

Humans can use the same JSON APIs from a terminal:

```sh
memory plan --request '{"action":"templates"}' --base /path/to/memory
memory policy --request '{"action":"read"}' --base /path/to/memory
# Multi-line requests: memory plan --request - --base /path/to/memory < request.json
```

See **[Contracts and policy guide](docs/contracts.md)** for creation, achievement
review, template improvements, policy history/rollback, and managed `AGENTS.md` sync.
Policy is operator-editable; audit labels are not authenticated human approval.
Shared requirements should not be weakened without explicit human review.

## Requirements

- A DeepSeek Harness installation (the `dsh` CLI)
- Python 3.10+ on the host, with the vendored CLI installed (see below)

## Install

### One command, no clone

```sh
curl -fsSL https://raw.githubusercontent.com/vantasnerdan/memory-rsi/main/scripts/install.sh | sh
```

Fully scriptable with flags (a bare profile name also works for backwards
compatibility):

```sh
curl -fsSL .../scripts/install.sh | sh -s -- \
    --profile my-profile \
    --agent-id dan \
    --base ~/agent-memory/memory
```

| Flag | Meaning | Default |
|---|---|---|
| `--profile NAME` | dsh profile to install into | `web` |
| `--agent-id ID` | memory author identity (`agentId` config) | `git config user.name`, else `$USER` |
| `--base DIR` | memory repo directory | `$AGENT_MEMORY_PATH` or `~/agent-memory/memory` |
| `-y, --yes` | never prompt; accept defaults | — |

Run from a terminal, the installer prompts for anything you did not pass;
piped or fully flagged, it is non-interactive — safe for agents to drive.

The installer is idempotent and owns the whole setup:

1. Installs **or upgrades** the vendored CLI — **pipx** if available, else pip
   (active environment or `--user`), with a dedicated venv fallback. Existing
   `memory` commands no longer cause the upgrade to be skipped.
2. Initializes the memory repo (git init, initial commit with a repo-local
   identity, BM25 search cache) when it is missing or has no commits.
3. Adds the plugin to the dsh profile (`dsh plugin --profile <p> add`).
4. Registers `memory-rsi` in the profile's bundle list (`package.json` /
   `dsh.profile`).
5. Writes a managed config block (`agentId`, `base`, timeouts) into the
   profile's `cordis.patch.yml`, refreshing it on re-runs and leaving
   hand-edited rows alone.

### From a clone

```sh
git clone https://github.com/vantasnerdan/memory-rsi.git
cd memory-rsi
./scripts/install.sh              # interactive prompts for anything unset
./scripts/install.sh --agent-id dan --base ~/agent-memory/memory
```

### Manual install

If you prefer to do each step yourself:

```sh
pipx install ./cli          # preferred: puts `memory` on PATH
# or
pip install ./cli           # the plugin then falls back to `python3 -m agent_memory`

dsh plugin --profile web add /path/to/memory-rsi   # or the published package name
```

Then add the package to the profile's bundle list in
`$DSH_HOME/profiles/web/dsh.profile`:

```yaml
dsh:
  profile:
    bundles:
      - "@deepseek-ai/dsh-base"
      - "@deepseek-ai/dsh-web-app"
      - "memory-rsi"
```

Restart the profile. Verify with `dsh --profile web --dump-config | grep memory-rsi`.

## Configuration

Set overrides in the profile's `cordis.patch.yml` (or `$DSH_HOME/cordis.patch.yml`):

```yaml
- id: memory-rsi
  config:
    memoryBin: memory      # console script to invoke
    pythonBin: python3     # used for the `python -m agent_memory` fallback
    base: "/path/to/memory" # tools and prompt share this root; empty resolves CLI defaults once at load
    agentId: ""            # ordinary memory author; plan tools record the calling session ID
    instructionFiles: []   # operator allowlist, e.g. ["/project/AGENTS.md"]; sync previews before applying
    timeoutMs: 60000       # cooperative per-call timeout
```

## Usage (what your agent can do)

- **Discover:** `memory_ls` → `memory_toc <entry>` → `memory_section <entry> <title>`
- **Ask:** `memory_search "how do we deploy the api"` (BM25 over sections,
  filter by `category`/`tag`/`confidence`), or `memory_grep "ECONNREFUSED"` for exact strings
- **Remember:** `memory_new deploy-runbook --description "How we ship" --category efforts --tags ops,deploy`
  (body flows over stdin, frontmatter is validated, git commit+push is automatic)
- **Maintain:** `memory_validate memory/`, `memory_cache build`, `memory_sync`

See [`cli/README.md`](cli/README.md) for the complete upstream documentation,
[`cli/docs/`](cli/docs) for the entry schema, and [`cli/hooks/`](cli/hooks) for the optional
Claude-style lifecycle hooks.

## Development

```sh
npm test                                     # DSH tools, live policy reads, installer regressions
python3 -m pip install ./cli pytest           # runtime + test dependencies
PYTHONPATH=cli/src python3 -m pytest cli/tests # isolated CLI regression suite
python3 scripts/check-language-servers.py     # optional: AST + actual Python/JS LSP symbol queries
```

## License

MIT. The vendored CLI keeps its upstream attribution in [`cli/`](cli/).
