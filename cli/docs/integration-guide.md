# Agent-Memory Integration Guide

Step-by-step self-adoption guide for integrating agent-memory into any Claude Code project.

## Prerequisites

Before starting, confirm ALL of the following are true. Do not skip any.

1. The agent-memory repo is cloned locally:
   ```bash
   git clone git@github.com:axis-marbell/agent-memory-cli.git <CLONE_PATH>
   ```

2. The repo is on `main` branch with all fixes merged:
   ```bash
   cd <CLONE_PATH> && git checkout main && git pull origin main
   ```

3. Python 3.10+ is available:
   ```bash
   python3 --version
   ```

4. ripgrep is installed (required for `memory grep`):
   ```bash
   which rg
   ```
   Expected output: `/usr/bin/rg` or similar path. If missing: `apt install ripgrep -y`

5. pipx is available for CLI installation:
   ```bash
   which pipx
   ```
   If missing (common on Ubuntu 24.04 due to PEP 668):
   ```bash
   apt install python3-pip -y
   pip install --break-system-packages pipx
   pipx ensurepath
   source ~/.bashrc
   ```

If any prerequisite fails, stop and resolve it before continuing.

---

## Step 1: Install the CLI

Install globally with pipx so the `memory` command is available without activating a venv:

```bash
pipx install <CLONE_PATH>
```

Verify the CLI is accessible:

```bash
which memory && memory --help
```

Expected: `which` returns `~/.local/bin/memory` (or similar stable path) and `--help` lists 12 commands: `cache`, `clone`, `grep`, `init`, `ls`, `new`, `search`, `section`, `sync`, `toc`, `update`, `validate`.

After future `git pull` that updates CLI code, refresh the installed binary:

```bash
pipx install --force <CLONE_PATH>
```

---

## Step 2: Environment Variables

**CRITICAL**: On most Linux distributions, `~/.bashrc` contains a non-interactive guard near the top:

```bash
[ -z "$PS1" ] && return
```

This line causes non-interactive shells to exit early, skipping any exports below it. Claude Code hooks and the Bash tool run in non-interactive shells. All agent-memory exports MUST go ABOVE this guard line, or they will be invisible to hooks.

Find the guard line:

```bash
grep -n 'PS1.*return\|return.*PS1' ~/.bashrc
```

Insert ABOVE that line number in `~/.bashrc`:

```bash
# --- agent-memory configuration ---
export AGENT_ID="<AGENT_ID>"
export AGENT_MEMORY_PATH="<CLONE_PATH>/memory"
export AGENT_MEMORY_CACHE_PATH="<CLONE_PATH>/.agent-memory-cache"
```

Optional (only needed for `memory clone`/`memory sync`):

```bash
export AGENT_MEMORY_REPO="git@github.com:axis-marbell/agent-memory-cli.git"
```

Source and verify:

```bash
source ~/.bashrc
echo "AGENT_ID=$AGENT_ID"
echo "AGENT_MEMORY_PATH=$AGENT_MEMORY_PATH"
echo "AGENT_MEMORY_CACHE_PATH=$AGENT_MEMORY_CACHE_PATH"
```

All must print non-empty values.

### Environment Variable Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `AGENT_ID` | Yes | Agent identity for authorship and scoping (e.g., `my-agent`) |
| `AGENT_MEMORY_PATH` | Yes | Path to the `memory/` directory inside the local clone |
| `AGENT_MEMORY_CACHE_PATH` | No | SQLite index cache directory (default: `<base>/.agent-memory-cache/`) |
| `AGENT_MEMORY_REPO` | No | SSH URL for the memory repo (only needed for `clone`/`sync`) |

---

## Step 3: Initialize Memory Directory

Create the standard directory structure:

```bash
memory init <AGENT_ID> --base <CLONE_PATH>/memory
```

This creates:

```
<CLONE_PATH>/memory/<AGENT_ID>/
  atlas/        # Reference knowledge -- "what I know"
  efforts/      # Project/task tracking -- "what I'm working on"
  calendar/     # Time-based entries -- "what happened when"
  moc/          # Maps of content -- indices and navigation
```

Verify:

```bash
ls -la <CLONE_PATH>/memory/<AGENT_ID>/
```

---

