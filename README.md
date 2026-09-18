# memory-rsi

A [DeepSeek Harness](https://www.npmjs.com/package/@deepseek-ai/dsh) (DSH) plugin that turns the
[agent-memory CLI](https://github.com/axis-marbell/agent-memory-cli) into first-class model tools.

agent-memory is progressive-disclosure memory management for autonomous agents: Git-backed markdown
with YAML frontmatter, where *structure IS the memory* — discovery over retrieval. This repository
**vendors the full CLI** (under [`cli/`](cli/)) and wraps its entire command surface as DSH tools, so
your agent gets persistent, searchable, Git-synced memory out of the box.

## Tools

| Tool | Wraps | Purpose |
|---|---|---|
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

The plugin also injects a system-prompt section teaching the model the
discover-narrow-write workflow (`ls → toc → section/search → new/update`).

## Requirements

- A DeepSeek Harness installation (the `dsh` CLI)
- Python 3.10+ on the host, with the vendored CLI installed (see below)

## Install

### One command, no clone

```sh
curl -fsSL https://raw.githubusercontent.com/vantasnerdan/memory-rsi/main/scripts/install.sh | sh
```

Pass a profile name after `--` (defaults to `web`):

```sh
curl -fsSL https://raw.githubusercontent.com/vantasnerdan/memory-rsi/main/scripts/install.sh | sh -s -- my-profile
```

The script downloads the repository to `$DSH_HOME/plugins/memory-rsi`,
installs its runtime dependencies, installs the vendored CLI, wires the plugin
into the profile, and registers the bundle. Re-running it updates to the
latest `main`.

### From a clone

```sh
git clone https://github.com/vantasnerdan/memory-rsi.git
cd memory-rsi
./scripts/install.sh            # installs into the "web" profile
./scripts/install.sh my-profile # or any other profile name
```

The script is idempotent and does three things:

1. Installs the vendored CLI — **pipx** if available, else `pip install --user`,
   else a dedicated venv with a `memory` shim on `~/.local/bin`.
2. Adds this repository to the dsh profile via `dsh plugin --profile <p> add`.
3. Registers `memory-rsi` in the profile's `dsh.profile` bundle list.

Then restart the profile (`dsh --profile web`) and verify with
`dsh --profile web --dump-config | grep memory-rsi`.

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
    base: ""               # default --base dir; empty = CLI auto-detect (config/env/cwd)
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
npm test            # plugin smoke tests
pip install ./cli[dev] && pytest cli/tests   # vendored CLI test suite
```

## License

MIT. The vendored CLI keeps its upstream attribution in [`cli/`](cli/).
