# Portable bootstrap and Codex migration

The plugin can be loaded before its backends exist. **Adding/loading it does not provision Python or GitNexus, initialize memory, import Codex data, or install models.** It registers `memory_setup` and `gitnexus` so a fresh agent can inspect readiness and explicitly set up its tools.

## Prerequisites and support

For the complete installer, provide:

- A POSIX environment; use **WSL on Windows**. Native Windows setup is rejected in this release.
- Node.js **22+**, npm, an installed `dsh` CLI, Git, and Python **3.10+** with `venv`/pip support. Some distributions package `python3-venv` separately.
- `curl` for the one-line installer and network access to the repository and package registries during explicit installation.
- A platform supported by the pinned GitNexus native database/parser dependencies. If a matching native binary is unavailable, installation may require the dependency's build prerequisites or fail; inspect the reported error rather than assuming graph support.

| Environment/check | Validation status |
|---|---|
| Linux x64, Node 22.22.3, installed GitNexus 1.6.7 | Isolated HOME/repository graph analysis, lexical query, context, impact, status, list, and change detection exercised successfully |
| Linux x64, Node 22.22.3, Python 3.12; clean HOME/config/caches and no memory/GitNexus backends | **Passed:** packed-package install provisions private CLI/ripgrep/GitNexus; real memory/plan/policy calls, reinstall preservation, Codex preview/apply/repeat, and graph query with zero embeddings/model-cache files. Reproduce with `node scripts/smoke-bootstrap.mjs`; CI runs the same check. |
| Existing DSH installation, isolated fresh profile | **Passed:** real plugin/bundle registration, repeated profile setup, and composed-config validation; independent of the operator's profile. |
| Other POSIX platforms, including macOS | Not validated here; native dependency availability must be checked |
| Windows via WSL | Intended POSIX route; clean WSL installation not validated here |
| Native Windows | Unsupported by the current private Python runtime/bootstrap implementation |

`memory_setup` status distinguishes installed tooling from memory initialization. Its overall `ready` does **not** mean any project has been indexed. GitNexus doctor deliberately reports repository `graphReady: false`/unverified; analyze and query the chosen repository separately.

## Complete unattended installation

Review the installer source if required by your environment, then run:

```sh
curl -fsSL https://raw.githubusercontent.com/vantasnerdan/memory-rsi/main/scripts/install.sh | sh -s -- --yes
```

Choose a profile, memory root, or author explicitly:

```sh
curl -fsSL https://raw.githubusercontent.com/vantasnerdan/memory-rsi/main/scripts/install.sh | sh -s -- \
  --yes --profile web --base "$HOME/agent-memory" --agent-id my-agent
```

The shell launcher fetches the plugin and installs its Node dependencies, then runs `bin/setup.js`. The explicit `--yes` authorizes private dependency provisioning, initialization of the selected memory root, and synchronization of the canonical policy to the selected instruction targets. It does **not** authorize applying a Codex migration preview.

Full setup:

1. Installs the bundled Python CLI into a private venv and **ripgrep 14.1.0** into that same venv.
2. Installs **GitNexus 1.6.7** into a private npm prefix. It does not use global pip/npm installation or generate embeddings.
3. Creates missing memory directories, plan templates, and canonical policy. Existing policy/templates win; an update does not overwrite them.
4. Initializes a new local memory Git repository when needed. Existing repository identity, branches, staging, and commits are preserved. Bootstrap neither sets up a remote nor pushes.
5. Synchronizes the canonical policy's managed section to the selected instruction files, preserving text outside that section. The default target is `$DSH_HOME/AGENTS.md`.
6. Adds the plugin and its bundle to the selected DSH profile and records managed configuration. Unmanaged/ambiguous existing plugin rows require explicit operator resolution rather than silent replacement.
7. Returns readiness diagnostics. Reload/restart a profile that was already running; package updates are not automatically hot-reloaded.

### Portable defaults and storage

`DSH_HOME` defaults to `$HOME/.dsh`. Explicit options take precedence; reruns retain the selected base, identity, runtime directory, bootstrap Python, instruction targets, and unrelated configuration when corresponding options are omitted. Full provisioning deliberately refreshes executable paths to its managed runtime. For externally managed CLI/GitNexus executables, use package-only installation and operator configuration instead.