## Step 4: Configure Hooks

Edit your project's `.claude/settings.json` to add the hooks configuration. If the file already has a `permissions` key, add `hooks` as a sibling key.

Replace `<CLONE_PATH>` with the absolute path to the agent-memory clone:

```json
{
  "permissions": { "...existing..." },
  "hooks": {
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "<CLONE_PATH>/hooks/pre-compact-save.sh",
            "timeout": 30000
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "<CLONE_PATH>/hooks/post-compact-restore.sh",
            "timeout": 15000
          }
        ]
      }
    ],
    "SubagentStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "<CLONE_PATH>/hooks/subagent-start-load.sh",
            "timeout": 15000
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "<CLONE_PATH>/hooks/subagent-stop-save.sh",
            "timeout": 15000
          }
        ]
      }
    ]
  }
}
```

Validate the JSON:

```bash
python3 -c "import json; json.load(open('<SETTINGS_PATH>')); print('Valid JSON')"
```

Verify all hook scripts are executable:

```bash
ls -la <CLONE_PATH>/hooks/*.sh
```

If any lack execute permission:

```bash
chmod +x <CLONE_PATH>/hooks/*.sh
```

### Hook Behavior Summary

| Hook | Event | Matcher | Timeout | What It Does |
|------|-------|---------|---------|--------------|
| PreCompact | Before compaction | `""` (all) | 30s | Saves session context to memory as an efforts entry |
| SessionStart | After compaction | `"compact"` | 15s | Restores key context into the fresh context window |
| SubagentStart | Subagent spawns | `""` (all) | 15s | Loads relevant institutional memory for the subagent |
| SubagentStop | Subagent finishes | `""` (all) | 15s | Saves subagent learnings back to memory |

Key details:
- The SessionStart matcher MUST be `"compact"` (not empty), so the restore hook only fires after compaction, not on every session start.
- All hooks exit 0 regardless of errors. They never break compaction, session start, or subagent lifecycle.
- PreCompact and SubagentStop write to stderr only (save hooks).
- SessionStart and SubagentStart write to stdout (load hooks inject context).
- Hooks find the `memory` CLI via `common.sh`, which searches PATH, `~/.local/bin/`, `/usr/local/bin/`, and project virtualenvs.

---

## Step 5: CLAUDE.md Compact Instructions

Add the following section to your project's `CLAUDE.md` (or equivalent instruction file). Place it where it will be seen by the agent after compaction:

```markdown
## Compact Instructions

When compacting, always preserve:
- Key decisions and their rationale
- File paths modified and current state
- Test results and verification status
- Error patterns discovered and resolutions
- Active task context and next steps

The PreCompact hook saves detailed context to agent-memory automatically.
After compaction, restored context appears at the top of your fresh context window.
Use `memory search` or `memory section` to retrieve deeper historical context.
```

Verify the section was added:

```bash
grep -n "Compact Instructions" <CLAUDE_MD_PATH>
```

---

## Step 6: Verification Test Plan

Execute each test in order. If any test fails, fix the issue before proceeding.

### 6.1 Verify Environment Variables

```bash
echo "AGENT_ID=${AGENT_ID}"
echo "AGENT_MEMORY_PATH=${AGENT_MEMORY_PATH}"
echo "AGENT_MEMORY_CACHE_PATH=${AGENT_MEMORY_CACHE_PATH}"
```

All must print non-empty values.

### 6.2 Verify CLI

```bash
memory ls <CLONE_PATH>/memory
```

Note: `memory ls` takes a positional path argument, not `--base`.

### 6.3 Verify Hooks Configuration

```bash
python3 -c "
import json
with open('<SETTINGS_PATH>') as f:
    settings = json.load(f)
hooks = settings.get('hooks', {})
expected = ['PreCompact', 'SessionStart', 'SubagentStart', 'SubagentStop']
for event in expected:
    if event in hooks:
        cmd = hooks[event][0]['hooks'][0]['command']
        print(f'  {event}: {cmd}')
    else:
        print(f'  {event}: MISSING')
ok = all(e in hooks for e in expected)
print('All hooks configured.' if ok else 'HOOKS INCOMPLETE')
"
```

### 6.4 Create a Test Memory Entry

Include body content with `##` section headers so BM25 search can index it:

