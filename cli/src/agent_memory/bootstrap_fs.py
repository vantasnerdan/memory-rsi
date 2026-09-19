"""Conservative filesystem primitives shared by first-run and import operations.

Cooperating operations use advisory locks. Hostile concurrent directory replacement
is not supported; all observed symlinks, special files and hard links are refused.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import stat
from pathlib import Path


class BootstrapError(ValueError):
    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code


def safe_path(value: str | Path, *, kind: str | None = None) -> Path:
    path = Path(value).expanduser()
    if ".." in path.parts:
        raise BootstrapError(f"Path traversal is not allowed: {path}", "unsafe_path")
    path = path.absolute()
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise BootstrapError(f"Symlink is not allowed: {item}", "unsafe_path")
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise BootstrapError(f"Special file is not allowed: {item}", "unsafe_path")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise BootstrapError(f"Hard-linked file is not allowed: {item}", "unsafe_path")
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise BootstrapError(f"Parent is not a directory: {item}", "unsafe_path")
    if path.exists() and kind == "directory" and not path.is_dir():
        raise BootstrapError(f"Expected directory: {path}", "unsafe_path")
    if path.exists() and kind == "file" and not path.is_file():
        raise BootstrapError(f"Expected regular file: {path}", "unsafe_path")
    return path


def read_bytes(path: Path, maximum: int = 4 * 1024 * 1024) -> bytes:
    path = safe_path(path, kind="file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
            raise BootstrapError(f"Expected bounded regular file: {path}", "unsafe_path")
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise BootstrapError(f"File exceeds {maximum} bytes: {path}", "size_limit")
    return data


def revision(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def create_bytes(path: Path, data: bytes) -> bool:
    """Create only; never replace existing destination bytes."""
    path = safe_path(path, kind="file")
    safe_path(path.parent, kind="directory").mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink()
        raise
    return True


@contextlib.contextmanager
def locked(base: Path):
    safe_path(base, kind="directory").mkdir(parents=True, exist_ok=True)
    lock = safe_path(base / ".bootstrap.lock", kind="file")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BootstrapError("Unsafe bootstrap lock", "unsafe_path")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)
