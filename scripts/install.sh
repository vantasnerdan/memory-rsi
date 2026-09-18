#!/usr/bin/env sh
# One-command installer for memory-rsi.
#
# Non-interactive (agent/script friendly — flags or defaults, no prompts):
#
#   curl -fsSL https://raw.githubusercontent.com/vantasnerdan/memory-rsi/main/scripts/install.sh | sh
#   curl -fsSL .../scripts/install.sh | sh -s -- --profile my-profile --agent-id dan
#
# Interactive (prompts for anything not passed as a flag):
#
#   ./scripts/install.sh
#
# Options:
#   --profile NAME    dsh profile to install into          (default: web)
#   --agent-id ID     memory author identity               (default: git user.name or $USER)
#   --base DIR        memory repo directory                (default: $AGENT_MEMORY_PATH or ~/agent-memory/memory)
#   -y, --yes         never prompt, accept all defaults
#   -h, --help        show this help
#
# What it does, idempotently:
#   1. Downloads the repo to a persistent dir when run via curl (no clone).
#   2. Installs the vendored agent-memory CLI (pipx → pip --user → venv).
#   3. Initializes the memory repo (git init + initial commit + BM25 cache)
#      when it does not exist or has no commits.
#   4. Adds the repository as a plugin dependency of the dsh profile
#      (`dsh plugin --profile <p> add <repo>`).
#   5. Registers the bundle in the profile's dsh.profile.
#   6. Writes the plugin config row (agentId, base, ...) into the profile's
#      cordis.patch.yml, merging safely with existing content.
set -eu

usage() { sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; }

profile=""
agent_id=""
base=""
assume_yes=0

while [ $# -gt 0 ]; do
    case "$1" in
        --profile)  [ $# -ge 2 ] || { echo "error: $1 needs a value" >&2; exit 1; }; profile="$2"; shift 2 ;;
        --agent-id) [ $# -ge 2 ] || { echo "error: $1 needs a value" >&2; exit 1; }; agent_id="$2"; shift 2 ;;
        --base)     [ $# -ge 2 ] || { echo "error: $1 needs a value" >&2; exit 1; }; base="$2"; shift 2 ;;
        -y|--yes)   assume_yes=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        --*)        echo "error: unknown option: $1 (see --help)" >&2; exit 1 ;;
        *) break ;;   # first positional arg = profile (back-compat)
    esac
done
if [ -z "$profile" ] && [ -n "${1:-}" ]; then profile="$1"; fi

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

# Ask only when running interactively with no value decided; sets REPLY.
ask_default() {
    if [ "$assume_yes" -eq 1 ] || [ ! -t 0 ] || [ ! -t 1 ]; then
        REPLY="$2"
        return 0
    fi
    printf '%s [%s]: ' "$1" "$2"
    IFS= read -r REPLY || REPLY="$2"
    [ -n "$REPLY" ] || REPLY="$2"
}

bundle="memory-rsi"
repo_slug="${MRSI_REPO_SLUG:-vantasnerdan/memory-rsi}"
dsh_home="${DSH_HOME:-$HOME/.dsh}"

# ── 0. Resolve settings (flags > env > prompt > defaults) ───────────────────
if [ -z "$profile" ]; then
    ask_default "dsh profile to install into" "web"
    profile="$REPLY"
fi
if [ -z "$agent_id" ]; then
    guess="$(git config --get user.name 2>/dev/null || true)"
    [ -n "$guess" ] || guess="${USER:-$(id -un)}"
    ask_default "memory author identity (agent id)" "$guess"
    agent_id="$REPLY"
fi
if [ -z "$base" ]; then
    ask_default "memory repo directory" "${AGENT_MEMORY_PATH:-$HOME/agent-memory/memory}"
    base="$REPLY"
fi
profile_dir="$dsh_home/profiles/$profile"

say "profile:   $profile"
say "agent id:  $agent_id"
say "memory:    $base"

