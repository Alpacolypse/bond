"""Blobs in a local directory, for development, tests and single-machine stores."""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path


class LocalBlobs:
    """Blobs as files under root, each named by its hash, in a subdirectory by its first two characters."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, content_hash: str) -> Path:
        return self.root / content_hash[:2] / content_hash

    def put(self, content_hash: str, data: bytes) -> None:
        path = self.path(content_hash)
        if path.exists():
            return
        path.parent.mkdir(exist_ok=True)
        _write_durably(path, data)

    def get(self, content_hash: str) -> bytes | None:
        # Blobs are never deleted, so one that is there stays there.
        path = self.path(content_hash)
        return _read(path) if path.is_file() else None


# On Windows, opening a file that was just renamed into place can be refused for a moment while another handle on it
# (another writer's rename, a virus scanner) closes. A blob never changes once there, so reading it again is safe.
READ_WAITS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5) if os.name == "nt" else ()  # seconds, about 0.9 in all


def _read(path: Path, waits: Sequence[float] = READ_WAITS) -> bytes:
    """A blob's bytes, read again after each wait while the OS refuses it, and once more after the last."""
    for wait in waits:
        try:
            return path.read_bytes()
        except PermissionError:
            time.sleep(wait)
    return path.read_bytes()


def _write_durably(path: Path, data: bytes, sync_dir: bool = os.name != "nt") -> None:
    """Write path atomically, through a temporary file, and fsync it and (where the OS allows) its directory.

    Windows can't open a directory as a file, so there it can't be fsynced: the rename is as durable as the file
    system makes it (NTFS journals it), and trying would fail every write.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            # On Windows a reader of a file being replaced is refused, so a blob is never replaced there: rename
            # refuses an existing target. Blobs are named by their bytes, so if another writer put it in place
            # first, it holds these bytes and this write is done.
            if os.name == "nt":
                os.rename(tmp, path)
            else:
                os.replace(tmp, path)
        except (FileExistsError, PermissionError):
            if not path.is_file():
                raise
            Path(tmp).unlink(missing_ok=True)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    if not sync_dir:
        return
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