| Setting | Fresh-install default |
|---|---|
| Profile | `web` |
| Memory base | `$AGENT_MEMORY_PATH`, when set; otherwise `$DSH_HOME/memory` |
| Memory author | Current local identity (`AGENT_ID`, username), normalized to a safe lowercase identifier; override with `--agent-id` |
| Private runtime | `$DSH_HOME/plugins/memory-rsi/runtime` |
| Python CLI and ripgrep | `runtime/python/bin/` |
| GitNexus package | `runtime/graph/node_modules/` |
| GitNexus registry/cache HOME | `runtime/graph-home/`, separate from the operator's normal GitNexus HOME |
| Canonical policy | `<base>/shared/policies/agent-policy.md` |

No developer home, session identifier, preexisting memory, or operator repository is used as a shipped default. A managed tool is not necessarily added to your interactive shell's PATH; DSH receives its executable path.

### Other setup options

From an existing checkout with Node dependencies installed:

```sh
node bin/setup.js --help
node bin/setup.js --profile web --status             # read-only diagnostics
node bin/setup.js --profile web --yes               # explicit setup/update
```

Useful options include `--python COMMAND`, `--runtime-dir PATH`, repeatable `--instruction-file PATH`, and `--no-instructions` to skip instruction synchronization for that invocation. `--no-profile` provisions backends without changing a DSH profile. `--no-git` selects file-only memory initialization, not Git persistence.

`--no-gitnexus` intentionally skips graph provisioning. A fresh memory-only installation is **not fully graph-ready**; this flag does not remove an existing GitNexus installation or index. Rerun without it when graph tools are wanted.

## Package-only installation and a fresh agent's first turn

A package add can fetch/install the JavaScript plugin, but does not run the private backend setup:

```sh
dsh plugin --profile web add /path/to/memory-rsi
```

Ensure the profile's bundle list includes `memory-rsi`, then load/reload that profile. The complete installer handles both steps; a bare package add is not a substitute for them.

The agent can then call these actual registered tools:

```js
memory_setup({ action: "status" })
// After the operator authorizes dependency installation:
memory_setup({ action: "install", request: '{"graph":true}' })
memory_setup({ action: "initialize" })
memory_setup({ action: "status" })
```

`request` is a JSON string containing fields **without** `action`. `install` accepts only optional `graph:boolean` (default `true`); `{"graph":false}` is explicit memory-only provisioning. `initialize` creates missing defaults and preserves existing files. The tool also accepts `no_git: true` for file-only initialization. A missing backend is an actionable status/error, not a reason to fail plugin mounting.

Bare plugin configuration defaults `instructionFiles` to **an empty allowlist**. It does not silently acquire permission to write a global or project `AGENTS.md`. Dynamic canonical-policy guidance is still included at each prompt assembly. To persist that guidance in a file, the operator must first configure the allowed target, then preview and apply:

```js
memory_setup({
  action: "sync_instructions",
  request: '{"target":"/absolute/allowed/AGENTS.md"}'
})
// Inspect the returned diff. Use its exact two revisions after approval:
memory_setup({
  action: "sync_instructions",
  request: JSON.stringify({
    target: "/absolute/allowed/AGENTS.md", apply: true,
    expected_revision: policyRevision,
    expected_target_revision: targetRevision,
    actor: "human", reason: "Operator reviewed this managed-policy synchronization"
  })
})
```

An `actor` label records provenance; it is not itself proof of human approval. The full `--yes` installer configures and synchronizes its selected allowlist, unlike bare `memory_setup` installation.

## Graph-only project tools

```js
gitnexus({ action: "doctor" })
gitnexus({ action: "analyze", cwd: "/absolute/selected/repository" })
gitnexus({ action: "query", cwd: "/absolute/selected/repository", query: "checkout", limit: 10 })
gitnexus({ action: "context", cwd: "/absolute/selected/repository", symbol: "checkout" })
gitnexus({ action: "impact", cwd: "/absolute/selected/repository", symbol: "checkout", direction: "upstream" })
gitnexus({ action: "detect_changes", cwd: "/absolute/selected/repository", scope: "unstaged" })
```

Also available: `status` for an explicit repository root and `list` for the managed runtime registry. Except `doctor`/`list`, each call requires an absolute owner-selected `cwd`; no repository is inferred from hidden session fields.

The adapter enforces the audited graph-only surface:

