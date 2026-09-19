import fcntl
import os
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


@contextmanager
def output_lock(output: Path) -> Iterator[None]:
    # Keep the inode in place so every process locks the same file.
    lock_path = output.with_name(f".{output.name}.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o666)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Conversion output is in use") from exc
        yield
    finally:
        os.close(fd)
