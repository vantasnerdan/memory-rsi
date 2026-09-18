#!/usr/bin/env sh
# One-command installer for memory-rsi.
#
#   curl -fsSL https://raw.githubusercontent.com/vantasnerdan/memory-rsi/main/scripts/install.sh | sh
#   curl -fsSL https://raw.githubusercontent.com/vantasnerdan/memory-rsi/main/scripts/install.sh | sh -s -- my-profile
#
# Or from a checkout:
#
#   ./scripts/install.sh [profile]        # profile defaults to "web"
#
# What it does, idempotently:
#   1. Downloads the repo to a temp dir when run via curl (no clone needed).
#   2. Installs the vendored agent-memory CLI with pipx (falls back to
#      `pip install --user`, then a dedicated venv + shim).
#   3. Adds the repository as a plugin dependency of the given dsh profile
#      (`dsh plugin --profile <p> add <repo>`).
#   4. Registers the bundle in the profile's dsh.profile so it loads at boot.
set -eu

profile="${1:-web}"
bundle="memory-rsi"
repo_slug="${MRSI_REPO_SLUG:-vantasnerdan/memory-rsi}"
dsh_home="${DSH_HOME:-$HOME/.dsh}"
profile_dir="$dsh_home/profiles/$profile"

say() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# ── 0. Resolve the repository directory ─────────────────────────────────────
# When the script is piped from curl, $0 has no usable path and there is no
# checkout; fetch the branch tarball into a temp dir instead. A local clone
# is used as-is so `dsh plugin add` can point at it.
script_dir="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
if [ -n "$script_dir" ] && [ -f "$script_dir/../cli/pyproject.toml" ]; then
    repo_root="$(cd "$script_dir/.." && pwd)"
else
    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 \
        || die "need curl or wget to download the repository"
    # Install to a persistent location: pnpm links the profile's plugin
    # dependency back to this directory, so it must outlive this script.
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

command -v dsh >/dev/null 2>&1 || die "dsh not found on PATH. Install the DeepSeek Harness first."

# ── 1. Install the vendored CLI ──────────────────────────────────────────────
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
        *) say "WARNING: add $HOME/.local/bin to your PATH so 'memory' resolves" ;;
    esac
fi

# ── 2. Initialize the memory repo if it does not exist yet ──────────────────
# The CLI's default base is ~/agent-memory/memory. Writes need a git repo
# with at least one commit, and search needs the BM25 cache built.
base_dir="${AGENT_MEMORY_PATH:-$HOME/agent-memory/memory}"
if [ ! -d "$base_dir" ]; then
    say "Initializing memory repo at $base_dir"
    mkdir -p "$base_dir"
    git -C "$base_dir" init -q -b main 2>/dev/null || git -C "$base_dir" init -q
    git -C "$base_dir" commit -q --allow-empty -m "initialize memory repo" ||
        say "WARNING: could not create the initial commit (set git user.name/user.email); writes will fail until you do"
    memory cache build >/dev/null 2>&1 || true
elif ! git -C "$base_dir" rev-parse HEAD >/dev/null 2>&1; then
    say "Memory repo has no commits; creating the initial commit"
    git -C "$base_dir" commit -q --allow-empty -m "initialize memory repo" ||
        say "WARNING: could not create the initial commit (set git user.name/user.email); writes will fail until you do"
fi

# ── 3. Add the plugin to the profile ────────────────────────────────────────
say "Adding $bundle to dsh profile '$profile'"
dsh plugin --profile "$profile" add "$repo_root"

# ── 4. Register the bundle in dsh.profile ───────────────────────────────────
manifest="$profile_dir/dsh.profile"
[ -f "$manifest" ] || die "profile manifest not found: $manifest (boot the profile once to initialize it)"

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

say "Done. Restart the profile to load the memory tools:"
echo "    dsh --profile $profile"
echo
echo "Verify:"
echo "    dsh --profile $profile --dump-config | grep memory-rsi"
