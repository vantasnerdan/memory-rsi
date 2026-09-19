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
**vendors the CLI** (under [`cli/`](cli/)) and exposes memory, bootstrap, and graph-only
GitNexus tools to DSH agents. Explicit setup provisions private dependencies; merely
adding or importing the plugin performs no backend installation or data migration.

## Tools

| Tool | Wraps | Purpose |
|---|---|---|
| `memory_setup` | Private runtime + `memory bootstrap` | Readiness, explicit install/initialize, Codex preview/apply, allowlisted instruction sync |
| `gitnexus` | Audited graph-only GitNexus 1.6.7 | Doctor, status, analyze, lexical graph query, context, impact, detect_changes, list — never embeddings/wiki |
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

Humans can use the same JSON APIs from a terminal. After private setup, optionally
add its executables to this shell's PATH (use your configured runtime directory if overridden):

```sh
export PATH="${DSH_HOME:-$HOME/.dsh}/plugins/memory-rsi/runtime/python/bin:$PATH"
memory plan --request '{"action":"templates"}' --base /path/to/memory
memory policy --request '{"action":"read"}' --base /path/to/memory
# Multi-line requests: memory plan --request - --base /path/to/memory < request.json
```

See **[Contracts and policy guide](docs/contracts.md)** for creation, achievement
review, template improvements, policy history/rollback, and managed `AGENTS.md` sync.
Policy is operator-editable; audit labels are not authenticated human approval.
Shared requirements should not be weakened without explicit human review.

## Requirements

- POSIX or Windows through WSL; native Windows setup is unsupported.
- Node.js **22+**, npm, the `dsh` CLI, Git, and Python **3.10+** with `venv`/pip.
- `curl` for the launcher, network access during explicit dependency installation,
  and a platform supported by GitNexus's native database/parser dependencies.

See the [support and validation matrix](docs/bootstrap.md#prerequisites-and-support).
A working installed-runtime graph smoke is not a claim that every clean installation
or platform has been validated.

## Install

### Complete setup

```sh
curl -fsSL https://raw.githubusercontent.com/vantasnerdan/memory-rsi/main/scripts/install.sh | sh -s -- --yes
```

Optional profile, memory root, and local author:

```sh
curl -fsSL https://raw.githubusercontent.com/vantasnerdan/memory-rsi/main/scripts/install.sh | sh -s -- \
  --yes --profile web --base "$HOME/agent-memory" --agent-id my-agent
```

The launcher fetches the plugin and installs Node dependencies, then runs the explicit
setup CLI. `--yes` provisions a **private Python venv**, bundled CLI, **ripgrep 14.1.0**,
and **GitNexus 1.6.7**; it does not modify global pip/npm installations. It initializes
missing memory defaults, configures the profile, and syncs the canonical policy's
managed section to the selected instruction targets (default `$DSH_HOME/AGENTS.md`).
Existing policy, templates, and unmanaged instruction text are preserved.

Defaults derive from your environment: `DSH_HOME` is `$HOME/.dsh` when unset, memory
lives at `$AGENT_MEMORY_PATH` or `$DSH_HOME/memory`, and private dependencies live at
`$DSH_HOME/plugins/memory-rsi/runtime`. Author identity derives from the local user,
not a shipped developer/session identity. Existing installer-managed choices are
retained on rerun unless overridden. Reload/restart an already-running profile.

From a clone with Node dependencies installed:

```sh
node bin/setup.js --help
node bin/setup.js --profile web --status
node bin/setup.js --profile web --yes
```

`--no-gitnexus` intentionally skips graph provisioning; a fresh memory-only setup is
not fully graph-ready. Existing repositories without Git identity report incomplete
and require the owner to configure it explicitly. Bootstrap does not configure remotes
or push, and upgrades do not overwrite existing user policy/templates.

### Package-only install and first agent turn

`dsh plugin --profile web add /path/to/memory-rsi` adds the JavaScript plugin; ensure
`memory-rsi` is also in that profile's bundle list, then reload the profile. This alone
**does not install private backends or migrate data**. Tools still register when
backends are missing, so a new agent can start with:

```js
memory_setup({ action: "status" })
// After explicit installation authorization:
memory_setup({ action: "install", request: '{"graph":true}' })
memory_setup({ action: "initialize" })
memory_setup({ action: "status" })
```

Bare plugin setup defaults to an empty instruction-file allowlist. It still injects
dynamic canonical-policy guidance, but synchronizing global/project instruction files
requires operator configuration and preview/apply; the full installer handles its
selected targets explicitly.

### Codex migration is opt-in

`--codex-home PATH` with optional repeatable `--memory-dir PATH` creates a **preview**.
Inspect it, then separately run `--apply-migration FILE` with the same `--base`, or use
`memory_setup` actions `preview_migration` and `apply_migration`. Selected Markdown is
copied as reference material; source files stay unchanged. Credentials, provider/MCP
configuration, sessions, and history are not migration targets. Imported instructions
are not automatically activated.

See **[Portable bootstrap and migration](docs/bootstrap.md)** for exact commands,
first-run tools, readiness, instruction synchronization, and troubleshooting.

## Configuration

The full installer writes the profile's managed `memory-rsi` configuration. Operators
can set explicit overrides in its `cordis.patch.yml`; do not add a duplicate plugin row.

| Setting | Purpose |
|---|---|
| `base`, `agentId` | Selected memory root and local author identity |
| `runtimeDir` | Private dependency root; defaults under the local DSH home |
| `memoryBin`, `pythonBin` | Managed CLI/interpreter paths or explicit operator alternatives |
| `bootstrapPython` | Python used to create the private venv; default `python3` |
| `gitnexusBin`, `gitnexusHome` | Audited GitNexus executable and isolated runtime HOME |
| `instructionFiles` | Operator-owned absolute-path allowlist; bare plugin default `[]` |
| `timeoutMs` | Memory command budget; default 60000 ms |
| `gitnexusTimeoutMs` | Graph subprocess budget; default 120000 ms |
| `setupTimeoutMs` | Installation budget; default 600000 ms |

## Graph-only GitNexus

```js
gitnexus({ action: "doctor" })
gitnexus({ action: "analyze", cwd: "/absolute/selected/repository" })
gitnexus({ action: "query", cwd: "/absolute/selected/repository", query: "checkout" })
gitnexus({ action: "impact", cwd: "/absolute/selected/repository", symbol: "checkout" })
```

Analyze uses `--index-only --skip-git` and never enables embeddings or modifies agent
instructions/skills. An embedding-enabled `.gitnexusrc` is rejected, not silently
honored or rewritten. Existing embeddings are preserved. `query` uses **lexical Cypher**,
not GitNexus's native hybrid query, so even an existing embedding-bearing index cannot
cause query-vector generation/model loading through that action. No arbitrary flags,
raw Cypher, wiki/LLM, model download, or embedding-removal surface is exposed.

Graph commands use a private registry/HOME and no optional-extension downloads; FTS
is not required for lexical graph search. Readiness reports installed/native support
separately from repository indexing. See the [graph tool guide](docs/bootstrap.md#graph-only-project-tools)
for the complete allowlist and limitations.

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
node scripts/smoke-bootstrap.mjs              # networked packed-package install in clean temporary HOME
```

## License

MIT. The vendored CLI keeps its upstream attribution in [`cli/`](cli/).
