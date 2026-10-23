"""Tests for the effective settings service."""

import logging
import socket

import pytest
from sqlalchemy import select

from app import main
from app.models.app_state import AppState
from app.services.settings_service import (
    SETTING_DEFINITIONS,
    SettingsLockedError,
    settings_service,
)


@pytest.fixture
def clean_settings_env(monkeypatch):
    for name in SETTING_DEFINITIONS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def restore_logging(monkeypatch):
    yield
    monkeypatch.setattr(main.settings, "LOG_LEVEL", "INFO")
    main.configure_logging()


@pytest.mark.asyncio
async def test_precedence_default_then_stored_then_env(
    db_session, clean_settings_env, monkeypatch
):
    values = await settings_service.get_all()
    assert values["AUDIO_TRACK_MODE"] == {
        "value": "preferred",
        "source": "default",
        "restart_required": False,
    }

    await settings_service.update({"AUDIO_TRACK_MODE": "all"})
    values = await settings_service.get_all()
    assert values["AUDIO_TRACK_MODE"]["value"] == "all"
    assert values["AUDIO_TRACK_MODE"]["source"] == "stored"

    monkeypatch.setenv("AUDIO_TRACK_MODE", "preferred")
    values = await settings_service.get_all()
    assert values["AUDIO_TRACK_MODE"]["value"] == "preferred"
    assert values["AUDIO_TRACK_MODE"]["source"] == "env"


@pytest.mark.asyncio
async def test_env_locked_field_rejects_update(
    db_session, clean_settings_env, monkeypatch
):
    monkeypatch.setenv("AUDIO_TRACK_MODE", "all")

    with pytest.raises(SettingsLockedError) as excinfo:
        await settings_service.update({"AUDIO_TRACK_MODE": "preferred"})

    assert excinfo.value.names == ["AUDIO_TRACK_MODE"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"AUDIO_TRACK_MODE": "both"},
        {"SUBTITLE_TRACK_MODE": "some"},
        {"PREFERRED_AUDIO_LANGUAGES": "de,english"},
        {"PREFERRED_SUBTITLE_LANGUAGES": ""},
        {"OUTPUT_FILE_MODE": "999"},
        {"OUTPUT_FILE_MODE": "07777"},
        {"PUID": 70000},
        {"PGID": -1},
        {"DISTRIBUTED_PUBLIC_URL": "ftp://node:8000"},
        {"DISTRIBUTED_PEERS": "http://node:8000,node-b"},
        {"JOB_HISTORY_RETENTION_DAYS": -1},
        {"LOG_LEVEL": "TRACE"},
    ],
)
async def test_update_validates_values(db_session, clean_settings_env, changes):
    with pytest.raises(ValueError):
        await settings_service.update(changes)


@pytest.mark.asyncio
async def test_update_normalizes_values(db_session, clean_settings_env):
    await settings_service.update(
        {
            "PREFERRED_AUDIO_LANGUAGES": "ENG, de",
            "OUTPUT_FILE_MODE": "600",
            "DISTRIBUTED_PUBLIC_URL": "http://node-a:8000/",
            "DISTRIBUTED_PEERS": "http://node-b:8000/, http://node-c:8000",
            "PUID": "",
        }
    )

    values = await settings_service.get_all()
    assert values["PREFERRED_AUDIO_LANGUAGES"]["value"] == "eng,de"
    assert values["OUTPUT_FILE_MODE"]["value"] == "0600"
    assert values["DISTRIBUTED_PUBLIC_URL"]["value"] == "http://node-a:8000"
    assert (
        values["DISTRIBUTED_PEERS"]["value"] == "http://node-b:8000,http://node-c:8000"
    )
    assert values["PUID"]["value"] == ""


@pytest.mark.asyncio
async def test_node_id_is_generated_once_and_persisted(db_session, clean_settings_env):
    first = await settings_service.ensure_node_id()
    second = await settings_service.ensure_node_id()

    assert first == second
    assert first == socket.gethostname()

    result = await db_session.execute(
        select(AppState).where(AppState.key == "setting.DISTRIBUTED_NODE_ID")
    )
    assert result.scalar_one().value == first


@pytest.mark.asyncio
async def test_node_id_env_overrides_stored_value(
    db_session, clean_settings_env, monkeypatch
):
    stored = await settings_service.ensure_node_id()
    monkeypatch.setenv("DISTRIBUTED_NODE_ID", "node-a")

    assert await settings_service.ensure_node_id() == "node-a"

    result = await db_session.execute(
        select(AppState).where(AppState.key == "setting.DISTRIBUTED_NODE_ID")
    )
    assert result.scalar_one().value == stored


@pytest.mark.asyncio
async def test_env_peer_list_is_normalized(db_session, clean_settings_env, monkeypatch):
    monkeypatch.setenv("DISTRIBUTED_PEERS", "http://node-b:8000/, http://node-c:8000")

    values = await settings_service.get_all()

    assert values["DISTRIBUTED_PEERS"]["source"] == "env"
    assert (
        values["DISTRIBUTED_PEERS"]["value"] == "http://node-b:8000,http://node-c:8000"
    )


@pytest.mark.asyncio
async def test_node_id_is_read_only(db_session, clean_settings_env):
    with pytest.raises(ValueError):
        await settings_service.update({"DISTRIBUTED_NODE_ID": "node-a"})


@pytest.mark.asyncio
async def test_cluster_change_requires_restart(db_session, clean_settings_env):
    await settings_service.apply_startup()

    await settings_service.update({"DISTRIBUTED_NODE_NAME": "worker-a"})
    values = await settings_service.get_all()

    assert values["DISTRIBUTED_NODE_NAME"]["value"] == "worker-a"
    assert values["DISTRIBUTED_NODE_NAME"]["restart_required"] is True
    assert values["JOB_HISTORY_MAX_ROWS"]["restart_required"] is False


@pytest.mark.asyncio
async def test_log_level_applies_immediately(
    db_session, clean_settings_env, restore_logging
):
    await settings_service.update({"LOG_LEVEL": "ERROR"})

    assert logging.getLogger().level == logging.ERROR