command -v dsh >/dev/null 2>&1 || die "dsh not found on PATH. Install the DeepSeek Harness first."
command -v git >/dev/null 2>&1 || die "git not found on PATH."

# ── 1. Resolve the repository directory ─────────────────────────────────────
# When piped from curl there is no checkout; download to a persistent
# location (pnpm links the profile's dependency there, so it must outlive
# this script). A local clone is used as-is so `dsh plugin add` can point
# at it and pick up local changes.
script_dir="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
if [ -n "$script_dir" ] && [ -f "$script_dir/../cli/pyproject.toml" ]; then
    repo_root="$(cd "$script_dir/.." && pwd)"
else
    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 \
        || die "need curl or wget to download the repository"
    repo_root="$dsh_home/plugins/$bundle"
    tmpdir="$(mktemp -d)"
    trap 'rm -rf "$tmpdir"' EXIT
    say "Downloading https://github.com/$repo_slug to $repo_root"
    url="https://github.com/$repo_slug/archive/refs/heads/main.tar.gz"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" | tar -xz -C "$tmpdir"
    else
        wget -qO- "$url" | tar -xz -C "$tmpdir"
    fi
    rm -rf "$repo_root"
    mkdir -p "$(dirname "$repo_root")"
    mv "$tmpdir/$bundle-main" "$repo_root"
    say "Installing plugin runtime dependencies"
    (cd "$repo_root" && npm install --omit=dev --no-audit --no-fund --loglevel=error)
fi

# ── 2. Install the vendored CLI ─────────────────────────────────────────────
if command -v memory >/dev/null 2>&1; then
    say "memory CLI already installed: $(command -v memory)"
elif command -v pipx >/dev/null 2>&1; then
    say "Installing the agent-memory CLI with pipx"
    pipx install "$repo_root/cli"
elif python3 -m pip --version >/dev/null 2>&1; then
    say "pipx not found; installing with pip --user (pipx is recommended)"
    python3 -m pip install --user "$repo_root/cli"
else
    say "Neither pipx nor pip found; installing into $HOME/.local/memory-venv"
    python3 -m venv "$HOME/.local/memory-venv"
    "$HOME/.local/memory-venv/bin/pip" install "$repo_root/cli"
    mkdir -p "$HOME/.local/bin"
    ln -sf "$HOME/.local/memory-venv/bin/memory" "$HOME/.local/bin/memory"
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) warn "add $HOME/.local/bin to your PATH so 'memory' resolves" ;;
    esac
fi
command -v memory >/dev/null 2>&1 || warn "'memory' is not on PATH yet; open a new shell or add ~/.local/bin"

# ── 3. Initialize the memory repo ───────────────────────────────────────────
# Writes need a git repo with at least one commit; search needs the BM25
# cache. Both are set up here so the tools work on first use.
mkdir -p "$base"
if [ ! -d "$base/.git" ]; then
    say "Initializing memory repo at $base"
    git -C "$base" init -q -b main 2>/dev/null || git -C "$base" init -q
fi
if ! git -C "$base" rev-parse HEAD >/dev/null 2>&1; then
    say "Creating the memory repo's initial commit"
    # Repo-local identity so a fresh machine can commit without global config.
    git -C "$base" config user.name  "$agent_id"
    git -C "$base" config user.email "$agent_id@memory.local"
    git -C "$base" commit -q --allow-empty -m "initialize memory repo" ||
        warn "could not create the initial commit; memory writes will fail until you can commit in $base"
else
    say "Memory repo ready: $base ($(git -C "$base" rev-parse --short HEAD))"
fi
memory cache build >/dev/null 2>&1 && say "BM25 search cache built" || warn "could not build the search cache (run 'memory cache build' later)"

# ── 4. Add the plugin to the profile ────────────────────────────────────────
say "Adding $bundle to dsh profile '$profile'"
dsh plugin --profile "$profile" add "$repo_root"