- Analyze uses `--index-only --skip-git`: it indexes precisely the selected root without injecting AGENTS/CLAUDE instructions or skills. Optional `force:true` rebuilds an unchanged index.
- **Never passes `--embeddings` or `--drop-embeddings`.** Existing embeddings are preserved, not silently deleted. If `.gitnexusrc` enables embeddings or embedding removal, analysis refuses and asks the owner to change that setting explicitly; the tool never rewrites it.
- The native GitNexus `query` command is **not used**: on an embedding-bearing index it can generate a query vector/load a model. Our `query` is a generated, read-only **lexical Cypher** search over names/paths, with AND matching of identifier words. It is not semantic search or BM25 and does not require the optional FTS extension.
- No raw Cypher, arbitrary CLI flags, wiki/LLM generation, model download, or embedding-removal action is exposed. Other GitNexus versions are rejected pending audit.
- Graph commands use the private HOME, offline model settings, and disabled optional-extension downloads. Normal operator registry/configuration is not modified.
- Existing index/config symlinks, hardlinks, and special files are rejected. These are cooperative preflight checks, not a filesystem sandbox against concurrent path replacement.
- Child output is bounded, cancellation/timeouts stop execution, and nonzero exits/backend error envelopes are surfaced. Native database failures are not reported as readiness.

An offline FTS-unavailable warning is compatible with working lexical graph tools. If analysis or a subsequent query fails, retain its actual error and inspect `doctor`; do not treat a successful version check as a working index.

## Codex migration: preview, review, explicit apply

Nothing is imported automatically. Select the Codex home and dedicated memory directories intentionally; a whole home directory is not a memory source.

```sh
node bin/setup.js --yes --profile web --base "$HOME/agent-memory" \
  --codex-home "$HOME/.codex" --memory-dir "$HOME/codex-memory"
```

Repeat `--memory-dir` to select additional Markdown roots; it is used together with `--codex-home`. Setup writes `codex-migration-preview.json` under the selected runtime directory and reports its absolute path. Inspect that file and the selected Markdown content. Then use the reported preview path with the **same `--base`**:

```sh
node bin/setup.js --profile web --base "$HOME/agent-memory" \
  --apply-migration /absolute/runtime/codex-migration-preview.json
```

Apply is a separate explicit operation, does not reinstall dependencies, and checks the saved revision and destination base. Changed sources, stale previews, or conflicting destination files require a fresh preview/operator resolution; existing conflicting files are not overwritten.

Equivalent agent calls:

```js
memory_setup({
  action: "preview_migration",
  request: '{"source_home":"/absolute/codex-home","memory_dirs":["/absolute/selected-memory"],"import_id":"codex"}'
})
// Retain the response's full preview and revision, inspect it, then explicitly approve:
memory_setup({
  action: "apply_migration",
  request: JSON.stringify({ preview: reviewedPreview, expected_revision: reviewedRevision })
})
```

The selection includes Codex `AGENTS.md`, Markdown under its `skills/`, and Markdown in the explicitly selected memory directories. Copies go under `<base>/shared/imports/<import_id>/` as **untrusted reference material**, not active instructions or installed DSH skills. Source files stay unchanged; migration does not stage/commit the imports or promote them into canonical policy.

**Credentials, authentication files, provider/MCP configuration, sessions, history, logs, and databases are not migration targets.** A preview is not a secret scanner: inspect any selected Markdown that itself might contain sensitive information. Importing policy or skills as active instructions requires a separate explicit review and change.

## Readiness and recovery

- **Missing Python/venv, Git, npm or network:** install/enable the named prerequisite and rerun explicit setup. Package loading remains available for diagnostics.
- **Existing memory repository lacks Git identity:** initialization preserves its configuration and reports incomplete. Set the intended `user.name` and `user.email` using `git config --local` in that repository, then rerun status/initialization. Bootstrap never chooses an identity for an existing repository.
- **Existing policy/template is invalid:** repair it explicitly. Upgrades and initialization preserve existing user content rather than replacing it with current defaults.
- **Graph unsupported or native startup fails:** retain the real error. Memory-only setup is an intentional fallback, not full graph readiness. A fresh Linux graph smoke does not establish support for every platform or recover an existing damaged index.
- **Migration conflict/stale revision:** preserve both source and destination, inspect changes, and preview again. Do not edit the saved revision to bypass checks.
- **Managed-path link/special-file refusal:** choose a real private directory or resolve the link deliberately; setup does not follow it into another location.

See [Contracts and policy](contracts.md) for normal plan/template/policy operations after bootstrap.
