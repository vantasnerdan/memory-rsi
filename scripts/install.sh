#!/usr/bin/env sh
# Portable memory-rsi bootstrap. All settings are derived from this user's
# environment or explicit flags, never from the plugin author's deployment.
# Usage: curl -fsSL https://raw.githubusercontent.com/vantasnerdan/memory-rsi/main/scripts/install.sh | sh -s -- --yes
set -eu

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
for tool in node npm git; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool is required; install the DeepSeek Harness and its Node.js prerequisites first."
done
node -e 'if (Number(process.versions.node.split(".")[0]) < 22) { console.error("Node.js 22+ is required"); process.exit(1); }'

repo="${MRSI_SOURCE_DIR:-}"
if [ -z "$repo" ]; then
    case "$0" in
        */install.sh)
            script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
            if [ -f "$script_dir/../bin/setup.js" ] && [ -d "$script_dir/../cli" ]; then
                repo="$(CDPATH='' cd -- "$script_dir/.." && pwd)"
            fi
            ;;
    esac
fi
if [ -z "$repo" ]; then
    command -v curl >/dev/null 2>&1 || die "curl is required to download the plugin."
    command -v tar >/dev/null 2>&1 || die "tar is required to unpack the plugin."
    slug="${MRSI_REPO_SLUG:-vantasnerdan/memory-rsi}"
    ref="${MRSI_REF:-main}"
    # Keep previous source versions intact for rollback; never rm an operator path.
    sources="${DSH_HOME:-$HOME/.dsh}/plugins/memory-rsi/sources"
    repo="$sources/release-$(date +%s)-$$"
    node --input-type=module - "$repo" <<'NODE'
import { lstatSync, mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
const target = resolve(process.argv[2]);
for (let path = target; ; path = dirname(path)) {
  try { if (lstatSync(path).isSymbolicLink()) throw new Error(`Refusing symlinked install directory: ${path}`); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (dirname(path) === path) break;
}
mkdirSync(target, { recursive: true });
NODE
    archive="$(mktemp "${TMPDIR:-/tmp}/memory-rsi-download.XXXXXX")"
    trap 'rm -f "$archive"' EXIT HUP INT TERM
    printf 'Downloading memory-rsi from %s (%s)…\n' "$slug" "$ref" >&2
    curl -fL --retry 2 "https://github.com/$slug/archive/$ref.tar.gz" -o "$archive"
    tar -xzf "$archive" -C "$repo" --strip-components=1
    rm -f "$archive"
    trap - EXIT HUP INT TERM
fi
[ -f "$repo/bin/setup.js" ] || die "Not a memory-rsi package: $repo"
printf 'Preparing plugin dependencies in %s…\n' "$repo" >&2
npm --prefix "$repo" install --omit=dev --ignore-scripts --no-audit --no-fund
exec node "$repo/bin/setup.js" "$@"