```bash
memory new "integration-test" \
  -d "First memory entry: agent-memory integration test for <AGENT_ID>" \
  --author <AGENT_ID> \
  -c efforts \
  --confidence working \
  --status active \
  -t "test,integration,agent-memory" \
  -b "## Integration Status
Agent-memory integration completed for <AGENT_ID>.

## Components Verified
All components verified working.

- CLI installed via pipx
- All 4 hooks configured in settings.json
- Environment variables set above PS1 guard" \
  --no-git \
  --base <CLONE_PATH>/memory
```

Verify the file was created:

```bash
cat <CLONE_PATH>/memory/<AGENT_ID>/efforts/integration-test.md
```

### 6.5 Verify BM25 Search

```bash
memory search "integration test" --scope own --base <CLONE_PATH>/memory
```

Expected: Returns the integration-test entry with a BM25 score > 0.

If the result is empty, verify the entry has `##` section headers in the body. BM25 indexes section content, not frontmatter.

### 6.6 Verify Ripgrep Search

```bash
memory grep "integration" --scope own --base <CLONE_PATH>/memory
```

Expected: Returns matching lines from the test entry.

### 6.7 Verify Hooks (Dry Run)

Use heredoc syntax for all hook tests. Do NOT use `echo ... | hook.sh` (causes exit 127 in non-interactive shells).

Pre-compact save:

```bash
AGENT_ID=<AGENT_ID> \
  AGENT_MEMORY_PATH=<CLONE_PATH>/memory \
  <CLONE_PATH>/hooks/pre-compact-save.sh <<'HOOKEOF'
{"session_id":"test-001","transcript_path":"/dev/null","trigger":"manual","cwd":"<HOME_DIR>","hook_event_name":"PreCompact"}
HOOKEOF
```

Post-compact restore:

```bash
AGENT_ID=<AGENT_ID> \
  AGENT_MEMORY_PATH=<CLONE_PATH>/memory \
  <CLONE_PATH>/hooks/post-compact-restore.sh 2>/dev/null <<'HOOKEOF'
{"session_id":"test-001","source":"compact","cwd":"<HOME_DIR>","hook_event_name":"SessionStart","transcript_path":"/dev/null","model":"opus-4"}
HOOKEOF
```

Subagent start:

```bash
AGENT_ID=<AGENT_ID> \
  AGENT_MEMORY_PATH=<CLONE_PATH>/memory \
  <CLONE_PATH>/hooks/subagent-start-load.sh 2>/dev/null <<'HOOKEOF'
{"agent_type":"memory-test","agent_id":"test-001","session_id":"test-001","cwd":"<HOME_DIR>","hook_event_name":"SubagentStart"}
HOOKEOF
```

Subagent stop:

```bash
AGENT_ID=<AGENT_ID> \
  AGENT_MEMORY_PATH=<CLONE_PATH>/memory \
  <CLONE_PATH>/hooks/subagent-stop-save.sh 2>&1 <<'HOOKEOF'
{"agent_type":"memory-test","agent_id":"test-001","session_id":"test-001","cwd":"<HOME_DIR>","hook_event_name":"SubagentStop","transcript_path":"/dev/null"}
HOOKEOF
```

All hooks must exit 0. The restore and subagent-start hooks produce stdout output. The save hooks produce stderr diagnostics only.

### 6.8 Validate the Test Entry

```bash
memory validate <CLONE_PATH>/memory/<AGENT_ID>/
```

Note: `memory validate` takes a positional path argument, not `--base`.

### 6.9 Build the SQLite Cache

```bash
memory cache build --base <CLONE_PATH>/memory
memory cache status --base <CLONE_PATH>/memory
```

### 6.10 Git Commit

If all tests pass:

```bash
cd <CLONE_PATH> && \
  git pull --rebase && \
  git add memory/<AGENT_ID>/ && \
  git commit -m "memory(<AGENT_ID>): init directory structure and first entry" && \
  git push origin main
```

Always `git pull --rebase` before push -- the repo has concurrent contributors.

---

## Post-Integration

After all tests pass:

1. **Restart your session.** Hooks only go live after a session restart.
2. **First compaction is the acid test.** The PreCompact hook will save context and the SessionStart hook will restore it. Watch for errors in the hook output.
3. **Clean up or keep the test entry.** The integration-test entry can serve as a record of adoption, or remove it with `rm <CLONE_PATH>/memory/<AGENT_ID>/efforts/integration-test.md`.

---

## Frontmatter Schema Reference

Every memory entry requires YAML frontmatter between `---` delimiters.

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
| `related` | list[str] | any | Wiki-links to related entries (e.g., `["[[topic-name]]"]`) |
| `confidence` | enum | `established`, `working`, `exploratory` | Knowledge maturity level |
| `category` | enum | built-ins plus configured categories | Entry type (matches directory name) |
| `status` | enum | `active`, `archived`, `draft` | Lifecycle state |
| `supersedes` | string | wiki-link | Entry this replaces |

The built-in categories are `atlas`, `efforts`, `calendar`, and `moc`. Add
project-specific categories with `categories:` or `category_types:` in
`~/.config/agent-memory/config.yaml`, repo-local `.agent-memory.yaml`,
`.agent-memory.yml`, `.agent-memory/config.yaml`, or the comma-separated
`AGENT_MEMORY_CATEGORIES` environment variable. Category names must match
`^[a-z0-9][a-z0-9_-]*$`.

### Section Description Convention

Every `##` section header must be followed by a plain prose description line as its first non-empty content:

```markdown
## Delivery Guarantees
At-least-once delivery with idempotent message IDs.

## Bad Example
- This starts with a list marker    <-- INVALID, will fail validation
```

Valid description lines are non-empty and do not start with `#`, backtick, `|`, `-`, or `*`.

---

## CLI Quick Reference

### Progressive Disclosure (read path)

```bash
# Level 1: List entries with descriptions
memory ls <CLONE_PATH>/memory/<AGENT_ID>/atlas

# Level 2: Show table of contents for a file
memory toc <CLONE_PATH>/memory/<AGENT_ID>/atlas/some-entry.md

# Level 3: Read a specific section (case-insensitive partial match)
memory section <CLONE_PATH>/memory/<AGENT_ID>/atlas/some-entry.md "delivery"
```

### Search

```bash
# BM25 relevance-ranked search
memory search "query terms" --scope own --base <CLONE_PATH>/memory

# Ripgrep exact/regex search
memory grep "exact text" -F --scope own --base <CLONE_PATH>/memory

# Search by tag
memory grep --tag deployment --base <CLONE_PATH>/memory

# Search wiki-link references
memory grep --links-to topic-name --base <CLONE_PATH>/memory
```

### Write

```bash
# Create a new entry
memory new "entry-name" \
  -d "Description of the entry" \
  -c atlas \
  --confidence established \
  -t "tag1,tag2" \
  -b "## Section Title
Section content here." \
  --base <CLONE_PATH>/memory

# Update an existing entry
memory update <CLONE_PATH>/memory/<AGENT_ID>/atlas/entry-name.md \
  --add-tags "new-tag" \
  --confidence established \
  --base <CLONE_PATH>/memory
```

### Maintenance

```bash
# Validate entries
memory validate <CLONE_PATH>/memory/<AGENT_ID>/

# Sync with remote
memory sync --base <CLONE_PATH>/memory

# Build/check/clear cache
memory cache build --base <CLONE_PATH>/memory
memory cache status --base <CLONE_PATH>/memory
memory cache clear --base <CLONE_PATH>/memory
```

### JSON Output

All commands support `--json-output` for machine-readable output. Place the flag before the subcommand:

```bash
memory --json-output ls <CLONE_PATH>/memory/<AGENT_ID>/atlas
memory --json-output search "query" --base <CLONE_PATH>/memory
```

---

## Troubleshooting and Gotchas

These are the hard-won lessons from three integration experiences. Each was encountered in production and cost debugging time.

### CRITICAL: PS1 Guard Blocks Non-Interactive Shells

**Symptom**: `echo $AGENT_ID` shows the value in an interactive terminal but is empty when run via Claude Code's Bash tool or hooks.

**Cause**: `~/.bashrc` contains `[ -z "$PS1" ] && return` near the top. Exports placed below this line are invisible to non-interactive shells.

**Fix**: Move all agent-memory exports ABOVE the PS1 guard line. This is the single most likely failure point for new integrations.

