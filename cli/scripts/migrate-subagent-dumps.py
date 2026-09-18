#!/usr/bin/env python3
"""Migrate subagent dump files from efforts/ to efforts/_archive/.

Moves all subagent-*.md files from efforts/ directories into the
corresponding _archive/ subdirectory. Files already in _archive/
are skipped. Creates _archive/ directories as needed.

Usage:
    python3 scripts/migrate-subagent-dumps.py [--dry-run] [--base MEMORY_DIR]

Issue: finml-sage/agent-memory#69
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def find_subagent_files(base: Path) -> list[tuple[Path, Path]]:
    """Find subagent-*.md files in efforts/ dirs and compute destinations.

    Returns list of (source, destination) tuples.
    Skips files already inside _archive/ directories.
    """
    moves: list[tuple[Path, Path]] = []

    for md_file in sorted(base.rglob("subagent-*.md")):
        # Skip files already in _archive/
        rel = md_file.relative_to(base)
        if any(p.startswith("_") for p in rel.parts):
            continue

        # Only move files that are directly inside efforts/
        if "efforts" not in rel.parts:
            continue

        efforts_idx = list(rel.parts).index("efforts")
        # The file should be a direct child of efforts/
        if len(rel.parts) != efforts_idx + 2:
            continue

        archive_dir = md_file.parent / "_archive"
        dest = archive_dir / md_file.name
        moves.append((md_file, dest))

    return moves


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate subagent dumps to _archive/"
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=Path("memory"),
        help="Base memory directory (default: memory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be moved without moving",
    )
    args = parser.parse_args()

    if not args.base.is_dir():
        print(f"Error: {args.base} is not a directory", file=sys.stderr)
        sys.exit(1)

    moves = find_subagent_files(args.base)

    if not moves:
        print("No subagent dump files found to migrate.")
        return

    # Group by agent directory for reporting
    by_agent: dict[str, int] = {}
    for src, _ in moves:
        rel = src.relative_to(args.base)
        agent = rel.parts[0] if rel.parts else "unknown"
        by_agent[agent] = by_agent.get(agent, 0) + 1

    print(f"Found {len(moves)} subagent dump files to migrate:")
    for agent, count in sorted(by_agent.items()):
        print(f"  {agent}: {count} files")

    if args.dry_run:
        print("\n--dry-run: no files moved.")
        for src, dest in moves[:10]:
            print(f"  would move: {src} -> {dest}")
        if len(moves) > 10:
            print(f"  ... and {len(moves) - 10} more")
        return

    # Create _archive/ directories and move files
    created_dirs: set[Path] = set()
    moved = 0
    errors = 0

    for src, dest in moves:
        archive_dir = dest.parent
        if archive_dir not in created_dirs:
            archive_dir.mkdir(parents=True, exist_ok=True)
            created_dirs.add(archive_dir)

        try:
            shutil.move(str(src), str(dest))
            moved += 1
        except OSError as e:
            print(f"  Error moving {src}: {e}", file=sys.stderr)
            errors += 1

    print(f"\nMigration complete: {moved} moved, {errors} errors")
    for d in sorted(created_dirs):
        print(f"  Created: {d}")


if __name__ == "__main__":
    main()
