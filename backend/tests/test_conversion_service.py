"""Tests for ConversionService.convert_file edge paths."""

import asyncio
import os
from unittest.mock import AsyncMock

import pytest

from .conftest import VIDEO_ROOT
from app.services.conversion_service import ConversionService
from app.services.settings_service import WRAPPER_SETTINGS, settings_service


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
async def test_convert_file_tolerates_already_dead_process_group(
    monkeypatch, db_session
):
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
async def test_convert_file_kills_stuck_process_on_timeout(monkeypatch, db_session):
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


async def _capture_wrapper_env(monkeypatch, service, source_name):
    source = VIDEO_ROOT / source_name
    source.write_bytes(b"source")
    captured = {}
    process = FakeProcess()

    async def fake_exec(*_args, **kwargs):
        captured.update(kwargs["env"])
        return process

    def dead_killpg(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(os, "killpg", dead_killpg)

    success, _log = await service.convert_file(
        1,
        str(source),
        str(VIDEO_ROOT / f"{source.stem}_conv.mkv"),
        dict(VALID_SETTINGS),
        AsyncMock(),
    )

    assert success is True
    return captured


@pytest.mark.asyncio
async def test_wrapper_env_uses_stored_settings(monkeypatch, db_session):
    for name in WRAPPER_SETTINGS:
        monkeypatch.delenv(name, raising=False)

    await settings_service.update(
        {
            "AUDIO_TRACK_MODE": "all",
            "PREFERRED_AUDIO_LANGUAGES": "en,de",
            "OUTPUT_FILE_MODE": "0600",
            "PUID": 1000,
            "PGID": 1001,
        }
    )

    env = await _capture_wrapper_env(monkeypatch, ConversionService(), "stored.mkv")

    assert env["AUDIO_TRACK_MODE"] == "all"
    assert env["PREFERRED_AUDIO_LANGUAGES"] == "en,de"
    assert env["OUTPUT_FILE_MODE"] == "0600"
    assert env["PUID"] == "1000"
    assert env["PGID"] == "1001"
    assert env["SUBTITLE_TRACK_MODE"] == "preferred"


@pytest.mark.asyncio
async def test_wrapper_env_lets_environment_win(monkeypatch, db_session):
    for name in WRAPPER_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OUTPUT_FILE_MODE", "0601")

    env = await _capture_wrapper_env(monkeypatch, ConversionService(), "env.mkv")

    assert env["OUTPUT_FILE_MODE"] == "0601"
