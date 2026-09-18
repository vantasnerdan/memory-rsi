#!/usr/bin/env sh
# One-command installer for memory-contract-rsi.
#
#   ./scripts/install.sh [profile]        # profile defaults to "web"
#
# What it does, idempotently:
#   1. Installs the vendored agent-memory CLI with pipx (falls back to
#      `pip install --user`, then a dedicated venv + shim).
#   2. Adds this repository as a plugin dependency of the given dsh profile
#      (`dsh plugin --profile <p> add <repo>`).
#   3. Registers the bundle in the profile's dsh.profile so it loads at boot.
set -eu

profile="${1:-web}"
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
bundle="memory-contract-rsi"
dsh_home="${DSH_HOME:-$HOME/.dsh}"
profile_dir="$dsh_home/profiles/$profile"

say() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

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

# ── 2. Add the plugin to the profile ────────────────────────────────────────
say "Adding $bundle to dsh profile '$profile'"
dsh plugin --profile "$profile" add "$repo_root"

# ── 3. Register the bundle in dsh.profile ───────────────────────────────────
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
echo "    dsh --profile $profile --dump-config | grep memory-contract-rsi"
