"""Tests for the settings API."""

import logging

import pytest

from app import main
from app.routes import settings as settings_routes
from app.services.job_queue import job_queue
from app.services.settings_service import SETTING_DEFINITIONS


@pytest.fixture
def clean_settings_env(monkeypatch):
    for name in SETTING_DEFINITIONS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def restore_logging(monkeypatch):
    yield
    monkeypatch.setattr(main.settings, "LOG_LEVEL", "INFO")
    main.configure_logging()


def test_get_settings_shape(clean_settings_env, client):
    response = client.get("/api/settings")

    assert response.status_code == 200
    values = response.json()["settings"]
    assert set(values) == set(SETTING_DEFINITIONS)
    assert values["AUDIO_TRACK_MODE"]["value"] == "preferred"
    assert values["AUDIO_TRACK_MODE"]["source"] == "default"
    assert values["DISTRIBUTED_NODE_ID"]["value"]
    assert values["DISTRIBUTED_NODE_ID"]["source"] == "stored"
    assert values["DISTRIBUTED_NODE_ID"]["restart_required"] is False


def test_get_settings_reports_environment_source(
    clean_settings_env, client, monkeypatch
):
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    values = client.get("/api/settings").json()["settings"]

    assert values["LOG_LEVEL"]["value"] == "ERROR"
    assert values["LOG_LEVEL"]["source"] == "env"


def test_put_settings_stores_values(clean_settings_env, client, restore_logging):
    response = client.put(
        "/api/settings",
        json={
            "AUDIO_TRACK_MODE": "all",
            "SUBTITLE_TRACK_MODE": "none",
            "PREFERRED_AUDIO_LANGUAGES": "ENG,de",
            "OUTPUT_FILE_MODE": "600",
            "PUID": 1000,
            "PGID": "",
            "JOB_HISTORY_RETENTION_DAYS": 3,
            "JOB_HISTORY_MAX_ROWS": 100,
            "LOG_LEVEL": "WARNING",
        },
    )

    assert response.status_code == 200
    values = response.json()["settings"]
    assert values["AUDIO_TRACK_MODE"]["value"] == "all"
    assert values["AUDIO_TRACK_MODE"]["source"] == "stored"
    assert values["PREFERRED_AUDIO_LANGUAGES"]["value"] == "eng,de"
    assert values["OUTPUT_FILE_MODE"]["value"] == "0600"
    assert values["PUID"]["value"] == 1000
    assert values["PGID"]["value"] == ""
    assert values["JOB_HISTORY_RETENTION_DAYS"]["value"] == 3
    assert values["JOB_HISTORY_MAX_ROWS"]["value"] == 100
    assert values["LOG_LEVEL"]["value"] == "WARNING"
    assert logging.getLogger().level == logging.WARNING


@pytest.mark.parametrize(
    "payload",
    [
        {"AUDIO_TRACK_MODE": "both"},
        {"SUBTITLE_TRACK_MODE": "some"},
        {"PREFERRED_AUDIO_LANGUAGES": "de,english"},
        {"OUTPUT_FILE_MODE": "999"},
        {"PUID": 70000},
        {"DISTRIBUTED_PUBLIC_URL": "ftp://node:8000"},
        {"DISTRIBUTED_LEADER_URL": "not a url"},
        {"DISTRIBUTED_PEERS": "http://node:8000,node-b"},
        {"JOB_HISTORY_RETENTION_DAYS": -1},
        {"JOB_HISTORY_MAX_ROWS": -5},
        {"LOG_LEVEL": "TRACE"},
    ],
)
def test_put_settings_rejects_invalid_values(clean_settings_env, client, payload):
    response = client.put("/api/settings", json=payload)

    assert response.status_code == 422


def test_put_settings_rejects_env_locked_field(clean_settings_env, client, monkeypatch):
    monkeypatch.setenv("AUDIO_TRACK_MODE", "all")

    response = client.put("/api/settings", json={"AUDIO_TRACK_MODE": "preferred"})

    assert response.status_code == 409
    assert "AUDIO_TRACK_MODE" in response.json()["detail"]


def test_put_cluster_setting_marks_restart_required(clean_settings_env, client):
    response = client.put("/api/settings", json={"DISTRIBUTED_NODE_NAME": "worker-a"})

    assert response.status_code == 200
    values = response.json()["settings"]
    assert values["DISTRIBUTED_NODE_NAME"]["value"] == "worker-a"
    assert values["DISTRIBUTED_NODE_NAME"]["restart_required"] is True
    assert values["JOB_HISTORY_MAX_ROWS"]["restart_required"] is False


def test_restart_refused_while_job_runs(clean_settings_env, client, monkeypatch):
    called = []
    monkeypatch.setattr(job_queue, "current_job_id", 7)
    monkeypatch.setattr(settings_routes, "exit_process", lambda: called.append(True))

    response = client.post("/api/settings/restart")

    assert response.status_code == 409
    assert called == []


def test_restart_when_idle(clean_settings_env, client, monkeypatch):
    called = []
    monkeypatch.setattr(settings_routes, "exit_process", lambda: called.append(True))

    response = client.post("/api/settings/restart")

    assert response.status_code == 200
    assert response.json()["restarting"] is True
    assert called == [True]
