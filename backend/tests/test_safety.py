import asyncio
import errno
import json
from pathlib import Path
import os
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.models.job import Job
from app.services.conversion_service import ConversionService
from app.services.distributed import DistributedService, PeerNode
from app.services.file_service import file_service
from app.services.websocket_manager import WebSocketManager
from app.utils.file_safety import OutputInUse, output_lock
from .test_files_routes import record_conversion


@pytest.fixture
def videos(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SOURCE_MOUNT", str(tmp_path))
    monkeypatch.setattr(file_service, "source_mount", tmp_path)
    return tmp_path


def test_job_paths_and_output_collisions(seeded_client, videos, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "source.mkv"
    outside.write_bytes(b"source")
    link = videos / "link.mkv"
    link.symlink_to(outside)
    for path in (outside, link, videos / "missing.mkv"):
        response = seeded_client.post(
            "/api/jobs", json={"source_file": str(path), "preset_id": 1}
        )
        assert response.status_code == 422

    source = videos / "source.mkv"
    other = videos / "source.mp4"
    source.write_bytes(b"source")
    other.write_bytes(b"other")
    batch = seeded_client.post(
        "/api/jobs/batch", json={"files": [str(source), str(other)], "preset_id": 1}
    )
    assert batch.status_code == 409
    assert seeded_client.get("/api/jobs").json()["total"] == 0
    assert (
        seeded_client.post(
            "/api/jobs", json={"source_file": str(source), "preset_id": 1}
        ).status_code
        == 200
    )
    assert (
        seeded_client.post(
            "/api/jobs", json={"source_file": str(other), "preset_id": 1}
        ).status_code
        == 409
    )


def test_source_deletion_requires_successful_conversion(client, videos):
    source = videos / "source.mkv"
    output = videos / "source_conv.mkv"
    source.write_bytes(b"source")
    output.write_bytes(b"partial output")
    assert client.delete("/api/files", params={"path": str(source)}).status_code == 400
    assert (
        client.delete("/api/files/converted", params={"path": str(source)}).status_code
        == 400
    )
    assert source.read_bytes() == b"source"


def test_deletion_rejects_locked_or_changed_output(client, videos):
    source = videos / "source.mkv"
    output = videos / "source_conv.mkv"
    source.write_bytes(b"source")
    output.write_bytes(b"completed")
    record_conversion(source, output)
    with output_lock(output):
        assert (
            client.delete("/api/files", params={"path": str(source)}).status_code == 400
        )
        assert (
            client.delete(
                "/api/files/converted", params={"path": str(output)}
            ).status_code
            == 400
        )
    output.write_bytes(b"damaged")
    assert client.delete("/api/files", params={"path": str(source)}).status_code == 400
    assert source.exists()


def test_node_specific_job_lookup_does_not_forward(seeded_client, videos, monkeypatch):
    from app.services.distributed import distributed_service

    source = videos / "source.mkv"
    source.write_bytes(b"source")
    created = seeded_client.post(
        "/api/jobs", json={"source_file": str(source), "preset_id": 1}
    )
    job_id = created.json()["job_ids"][0]
    monkeypatch.setattr(distributed_service, "should_use_leader", lambda: True)
    forward = AsyncMock(side_effect=AssertionError("Must address the selected node"))
    monkeypatch.setattr(distributed_service, "request_leader", forward)
    response = seeded_client.get(
        f"/api/jobs/{job_id}", params={"node_id": settings.DISTRIBUTED_NODE_ID}
    )
    assert response.status_code == 200 and response.json()["source_file"] == str(source)
    assert response.json()["cluster_node_id"] == settings.DISTRIBUTED_NODE_ID
    assert (
        seeded_client.delete(
            f"/api/jobs/{job_id}", params={"node_id": settings.DISTRIBUTED_NODE_ID}
        ).status_code
        == 200
    )
    forward.assert_not_called()


def test_output_lock_excludes_other_holders_and_is_removed(videos):
    output = videos / "source_conv.mkv"
    lock_path = videos / ".source_conv.mkv.lock"
    probe = (
        "import sys; from pathlib import Path; "
        "from app.utils.file_safety import output_lock\n"
        "try:\n"
        "    with output_lock(Path(sys.argv[1])): pass\n"
        "except ValueError: sys.exit(3)\n"
    )
    with output_lock(output):
        assert lock_path.exists()
        with pytest.raises(OutputInUse):
            with output_lock(output):
                pass
        other = subprocess.run(
            [sys.executable, "-c", probe, str(output)],
            cwd=Path(__file__).resolve().parents[1],
        )
        assert other.returncode == 3
    assert not lock_path.exists()
    with pytest.raises(RuntimeError):
        with output_lock(output):
            raise RuntimeError
    assert not lock_path.exists()


@pytest.mark.parametrize("race", ["replaced", "linked", "removed"])
def test_output_lock_retries_when_lock_file_was_replaced(videos, monkeypatch, race):
    # The lock file is swapped or removed between open() and lock; "linked"
    # keeps the stale inode alive so only the inode comparison can notice.
    output = videos / "source_conv.mkv"
    lock_path = videos / ".source_conv.mkv.lock"
    real_open = os.open
    opened = []

    def racing_open(path, flags, mode=0o777):
        fd = real_open(path, flags, mode)
        opened.append(os.fstat(fd).st_ino)
        if len(opened) == 1:
            if race == "linked":
                os.link(lock_path, videos / "stale.lock")
            os.unlink(lock_path)
            if race != "removed":
                os.close(real_open(lock_path, os.O_CREAT | os.O_WRONLY, 0o666))
        return fd

    monkeypatch.setattr(os, "open", racing_open)
    with output_lock(output):
        assert lock_path.stat().st_ino == opened[-1] != opened[0]
    assert len(opened) == 2
    assert not lock_path.exists()


def _open_fds():
    return len(os.listdir("/proc/self/fd"))


def test_output_lock_tolerates_refused_chmod(videos, monkeypatch):
    def refuse(*_args):
        raise PermissionError

    monkeypatch.setattr(os, "fchmod", refuse)
    with output_lock(videos / "source_conv.mkv"):
        pass
    assert not (videos / ".source_conv.mkv.lock").exists()


@pytest.mark.parametrize("target", ["fcntl", "stat"])
def test_output_lock_errors_propagate_without_leaking(videos, monkeypatch, target):
    import fcntl

    def fail(*_args, **_kwargs):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(fcntl if target == "fcntl" else os, target, fail)
    before = _open_fds()
    with pytest.raises(OSError) as info:
        with output_lock(videos / "source_conv.mkv"):
            pass
    assert not isinstance(info.value, OutputInUse)
    assert _open_fds() == before


def test_remote_submission_is_idempotent(seeded_client, videos):
    source = videos / "source.mkv"
    source.write_bytes(b"source")
    payload = {
        "source_file": str(source),
        "preset_id": 1,
        "local_only": True,
        "cluster_job_id": "node-a:123",
        "cluster_origin_node_id": "node-a",
        "cluster_origin_job_id": 123,
    }
    first = seeded_client.post("/api/jobs", json=payload)
    second = seeded_client.post("/api/jobs", json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert (
        seeded_client.get("/api/jobs", params={"cluster": "false"}).json()["total"] == 1
    )


@pytest.mark.asyncio
async def test_uncertain_dispatch_stays_on_same_worker(db_session, monkeypatch):
    monkeypatch.setattr(settings, "DISTRIBUTED_ENABLED", True)
    service = DistributedService()
    peer = PeerNode("node-b", "Worker", "http://worker.example:8000", 0)
    service._available_peers = AsyncMock(return_value=[peer])
    service._term = 1
    service.publish_queue = AsyncMock()
    service._create_remote_job = AsyncMock(side_effect=[None, 77])
    service._get_remote_job = AsyncMock(return_value=None)
    job = Job(
        source_file="/videos/source.mkv",
        output_file="/videos/source_conv.mkv",
        settings="{}",
        status="pending",
        cluster_job_id="node-a:123",
    )
    db_session.add(job)
    await db_session.commit()

    await service.delegate_pending_jobs(None)
    await db_session.refresh(job)
    assert job.status == "processing"
    assert job.assigned_worker_id == "node-b"
    assert job.remote_job_id is None
    await service.sync_remote_jobs(None)
    await db_session.refresh(job)
    assert job.remote_job_id == 77
    await service.sync_remote_jobs(None)
    await db_session.refresh(job)
    assert job.status == "processing"
    assert job.assigned_worker_id == "node-b"
    assert service._create_remote_job.call_count == 2


@pytest.mark.parametrize(
    "policy,expected", [("skip", [26]), ("rename", [26, 30]), ("overwrite", [30])]
)
def test_import_repeated_names_in_one_document(client, policy, expected):
    entry = {
        "name": "Repeated",
        "crf": 26,
        "encoder_preset": 4,
        "svt_params": "",
        "audio_bitrate": "96k",
        "max_resolution": 1080,
    }
    document = {
        "format": "archive-video-av1.presets",
        "version": 1,
        "presets": [entry, {**entry, "crf": 30}],
    }
    response = client.post(
        f"/api/presets/import?on_conflict={policy}",
        files={"file": ("presets.json", json.dumps(document), "application/json")},
    )
    assert response.status_code == 200
    assert [preset["crf"] for preset in client.get("/api/presets").json()] == expected


@pytest.mark.asyncio
async def test_websocket_timeout_and_connection_changes():
    manager = WebSocketManager()
    manager.send_timeout = 0.01
    received = []

    class Healthy:
        async def send_json(self, message):
            received.append(message)

    healthy, newcomer = Healthy(), Healthy()

    class Stalled:
        async def send_json(self, message):
            manager.connections.add(newcomer)
            await asyncio.Event().wait()

    stalled = Stalled()
    manager.connections.update([healthy, stalled])
    await asyncio.wait_for(manager.broadcast({"type": "queue_update"}), timeout=1)
    assert manager.connections == {healthy, newcomer}
    assert len(received) == 1


@pytest.mark.asyncio
async def test_cancellation_terminates_process_group(videos, monkeypatch):
    source = videos / "source.mkv"
    source.write_bytes(b"source")
    wrapper = videos / "wrapper.sh"
    child_file = videos / "child.pid"
    wrapper.write_text(f"#!/bin/bash\nsleep 300 &\necho $! > '{child_file}'\nwait\n")
    wrapper.chmod(0o755)
    service = ConversionService()
    service.wrapper_script = str(wrapper)
    started = asyncio.Event()

    async def on_process(process):
        started.set()

    task = asyncio.create_task(
        service.convert_file(
            1,
            str(source),
            str(videos / "source_conv.mkv"),
            {"crf": 26, "encoder_preset": 4, "svt_params": "", "audio_bitrate": "96k"},
            AsyncMock(),
            on_process,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    for _ in range(100):
        if child_file.exists():
            break
        await asyncio.sleep(0.01)
    assert (videos / ".source_conv.mkv.lock").exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert not (videos / ".source_conv.mkv.lock").exists()
    stat = Path(f"/proc/{int(child_file.read_text())}/stat")
    # SIGTERM delivery is asynchronous, so a loaded machine may still show the
    # child as running for a moment.
    state = None
    for _ in range(200):
        try:
            state = stat.read_text().split()[2]
        except (ProcessLookupError, FileNotFoundError):
            return
        if state == "Z":
            return
        await asyncio.sleep(0.01)
    assert state == "Z"


def test_converted_output_cannot_be_queued_as_source(videos):
    from app.utils.file_safety import validate_source_path

    converted = videos / "movie_conv.mkv"
    converted.write_bytes(b"converted")

    with pytest.raises(
        ValueError, match="Converted outputs cannot be queued as sources"
    ):
        validate_source_path(str(converted))


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code,succeeded", [(0, True), (1, False)])
async def test_conversion_removes_output_lock(videos, exit_code, succeeded):
    source = videos / "source.mkv"
    source.write_bytes(b"source")
    lock_path = videos / ".source_conv.mkv.lock"
    wrapper = videos / "wrapper.sh"
    seen = videos / "seen"
    wrapper.write_text(
        f"#!/bin/bash\n[[ -e '{lock_path}' ]] && touch '{seen}'\nexit {exit_code}\n"
    )
    wrapper.chmod(0o755)
    service = ConversionService()
    service.wrapper_script = str(wrapper)
    success, _log = await service.convert_file(
        1,
        str(source),
        str(videos / "source_conv.mkv"),
        {"crf": 26, "encoder_preset": 4, "svt_params": "", "audio_bitrate": "96k"},
        AsyncMock(),
    )
    assert success is succeeded
    assert seen.exists()
    assert not lock_path.exists()


@pytest.mark.asyncio
async def test_conversion_refuses_locked_output(videos):
    source = videos / "source.mkv"
    source.write_bytes(b"source")
    service = ConversionService()
    service.wrapper_script = "/bin/false-never-run"
    with output_lock(videos / "source_conv.mkv"):
        success, log = await service.convert_file(
            1,
            str(source),
            str(videos / "source_conv.mkv"),
            {"crf": 26, "encoder_preset": 4, "svt_params": "", "audio_bitrate": "96k"},
            AsyncMock(),
        )
        assert (videos / ".source_conv.mkv.lock").exists()
    assert success is False and "in use" in log
    assert not (videos / ".source_conv.mkv.lock").exists()
