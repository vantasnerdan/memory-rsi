"""SQLite index cache for BM25 search.

Caches file metadata, parsed frontmatter, pre-tokenized sections,
and document-frequency statistics in SQLite. Uses mtime_ns + size_bytes
for fast staleness detection, with optional SHA-256 verification.

Designed for 100k files: WAL mode, indexed queries, batch transactions.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from agent_memory.parser import parse_frontmatter, parse_sections
from agent_memory.tokenizer import tokenize

SCHEMA_VERSION = 1

_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id         INTEGER PRIMARY KEY,
    path       TEXT    NOT NULL UNIQUE,
    mtime_ns   INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    content_hash TEXT,
    frontmatter_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sections (
    id            INTEGER PRIMARY KEY,
    file_id       INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    title         TEXT    NOT NULL,
    description   TEXT    NOT NULL DEFAULT '',
    content       TEXT    NOT NULL DEFAULT '',
    tokens_json   TEXT    NOT NULL,
    token_count   INTEGER NOT NULL,
    section_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS global_stats (
    total_sections INTEGER NOT NULL DEFAULT 0,
    avgdl          REAL    NOT NULL DEFAULT 0.0,
    last_rebuilt   REAL    NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS doc_freq (
    term      TEXT    NOT NULL UNIQUE,
    doc_count INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_sections_file_id ON sections(file_id);
CREATE INDEX IF NOT EXISTS idx_doc_freq_term ON doc_freq(term);
"""


@dataclass
class CacheStats:
    """Summary statistics for the index cache."""

    file_count: int
    section_count: int
    term_count: int
    avgdl: float
    last_rebuilt: float
    db_size_bytes: int


@dataclass
class CachedSection:
    """A section loaded from cache with its file context."""

    file_path: str
    section_title: str
    section_description: str
    section_content: str
    frontmatter_json: str
    tokens: list[str]
    token_count: int


