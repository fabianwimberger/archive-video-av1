"""Tests for ConversionService.convert_file edge paths."""

import asyncio
import os
from unittest.mock import AsyncMock

import pytest

from .conftest import VIDEO_ROOT
from app.services.conversion_service import ConversionService


VALID_SETTINGS = {
    "crf": 26,
    "encoder_preset": 4,
    "svt_params": "",
    "audio_bitrate": "96k",
}


@pytest.mark.asyncio
async def test_convert_file_rejects_wrong_output_path():
    source = VIDEO_ROOT / "reject.mkv"
    source.write_bytes(b"source")
    service = ConversionService()

    with pytest.raises(ValueError, match="Invalid conversion output path"):
        await service.convert_file(
            1,
            str(source),
            str(VIDEO_ROOT / "somewhere_else.mkv"),
            dict(VALID_SETTINGS),
            AsyncMock(),
        )


class EmptyStdout:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class FakeProcess:
    def __init__(self, communicate_hangs: bool = False):
        self.pid = 12345
        self.returncode = 0
        self.stdout = EmptyStdout()
        self.communicate_hangs = communicate_hangs
        self.communicate_calls = 0

    def wait(self):
        return asyncio.sleep(0, result=0)

    async def communicate(self):
        self.communicate_calls += 1
        if self.communicate_hangs and self.communicate_calls == 1:
            await asyncio.sleep(3600)
        return b"", b""


@pytest.mark.asyncio
async def test_convert_file_tolerates_already_dead_process_group(monkeypatch):
    source = VIDEO_ROOT / "dead.mkv"
    source.write_bytes(b"source")
    service = ConversionService()

    process = FakeProcess()

    async def fake_exec(*_args, **_kwargs):
        return process

    def dead_killpg(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(os, "killpg", dead_killpg)

    success, _log = await service.convert_file(
        1,
        str(source),
        str(VIDEO_ROOT / "dead_conv.mkv"),
        dict(VALID_SETTINGS),
        AsyncMock(),
    )

    assert success is True
    assert process.communicate_calls == 1


@pytest.mark.asyncio
async def test_convert_file_kills_stuck_process_on_timeout(monkeypatch):
    source = VIDEO_ROOT / "stuck.mkv"
    source.write_bytes(b"source")
    service = ConversionService()

    process = FakeProcess(communicate_hangs=True)

    async def fake_exec(*_args, **_kwargs):
        return process

    def dead_killpg(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(os, "killpg", dead_killpg)

    success, _log = await service.convert_file(
        1,
        str(source),
        str(VIDEO_ROOT / "stuck_conv.mkv"),
        dict(VALID_SETTINGS),
        AsyncMock(),
    )

    assert success is True
    assert process.communicate_calls == 2