# ── 5. Register the bundle in the profile manifest ──────────────────────────
# Profiles keep their manifest either as a dedicated `dsh.profile` file or
# under the `dsh.profile` key of `package.json` (the shipped templates use
# package.json). Prefer whichever exists.
manifest="$profile_dir/package.json"
[ -f "$manifest" ] || manifest="$profile_dir/dsh.profile"
[ -f "$manifest" ] || die "profile manifest not found in $profile_dir (boot the profile once to initialize it)"

if grep -q "\"$bundle\"" "$manifest"; then
    say "Bundle already listed in $manifest"
else
    say "Registering $bundle in $manifest"
    python3 - "$manifest" "$bundle" <<'PY'
import json, sys
path, bundle = sys.argv[1], sys.argv[2]
with open(path) as f:
    data = json.load(f)
bundles = data.setdefault("dsh", {}).setdefault("profile", {}).setdefault("bundles", [])
if bundle not in bundles:
    bundles.append(bundle)
with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
fi

# ── 6. Write the plugin config row into the profile's cordis.patch.yml ──────
# A patch row replaces the targeted plugin's whole config, so this row
# restates every key. A managed block is refreshed in place on re-run; an
# unmanaged row is left alone (a later row wins, so ours still applies).
patch_file="$profile_dir/cordis.patch.yml"
[ -f "$patch_file" ] || printf '# Your patch layer for this dsh profile, applied after every bundle layer:\n# a top-level YAML array of loader patch entries (id-targeted config\n# overrides, disables, and insert lists; `!!js` expressions allowed).\n[]\n' > "$patch_file"

marker_begin="# >>> memory-rsi installer (managed; safe to delete) >>>"
marker_end="# <<< memory-rsi installer <<<"
append_block=0
if grep -qF "$marker_begin" "$patch_file"; then
    say "Refreshing managed config block in $patch_file"
    python3 - "$patch_file" "$marker_begin" "$marker_end" "$agent_id" "$base" <<'PY'
import re, sys
path, mb, me, agent_id, base = sys.argv[1:6]
block = f"""{mb}
- id: memory-rsi
  config:
    memoryBin: memory
    pythonBin: python3
    base: "{base}"
    agentId: "{agent_id}"
    timeoutMs: 60000
{me}"""
with open(path) as f:
    text = f.read()
text = re.sub(re.escape(mb) + r".*?" + re.escape(me), block.strip(), text, flags=re.S)
with open(path, "w") as f:
    f.write(text)
PY
elif grep -qE "^- id: ?${bundle}$" "$patch_file"; then
    warn "an unmanaged '$bundle' row already exists in $patch_file; appending the managed block (it takes precedence)"
    append_block=1
else
    append_block=1
fi
if [ "$append_block" -eq 1 ]; then
    # The template patch layer is a literal `[]`; a real array must not keep
    # that placeholder once rows are appended, so strip it.
    python3 - "$patch_file" <<'PY'
import sys
path = sys.argv[1]
with open(path) as f:
    lines = [ln for ln in f.read().splitlines() if ln.strip() != "[]"]
with open(path, "w") as f:
    f.write("\n".join(lines).rstrip("\n") + "\n")
PY
    {
        echo ""
        echo "$marker_begin"
        echo "- id: $bundle"
        echo "  config:"
        echo "    memoryBin: memory"
        echo "    pythonBin: python3"
        echo "    base: \"$base\""
        echo "    agentId: \"$agent_id\""
        echo "    timeoutMs: 60000"
        echo "$marker_end"
    } >> "$patch_file"
    say "Wrote config block to $patch_file"
fi

say "Done. Restart the profile to load (or reload) the plugin:"
echo "    dsh --profile $profile"
echo
echo "Verify:"
echo "    dsh --profile $profile --dump-config | grep -A8 memory-rsi"