class IndexCache:
    """SQLite-backed index cache for BM25 search.

    Opens a SQLite database in WAL mode for concurrent read access.
    Call refresh() to scan for changed files, then get_documents()
    to load cached sections for BM25 scoring.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def db_path(self) -> Path:
        return self._db_path

    def open(self) -> None:
        """Open the database connection and ensure schema exists."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path),
            isolation_level=None,
        )
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> IndexCache:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError(
                "IndexCache not open. Call open() or use as context manager."
            )
        return self._conn

    def _ensure_schema(self) -> None:
        """Create tables if missing, migrate if version differs."""
        conn = self._get_conn()
        conn.executescript(_CREATE_SCHEMA)
        row = conn.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
            conn.execute(
                "INSERT INTO global_stats "
                "(total_sections, avgdl, last_rebuilt) "
                "VALUES (0, 0.0, 0.0)"
            )
        elif row[0] != SCHEMA_VERSION:
            self._migrate(row[0])

    def _migrate(self, from_version: int) -> None:
        """Handle schema migrations. Currently rebuilds from scratch."""
        conn = self._get_conn()
        conn.executescript(
            "DROP TABLE IF EXISTS doc_freq;"
            "DROP TABLE IF EXISTS global_stats;"
            "DROP TABLE IF EXISTS sections;"
            "DROP TABLE IF EXISTS files;"
            "DROP TABLE IF EXISTS schema_version;"
        )
        conn.executescript(_CREATE_SCHEMA)
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
        conn.execute(
            "INSERT INTO global_stats "
            "(total_sections, avgdl, last_rebuilt) "
            "VALUES (0, 0.0, 0.0)"
        )

    def refresh(
        self,
        base_path: Path,
        verify_hash: bool = False,
    ) -> int:
        """Scan .md files and update cache for changed/new/deleted files.

        Returns the number of files that were re-parsed.
        """
        conn = self._get_conn()

        current_files: dict[str, os.stat_result] = {}
        for md_file in sorted(base_path.rglob("*.md")):
            if any(p.startswith("_") for p in md_file.relative_to(base_path).parts):
                continue
            try:
                st = md_file.stat()
                rel = str(md_file.relative_to(base_path))
                current_files[rel] = st
            except OSError:
                continue

        cached: dict[str, tuple[int, int, int, str | None]] = {}
        for row in conn.execute(
            "SELECT id, path, mtime_ns, size_bytes, content_hash "
            "FROM files"
        ):
            cached[row[1]] = (row[0], row[2], row[3], row[4])

        stale_paths: list[str] = []
        new_paths: list[str] = []
        deleted_ids: list[int] = []

        for path, (file_id, _, _, _) in cached.items():
            if path not in current_files:
                deleted_ids.append(file_id)

        for path, st in current_files.items():
            mtime_ns = st.st_mtime_ns
            size_bytes = st.st_size
            if path not in cached:
                new_paths.append(path)
            else:
                _, cached_mtime, cached_size, cached_hash = cached[path]
                if mtime_ns != cached_mtime or size_bytes != cached_size:
                    if verify_hash:
                        content_hash = _hash_file(base_path / path)
                        if content_hash == cached_hash:
                            conn.execute(
                                "UPDATE files SET mtime_ns=?, size_bytes=? "
                                "WHERE path=?",
                                (mtime_ns, size_bytes, path),
                            )
                            continue
                    stale_paths.append(path)

        changed_count = len(new_paths) + len(stale_paths)
        if not changed_count and not deleted_ids:
            return 0

        conn.execute("BEGIN")
        try:
            if deleted_ids:
                placeholders = ",".join("?" * len(deleted_ids))
                conn.execute(
                    f"DELETE FROM files WHERE id IN ({placeholders})",
                    deleted_ids,
                )

            if stale_paths:
                for path in stale_paths:
                    file_id = cached[path][0]
                    conn.execute(
                        "DELETE FROM files WHERE id=?", (file_id,)
                    )

            for path in new_paths + stale_paths:
                self._index_file(conn, base_path, path, verify_hash)

            self._rebuild_df(conn)

            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        return changed_count + len(deleted_ids)

    def _index_file(
        self,
        conn: sqlite3.Connection,
        base_path: Path,
        rel_path: str,
        verify_hash: bool,
    ) -> None:
        """Parse a single file and insert into cache."""
        full_path = base_path / rel_path
        try:
            text = full_path.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(text)
        except (ValueError, OSError):
            return

        st = full_path.stat()
        content_hash = _hash_file(full_path) if verify_hash else None

        fm_json = json.dumps(fm.raw, default=_json_default)

        conn.execute(
            "INSERT INTO files (path, mtime_ns, size_bytes, "
            "content_hash, frontmatter_json) VALUES (?, ?, ?, ?, ?)",
            (
                rel_path,
                st.st_mtime_ns,
                st.st_size,
                content_hash,
                fm_json,
            ),
        )
        file_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        for order, section in enumerate(parse_sections(body)):
            combined = (
                f"{section.title} {section.description} "
                f"{section.content}"
            )
            tokens = tokenize(combined)
            tokens_json = json.dumps(tokens)
            conn.execute(
                "INSERT INTO sections "
                "(file_id, title, description, content, "
                "tokens_json, token_count, section_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    file_id,
                    section.title,
                    section.description,
                    section.content,
                    tokens_json,
                    len(tokens),
                    order,
                ),
            )

    def _rebuild_df(self, conn: sqlite3.Connection) -> None:
        """Rebuild document frequency table and global stats."""
        conn.execute("DELETE FROM doc_freq")

        df: dict[str, int] = {}
        total_tokens = 0
        section_count = 0

        for (tokens_json, token_count) in conn.execute(
            "SELECT tokens_json, token_count FROM sections"
        ):
            section_count += 1
            total_tokens += token_count
            seen: set[str] = set()
            for token in json.loads(tokens_json):
                if token not in seen:
                    df[token] = df.get(token, 0) + 1
                    seen.add(token)

        if df:
            conn.executemany(
                "INSERT INTO doc_freq (term, doc_count) "
                "VALUES (?, ?)",
                df.items(),
            )

        avgdl = (
            total_tokens / section_count if section_count else 0.0
        )
        conn.execute(
            "UPDATE global_stats "
            "SET total_sections=?, avgdl=?, last_rebuilt=?",
            (section_count, avgdl, time.time()),
        )

    def get_documents(
        self,
        scope_prefix: str | None = None,
    ) -> list[CachedSection]:
        """Load cached sections, optionally filtered by path prefix."""
        conn = self._get_conn()

        if scope_prefix:
            rows = conn.execute(
                "SELECT f.path, s.title, s.description, s.content, "
                "f.frontmatter_json, s.tokens_json, s.token_count "
                "FROM sections s "
                "JOIN files f ON s.file_id = f.id "
                "WHERE f.path LIKE ? "
                "ORDER BY f.path, s.section_order",
                (scope_prefix + "%",),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT f.path, s.title, s.description, s.content, "
                "f.frontmatter_json, s.tokens_json, s.token_count "
                "FROM sections s "
                "JOIN files f ON s.file_id = f.id "
                "ORDER BY f.path, s.section_order"
            ).fetchall()

        return [
            CachedSection(
                file_path=row[0],
                section_title=row[1],
                section_description=row[2],
                section_content=row[3],
                frontmatter_json=row[4],
                tokens=json.loads(row[5]),
                token_count=row[6],
            )
            for row in rows
        ]

    def get_global_stats(self) -> CacheStats:
        """Return summary statistics for the cache."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT total_sections, avgdl, last_rebuilt "
            "FROM global_stats"
        ).fetchone()
        file_count = conn.execute(
            "SELECT COUNT(*) FROM files"
        ).fetchone()[0]
        term_count = conn.execute(
            "SELECT COUNT(*) FROM doc_freq"
        ).fetchone()[0]
        db_size = (
            self._db_path.stat().st_size
            if self._db_path.exists()
            else 0
        )
        return CacheStats(
            file_count=file_count,
            section_count=row[0],
            term_count=term_count,
            avgdl=row[1],
            last_rebuilt=row[2],
            db_size_bytes=db_size,
        )

    def clear(self) -> None:
        """Delete the cache database file."""
        self.close()
        if self._db_path.exists():
            self._db_path.unlink()


def resolve_cache_path(base_path: Path) -> Path:
    """Determine cache database path from env or default."""
    env_path = os.environ.get("AGENT_MEMORY_CACHE_PATH", "")
    if env_path:
        return Path(env_path)
    return base_path / ".agent-memory-cache" / "index.db"


def _hash_file(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_default(obj: object) -> object:
    """JSON serializer for objects not natively serializable."""
    import datetime

    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    raise TypeError(
        f"Object of type {type(obj).__name__} "
        "is not JSON serializable"
    )
