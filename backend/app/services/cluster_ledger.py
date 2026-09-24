"""Cluster queue ledger on storage shared by every node.

The leader mirrors its queue here after every change, so a node that takes
over, even after being offline or alone, starts from the queue the cluster
last agreed on instead of its own possibly stale database.
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

LEDGER_FILENAME = "queue.json"


def ledger_path() -> Path:
    state_dir = settings.DISTRIBUTED_STATE_DIR.strip() or os.path.join(
        settings.SOURCE_MOUNT, ".archive-video-av1"
    )
    return Path(state_dir) / LEDGER_FILENAME


def _read() -> Optional[dict]:
    try:
        with open(ledger_path(), encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None


def _write(ledger: dict) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(ledger, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


async def read_ledger() -> Optional[dict]:
    return await asyncio.to_thread(_read)


async def write_ledger(ledger: dict) -> None:
    await asyncio.to_thread(_write, ledger)
