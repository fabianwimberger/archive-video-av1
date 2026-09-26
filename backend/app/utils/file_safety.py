import errno
import fcntl
import os
import struct
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import settings


def validate_source_path(source_file: str) -> Path:
    path = Path(source_file).resolve()
    if not path.is_relative_to(Path(settings.SOURCE_MOUNT).resolve()):
        raise ValueError("Source file is outside the video mount")
    if not path.is_file() or path.suffix.lower() not in {".mkv", ".mp4"}:
        raise ValueError("Source must be an existing MKV or MP4 file")
    if path.stem.endswith("_conv"):
        raise ValueError("Converted outputs cannot be queued as sources")
    return path


class OutputInUse(ValueError):
    pass


def _lock_is_current(fd: int, lock_path: Path) -> bool:
    # The previous holder unlinks the file before releasing it, so a lock won
    # on an inode that is no longer at lock_path protects nothing. nlink is
    # checked too because NFS may still resolve lock_path from a stale dentry.
    try:
        held = os.fstat(fd)
        current = os.stat(lock_path, follow_symlinks=False)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ESTALE):
            return False
        raise
    return held.st_nlink > 0 and (held.st_dev, held.st_ino) == (
        current.st_dev,
        current.st_ino,
    )


@contextmanager
def output_lock(output: Path) -> Iterator[None]:
    # OFD locks, not flock(): the Linux NFS client maps flock() to a POSIX
    # lock on the server, which never conflicts with flock() taken on the
    # server's own filesystem, so nodes on NFS and a node writing locally
    # would not see each other's locks.
    lock_path = output.with_name(f".{output.name}.lock")
    request = struct.pack("hhqqi", fcntl.F_WRLCK, os.SEEK_SET, 0, 0, 0)
    while True:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o666)
        try:
            # Nodes reach the share under different uids.
            os.fchmod(fd, 0o666)
        except OSError:
            pass
        try:
            fcntl.fcntl(fd, fcntl.F_OFD_SETLK, request)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                raise OutputInUse("Conversion output is in use") from exc
            raise
        if _lock_is_current(fd, lock_path):
            break
        os.close(fd)
    try:
        yield
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        finally:
            os.close(fd)
