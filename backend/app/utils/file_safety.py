import errno
import fcntl
import os
import struct
from contextlib import contextmanager, suppress
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
    # Holders unlink before unlocking; nlink also catches stale NFS dentries.
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
    # OFD, not flock(): over NFS flock() becomes a server-side POSIX lock,
    # which never conflicts with flock() on the server's own disk.
    lock_path = output.with_name(f".{output.name}.lock")
    request = struct.pack("hhqqi", fcntl.F_WRLCK, os.SEEK_SET, 0, 0, 0)
    while True:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o666)
        try:
            # Nodes reach the share under different uids.
            with suppress(OSError):
                os.fchmod(fd, 0o666)
            try:
                fcntl.fcntl(fd, fcntl.F_OFD_SETLK, request)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EACCES):
                    raise OutputInUse("Conversion output is in use") from exc
                raise
            if _lock_is_current(fd, lock_path):
                break
        except BaseException:
            os.close(fd)
            raise
        os.close(fd)
    try:
        yield
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        finally:
            os.close(fd)