### Ubuntu 24.04: No System pip (PEP 668)

**Symptom**: `pipx install` fails because pipx is not installed. `pip install pipx` fails because pip is not available on system Python.

**Fix**: Install the full chain:
```bash
apt install python3-pip -y
pip install --break-system-packages pipx
pipx ensurepath
source ~/.bashrc
```

Note: `pip uninstall` also requires `--break-system-packages` on Ubuntu 24.04.

### Hook Dry-Run: Use Heredoc, Not Echo Pipe

**Symptom**: `echo '{"..."}' | /path/to/hook.sh` fails with exit 127 (`command not found`).

**Cause**: In Claude Code's non-interactive Bash environment, piping echo to a script can produce unexpected failures.

**Fix**: Always use heredoc syntax for hook testing:
```bash
AGENT_ID=<AGENT_ID> \
  AGENT_MEMORY_PATH=<CLONE_PATH>/memory \
  <CLONE_PATH>/hooks/pre-compact-save.sh <<'HOOKEOF'
{"session_id":"test","transcript_path":"/dev/null","trigger":"manual","cwd":"/tmp","hook_event_name":"PreCompact"}
HOOKEOF
```

### BM25 Search Requires Section Headers

**Symptom**: `memory search` returns zero results on entries that clearly contain the search terms.

**Cause**: BM25 indexes content under `##` section headers. Entries with only frontmatter and no body sections produce nothing to index.

**Fix**: Ensure every searchable entry has body content organized under `##` headers. Even `--field description` searches section descriptions (the prose line after `##`), not the frontmatter `description` field.

### Inconsistent `--base` Flag Usage

**Symptom**: `memory validate --base <path>` or `memory ls --base <path>` fails with an unrecognized option error.

**Cause**: Not all commands accept `--base`. The flag convention is inconsistent across the CLI.

**Commands that use positional path** (no `--base`):
- `memory ls <path>`
- `memory toc <path>`
- `memory section <path> "title"`
- `memory validate <path>`

**Commands that use `--base`**:
- `memory search --base <path>`
- `memory grep --base <path>`
- `memory new --base <path>`
- `memory update --base <path>`
- `memory init --base <path>`
- `memory cache build --base <path>`

### Section Descriptions Must Be Prose

**Symptom**: `memory validate` fails with "Section descriptions valid" check failing.

**Cause**: The first content line after a `##` header is a list item, code block, or table instead of a prose sentence.

**Fix**: Always add a prose summary line before structured content:
```markdown
## Components Verified
All components verified working.

- CLI installed via pipx
- Hooks configured
```

NOT:
```markdown
## Components Verified
- CLI installed via pipx    <-- INVALID: list item as first line
```

### find_memory_cli in Non-Interactive Shells

**Watch item** (from Kelvin's review): After `pipx ensurepath`, verify the `memory` binary is discoverable in non-interactive shell context. The `find_memory_cli` function in `hooks/common.sh` searches `PATH`, `~/.local/bin/memory`, `/usr/local/bin/memory`, and project virtualenvs. All three integrations confirmed this works, but if hooks fail to find the CLI, check that `~/.local/bin` is in the PATH exported above the PS1 guard.

### Concurrent Contributors and Git Push

**Symptom**: `git push origin main` is rejected with "non-fast-forward" error.

**Fix**: Always pull before pushing:
```bash
git pull --rebase && git push origin main
```

### PATH Cache After pipx Install

**Symptom**: `which memory` returns nothing after `pipx install` succeeds.

**Fix**: Refresh the shell's PATH cache:
```bash
hash -r
```

Or start a new shell session.

---

## Placeholder Reference

Replace these placeholders throughout the guide with your actual values:

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `<AGENT_ID>` | Your agent identity | `my-agent` |
| `<CLONE_PATH>` | Absolute path to the code clone | `/srv/agent-memory-cli` |
| `<HOME_DIR>` | Your home directory | `/home/my-agent` |
| `<SETTINGS_PATH>` | Path to `.claude/settings.json` | `/home/my-agent/.claude/settings.json` |
| `<CLAUDE_MD_PATH>` | Path to your CLAUDE.md | `/home/my-agent/project/CLAUDE.md` |
