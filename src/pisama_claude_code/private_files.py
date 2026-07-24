"""Small, cross-platform helpers for files that contain private trace data."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def _restrict_open_file(fd: int) -> None:
    """Restrict an open descriptor when the platform exposes POSIX modes."""
    fchmod = getattr(os, "fchmod", None)
    if fchmod is None:
        return
    try:
        fchmod(fd, PRIVATE_FILE_MODE)
    except OSError:
        pass


def ensure_private_dir(path: Path) -> None:
    """Create a directory and restrict it to the current user where supported."""
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    try:
        path.chmod(PRIVATE_DIR_MODE)
    except OSError:
        # Windows and unusual filesystems may not implement POSIX modes.
        pass


def make_private(path: Path) -> None:
    """Restrict an existing file to the current user where supported."""
    try:
        path.chmod(PRIVATE_FILE_MODE)
    except OSError:
        pass


def write_private_text(path: Path, content: str) -> None:
    """Atomically replace a private text file.

    The temporary file lives beside the target, so ``os.replace`` is atomic and
    concurrent hook/config writes never expose a partially written JSON file.
    """
    ensure_private_dir(path.parent)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        _restrict_open_file(fd)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary_path, path)
        make_private(path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def append_private_text(path: Path, content: str) -> None:
    """Append text while preserving user-only permissions."""
    ensure_private_dir(path.parent)
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    fd = os.open(path, flags, PRIVATE_FILE_MODE)
    try:
        _restrict_open_file(fd)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            stream.write(content)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
