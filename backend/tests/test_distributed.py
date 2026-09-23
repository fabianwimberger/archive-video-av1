"""Tests for DistributedService worker coordination edge paths."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.job import Job
from app.services.distributed import DistributedService, PeerNode

from .conftest import VIDEO_ROOT


def make_job(**overrides):
    fields = dict(
        source_file=str(VIDEO_ROOT / "sync.mkv"),
        output_file=str(VIDEO_ROOT / "sync_conv.mkv"),
        settings="{}",
        status="processing",
        assigned_worker_id="node-b",
        assigned_worker_name="Worker",
        assigned_worker_url="http://node-b:8000",
        is_cluster_replica=False,
    )
    fields.update(overrides)
    return Job(**fields)


async def add_job(job: Job) -> int:
    async with AsyncSessionLocal() as db:
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.id


async def refresh_job(job_id: int) -> Job:
    async with AsyncSessionLocal() as db:
        result = await db.get(Job, job_id)
        return result


@pytest.mark.asyncio
async def test_sync_remote_jobs_fails_job_when_creation_rejected(
    db_session, monkeypatch
):
    job_id = await add_job(make_job(remote_job_id=None))
    monkeypatch.setattr(settings, "DISTRIBUTED_ENABLED", True)
    distributed = DistributedService()
    distributed._create_remote_job = AsyncMock(side_effect=ValueError("source missing"))

    await distributed.sync_remote_jobs(None)

    job = await refresh_job(job_id)
    assert job.status == "failed"
    assert job.error_message == "source missing"
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_promote_replicas_flags_unknown_worker_state(db_session):
    job_id = await add_job(
        Job(
            source_file=str(VIDEO_ROOT / "promote.mkv"),
            output_file=str(VIDEO_ROOT / "promote_conv.mkv"),
            settings="{}",
            status="processing",
            cluster_job_id="leader:9",
            cluster_origin_node_id="leader",
            cluster_origin_job_id=9,
            is_cluster_replica=True,
            remote_job_id=None,
        )
    )
    distributed = DistributedService()

    jobs = await distributed._promote_replicated_jobs(db_session)

    assert [j.id for j in jobs] == [job_id]
    assert jobs[0].is_cluster_replica is False
    assert jobs[0].error_message == (
        "Worker state unknown; confirm it has stopped before cancelling or retrying"
    )


@pytest.mark.asyncio
async def test_delegate_pending_jobs_marks_job_failed_on_rejection(
    db_session, monkeypatch
):
    job_id = await add_job(make_job(status="pending", assigned_worker_id=None))
    monkeypatch.setattr(settings, "DISTRIBUTED_ENABLED", True)
    distributed = DistributedService()
    distributed._peers = {}
    distributed._leader_id = distributed.node_id
    distributed._available_peers = AsyncMock(
        return_value=[PeerNode("node-b", "Worker", "http://node-b:8000", 0)]
    )
    distributed.replicate_queue = AsyncMock()
    distributed._create_remote_job = AsyncMock(side_effect=ValueError("invalid source"))

    delegated = await distributed.delegate_pending_jobs(None)

    job = await refresh_job(job_id)
    assert delegated == 1
    assert job.status == "failed"
    assert job.error_message == "invalid source"
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_cancel_remote_job_reports_unknown_after_retries(db_session, monkeypatch):
    distributed = DistributedService()
    distributed._client = AsyncMock()
    distributed._client.delete = AsyncMock(side_effect=httpx.ConnectError("down"))
    distributed._get_remote_job = AsyncMock(return_value=None)

    async def instant_sleep(_delay=None, _result=None):
        return None

    monkeypatch.setattr("app.services.distributed.asyncio.sleep", instant_sleep)

    job = Job(
        source_file=str(VIDEO_ROOT / "cancel.mkv"),
        output_file=str(VIDEO_ROOT / "cancel_conv.mkv"),
        settings="{}",
        status="processing",
        assigned_worker_id="node-b",
        assigned_worker_url="http://node-b:8000",
        remote_job_id=7,
    )

    assert await distributed.cancel_remote_job(job) is False
    assert distributed._get_remote_job.await_count == 3


class FakeStatusClient:
    def __init__(self, status_code: int):
        self._status_code = status_code

    async def post(self, *_args, **_kwargs):
        response = httpx.Response(
            self._status_code,
            json={"detail": "worker refused"},
            request=httpx.Request("POST", "http://node-b:8000/api/jobs"),
        )
        response.raise_for_status()
        return response


def remote_job() -> Job:
    return Job(
        source_file=str(VIDEO_ROOT / "create.mkv"),
        output_file=str(VIDEO_ROOT / "create_conv.mkv"),
        settings="{}",
        status="processing",
        cluster_job_id="node-a:3",
        cluster_origin_node_id="node-a",
        cluster_origin_job_id=3,
    )


@pytest.mark.asyncio
async def test_create_remote_job_translates_4xx_to_value_error():
    distributed = DistributedService()
    distributed._client = FakeStatusClient(422)
    peer = PeerNode("node-b", "Worker", "http://node-b:8000", 0)

    with pytest.raises(ValueError, match="worker refused"):
        await distributed._create_remote_job(peer, remote_job())


@pytest.mark.asyncio
async def test_create_remote_job_returns_none_on_5xx():
    distributed = DistributedService()
    distributed._client = FakeStatusClient(500)
    peer = PeerNode("node-b", "Worker", "http://node-b:8000", 0)

    assert await distributed._create_remote_job(peer, remote_job()) is None
