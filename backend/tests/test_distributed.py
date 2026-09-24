"""Tests for DistributedService worker coordination edge paths."""

import time
from typing import Optional, cast
from unittest.mock import AsyncMock

import httpx
import pytest

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.job import Job
from app.services.cluster_ledger import read_ledger, write_ledger
from app.services.distributed import (
    REASSIGNED_ERROR,
    DistributedService,
    LeaderRequestError,
    PeerNode,
)

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
    )
    fields.update(overrides)
    return Job(**fields)


async def add_job(job: Job) -> int:
    async with AsyncSessionLocal() as db:
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return cast(int, job.id)


async def refresh_job(job_id: int) -> Optional[Job]:
    async with AsyncSessionLocal() as db:
        result = await db.get(Job, job_id)
        return result


async def all_jobs() -> list[Job]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).order_by(Job.id))
        return list(result.scalars().all())


@pytest.fixture(autouse=True)
def ledger_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DISTRIBUTED_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


def leader_service(monkeypatch, term=1) -> DistributedService:
    """A node that is the only cluster member and already holds the queue."""
    monkeypatch.setattr(settings, "DISTRIBUTED_ENABLED", True)
    distributed = DistributedService()
    distributed._peers = {}
    monkeypatch.setattr(distributed, "leader_is_stable", lambda: True)
    distributed._term = term
    return distributed


@pytest.mark.asyncio
async def test_sync_remote_jobs_fails_job_when_creation_rejected(
    db_session, monkeypatch
):
    job_id = await add_job(make_job(remote_job_id=None))
    distributed = leader_service(monkeypatch)
    distributed._create_remote_job = AsyncMock(side_effect=ValueError("source missing"))

    await distributed.sync_remote_jobs(None)

    job = await refresh_job(job_id)
    assert job.status == "failed"
    assert job.error_message == "source missing"
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_delegate_pending_jobs_marks_job_failed_on_rejection(
    db_session, monkeypatch
):
    job_id = await add_job(make_job(status="pending", assigned_worker_id=None))
    distributed = leader_service(monkeypatch)
    distributed._available_peers = AsyncMock(
        return_value=[PeerNode("node-b", "Worker", "http://node-b:8000", 0)]
    )
    distributed.publish_queue = AsyncMock()
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


def lost_worker_service(monkeypatch) -> DistributedService:
    monkeypatch.setattr(settings, "DISTRIBUTED_WORKER_TIMEOUT_SECONDS", 60)
    distributed = leader_service(monkeypatch)
    monkeypatch.setattr(distributed, "_get_remote_job", AsyncMock(return_value=None))
    return distributed


@pytest.mark.asyncio
async def test_sync_requeues_job_after_worker_silent_past_timeout(
    db_session, monkeypatch
):
    job_id = await add_job(make_job(remote_job_id=7, progress_percent=45.0))
    distributed = lost_worker_service(monkeypatch)

    assert await distributed.sync_remote_jobs(None) == 0
    assert (await refresh_job(job_id)).status == "processing"

    distributed._unreachable_since[job_id] -= 61
    assert await distributed.sync_remote_jobs(None) == 1

    job = await refresh_job(job_id)
    assert job.status == "pending"
    assert job.assigned_worker_id is None
    assert job.assigned_worker_url is None
    assert job.remote_job_id is None
    assert job.progress_percent == 0.0
    assert job.started_at is None
    assert job.requeue_count == 1


@pytest.mark.asyncio
async def test_sync_keeps_job_while_worker_still_heartbeats(db_session, monkeypatch):
    job_id = await add_job(make_job(remote_job_id=7))
    distributed = lost_worker_service(monkeypatch)
    distributed._remember_peer(
        PeerNode("node-b", "Worker", "http://node-b:8000", time.monotonic())
    )
    distributed._leader_id = distributed.node_id
    distributed._unreachable_since[job_id] = time.monotonic() - 600

    assert await distributed.sync_remote_jobs(None) == 0
    assert (await refresh_job(job_id)).status == "processing"


@pytest.mark.asyncio
async def test_sync_requeues_undelivered_job_after_worker_silent(
    db_session, monkeypatch
):
    job_id = await add_job(make_job(remote_job_id=None))
    distributed = lost_worker_service(monkeypatch)
    distributed._create_remote_job = AsyncMock(return_value=None)

    await distributed.sync_remote_jobs(None)
    distributed._unreachable_since[job_id] -= 61
    assert await distributed.sync_remote_jobs(None) == 1
    assert (await refresh_job(job_id)).status == "pending"


@pytest.mark.asyncio
async def test_sync_requeues_job_interrupted_by_worker_restart(db_session, monkeypatch):
    job_id = await add_job(make_job(remote_job_id=7))
    distributed = leader_service(monkeypatch)
    distributed._get_remote_job = AsyncMock(
        return_value={
            "status": "failed",
            "error_message": "Interrupted by service restart",
        }
    )

    assert await distributed.sync_remote_jobs(None) == 1
    job = await refresh_job(job_id)
    assert job.status == "pending"
    assert job.error_message is None


@pytest.mark.asyncio
async def test_sync_fetches_log_only_every_heartbeat(db_session, monkeypatch):
    await add_job(make_job(remote_job_id=7))
    distributed = leader_service(monkeypatch)
    distributed._get_remote_job = AsyncMock(
        return_value={"status": "processing", "progress_percent": 10.0}
    )

    await distributed.sync_remote_jobs(None)
    await distributed.sync_remote_jobs(None)

    include_log = [call.args[2] for call in distributed._get_remote_job.await_args_list]
    assert include_log == [True, False]


@pytest.mark.asyncio
async def test_follower_skips_remote_sync(db_session, monkeypatch):
    monkeypatch.setattr(settings, "DISTRIBUTED_ENABLED", True)
    await add_job(make_job(remote_job_id=7, assigned_worker_id="node-c"))
    distributed = DistributedService()
    distributed._get_remote_job = AsyncMock()

    assert await distributed.sync_remote_jobs(None) == 0
    distributed._get_remote_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_peer_leave_requeues_its_jobs_and_drops_it_as_leader(
    db_session, monkeypatch
):
    leaving_id = await add_job(make_job(remote_job_id=7))
    other_id = await add_job(
        make_job(
            source_file=str(VIDEO_ROOT / "other.mkv"),
            output_file=str(VIDEO_ROOT / "other_conv.mkv"),
            assigned_worker_id="node-c",
            assigned_worker_url="http://node-c:8000",
            remote_job_id=8,
        )
    )
    distributed = leader_service(monkeypatch)
    distributed._remember_peer(
        PeerNode("node-b", "Worker", "http://node-b:8000", time.monotonic())
    )
    distributed._leader_id = "node-b"

    assert await distributed.handle_peer_leave("node-b", None) == 1

    assert (await refresh_job(leaving_id)).status == "pending"
    assert (await refresh_job(other_id)).status == "processing"
    assert all(peer.node_id != "node-b" for peer in distributed.peers())
    assert distributed.is_leader
    assert "node-b" in distributed._departed


@pytest.mark.asyncio
async def test_requeue_gives_up_after_max_attempts(db_session, monkeypatch):
    monkeypatch.setattr(settings, "DISTRIBUTED_MAX_REQUEUES", 2)
    job_id = await add_job(make_job(remote_job_id=7, requeue_count=2))
    distributed = DistributedService()

    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        distributed._requeue_job(job, "worker unreachable")
        await db.commit()

    job = await refresh_job(job_id)
    assert job.status == "failed"
    assert job.completed_at is not None
    assert job.error_message == (
        "Gave up after 3 interrupted attempts (last: worker unreachable on Worker)"
    )


def ledger_entry(cluster_job_id: str, **overrides) -> dict:
    fields = dict(
        cluster_job_id=cluster_job_id,
        cluster_origin_node_id="node-a",
        cluster_origin_job_id=1,
        source_file=str(VIDEO_ROOT / f"{cluster_job_id}.mkv"),
        output_file=str(VIDEO_ROOT / f"{cluster_job_id}_conv.mkv"),
        settings="{}",
        status="pending",
    )
    fields.update(overrides)
    return fields


async def seed_ledger(owner: str, term: int, jobs: list[dict]) -> None:
    await write_ledger(
        {
            "term": term,
            "leader_node_id": owner,
            "leader_url": f"http://{owner}:8000",
            "updated_at": "2026-09-24T00:00:00+00:00",
            "jobs": jobs,
        }
    )


def candidate_service(monkeypatch) -> DistributedService:
    """The only live node, elected leader but not yet holding the queue."""
    monkeypatch.setattr(settings, "DISTRIBUTED_ENABLED", True)
    distributed = DistributedService()
    distributed._peers = {}
    monkeypatch.setattr(distributed, "leader_is_stable", lambda: True)
    return distributed


@pytest.mark.asyncio
async def test_first_leader_bootstraps_ledger_from_local_queue(db_session, monkeypatch):
    await add_job(make_job(status="pending", assigned_worker_id=None))
    distributed = candidate_service(monkeypatch)

    assert await distributed.own_queue(None) is True

    ledger = await read_ledger()
    assert ledger["term"] == 1
    assert ledger["leader_node_id"] == distributed.node_id
    assert [job["status"] for job in ledger["jobs"]] == ["pending"]
    assert "log" not in ledger["jobs"][0]


@pytest.mark.asyncio
async def test_live_owner_blocks_takeover(db_session, monkeypatch):
    await seed_ledger("node-a", 4, [])
    distributed = candidate_service(monkeypatch)

    assert await distributed.own_queue(None) is False
    assert distributed.holds_queue is False


@pytest.mark.asyncio
async def test_departed_owner_allows_takeover_at_once(db_session, monkeypatch):
    await seed_ledger("node-a", 4, [])
    distributed = candidate_service(monkeypatch)
    distributed._departed.add("node-a")

    assert await distributed.own_queue(None) is True
    assert (await read_ledger())["term"] == 5


@pytest.mark.asyncio
async def test_takeover_after_silent_owner_follows_the_ledger(db_session, monkeypatch):
    # This node's own stale view: one job the cluster already finished and one
    # the user deleted while this node was offline.
    finished_id = await add_job(
        make_job(status="pending", assigned_worker_id=None, cluster_job_id="done")
    )
    deleted_id = await add_job(
        make_job(
            status="pending",
            assigned_worker_id=None,
            cluster_job_id="deleted",
            source_file=str(VIDEO_ROOT / "deleted.mkv"),
            output_file=str(VIDEO_ROOT / "deleted_conv.mkv"),
        )
    )
    await seed_ledger(
        "node-a",
        4,
        [
            ledger_entry("queued", queue_position=1),
            ledger_entry(
                "remote",
                status="processing",
                assigned_worker_id="node-c",
                assigned_worker_url="http://node-c:8000",
                remote_job_id=9,
            ),
        ],
    )
    distributed = candidate_service(monkeypatch)
    assert await distributed.own_queue(None) is False
    distributed._ledger_changed_at -= settings.DISTRIBUTED_PEER_TTL_SECONDS

    assert await distributed.own_queue(None) is True

    jobs = {job.cluster_job_id: job for job in await all_jobs()}
    assert set(jobs) == {"queued", "remote"}
    assert await refresh_job(finished_id) is None
    assert await refresh_job(deleted_id) is None
    assert jobs["remote"].status == "processing"
    assert jobs["remote"].remote_job_id == 9
    ledger = await read_ledger()
    assert ledger["term"] == 5
    assert {job["cluster_job_id"] for job in ledger["jobs"]} == {"queued", "remote"}


@pytest.mark.asyncio
async def test_takeover_requeues_own_job_lost_in_restart(db_session, monkeypatch):
    distributed = candidate_service(monkeypatch)
    job_id = await add_job(
        make_job(
            status="failed",
            error_message="Interrupted by service restart",
            assigned_worker_id=distributed.node_id,
            assigned_worker_url=None,
            cluster_job_id="mine",
        )
    )
    await seed_ledger(
        distributed.node_id,
        2,
        [
            ledger_entry(
                "mine",
                status="processing",
                assigned_worker_id=distributed.node_id,
                assigned_worker_url="http://self:8000",
                remote_job_id=job_id,
            )
        ],
    )

    assert await distributed.own_queue(None) is True

    job = await refresh_job(job_id)
    assert job.status == "pending"
    assert job.assigned_worker_id is None
    assert job.completed_at is None
    assert job.requeue_count == 1


@pytest.mark.asyncio
async def test_takeover_keeps_own_completed_outcome(db_session, monkeypatch):
    distributed = candidate_service(monkeypatch)
    job_id = await add_job(
        make_job(
            status="completed",
            assigned_worker_id=distributed.node_id,
            cluster_job_id="mine",
        )
    )
    await seed_ledger(
        distributed.node_id,
        2,
        [
            ledger_entry(
                "mine", status="processing", assigned_worker_id=distributed.node_id
            )
        ],
    )

    assert await distributed.own_queue(None) is True

    assert (await refresh_job(job_id)).status == "completed"
    assert (await read_ledger())["jobs"] == []


@pytest.mark.asyncio
async def test_unsettled_leader_watches_ledger_without_taking_over(
    db_session, monkeypatch
):
    await seed_ledger("node-a", 4, [])
    distributed = candidate_service(monkeypatch)
    monkeypatch.setattr(distributed, "leader_is_stable", lambda: False)

    assert await distributed.own_queue(None) is False
    assert distributed._ledger_key == (4, "2026-09-24T00:00:00+00:00")


@pytest.mark.asyncio
async def test_worker_silence_counts_from_last_heartbeat(db_session, monkeypatch):
    job_id = await add_job(make_job(remote_job_id=7))
    distributed = lost_worker_service(monkeypatch)
    distributed._last_heard["node-b"] = time.monotonic() - 61

    assert await distributed.sync_remote_jobs(None) == 1
    assert (await refresh_job(job_id)).status == "pending"


@pytest.mark.asyncio
async def test_holder_steps_down_when_ledger_moves_on(db_session, monkeypatch):
    distributed = leader_service(monkeypatch, term=3)
    await seed_ledger("node-a", 4, [])

    assert await distributed.own_queue(None) is False
    assert distributed.holds_queue is False


@pytest.mark.asyncio
async def test_publish_refuses_to_overwrite_newer_term(db_session, monkeypatch):
    distributed = leader_service(monkeypatch, term=3)
    await seed_ledger("node-a", 4, [ledger_entry("theirs")])
    await add_job(make_job(status="pending", assigned_worker_id=None))

    await distributed.publish_queue()

    ledger = await read_ledger()
    assert ledger["leader_node_id"] == "node-a"
    assert distributed.holds_queue is False


def follower_service(monkeypatch) -> DistributedService:
    monkeypatch.setattr(settings, "DISTRIBUTED_ENABLED", True)
    monkeypatch.setattr(settings, "DISTRIBUTED_NODE_ID", "node-b")
    distributed = DistributedService()
    monkeypatch.setattr(distributed, "leader_is_stable", lambda: True)
    distributed._remember_peer(
        PeerNode("leader", "Leader", "http://leader:8000", time.monotonic())
    )
    distributed._leader_id = "leader"
    return distributed


def leader_replies(distributed, verdicts):
    distributed.request_leader = AsyncMock(return_value={"jobs": verdicts})
    return distributed.request_leader


@pytest.mark.asyncio
async def test_reconcile_stops_local_job_the_leader_moved(db_session, monkeypatch):
    distributed = follower_service(monkeypatch)
    job_id = await add_job(
        make_job(status="pending", assigned_worker_id="node-b", cluster_job_id="L:1")
    )
    leader_replies(
        distributed, {"L:1": {"status": "pending", "assigned_worker_id": None}}
    )

    await distributed.reconcile_with_leader()

    job = await refresh_job(job_id)
    assert job.status == "cancelled"
    assert job.error_message == REASSIGNED_ERROR


@pytest.mark.asyncio
async def test_reconcile_stops_local_job_unknown_to_leader(db_session, monkeypatch):
    distributed = follower_service(monkeypatch)
    job_id = await add_job(make_job(assigned_worker_id="node-b", cluster_job_id="L:1"))
    leader_replies(distributed, {})

    await distributed.reconcile_with_leader()

    assert (await refresh_job(job_id)).status == "cancelled"


@pytest.mark.asyncio
async def test_reconcile_keeps_job_still_assigned_here(db_session, monkeypatch):
    distributed = follower_service(monkeypatch)
    job_id = await add_job(
        make_job(status="pending", assigned_worker_id="node-b", cluster_job_id="L:1")
    )
    request = leader_replies(
        distributed, {"L:1": {"status": "processing", "assigned_worker_id": "node-b"}}
    )

    await distributed.reconcile_with_leader()

    assert (await refresh_job(job_id)).status == "pending"
    assert request.await_args.kwargs["json_body"] == {"cluster_job_ids": ["L:1"]}


@pytest.mark.asyncio
async def test_reconcile_drops_leftover_queue_rows(db_session, monkeypatch):
    distributed = follower_service(monkeypatch)
    job_id = await add_job(
        make_job(status="pending", assigned_worker_id=None, cluster_job_id="B:1")
    )
    leader_replies(distributed, {})

    await distributed.reconcile_with_leader()

    assert await refresh_job(job_id) is None


@pytest.mark.asyncio
async def test_reconcile_keeps_state_when_leader_unavailable(db_session, monkeypatch):
    distributed = follower_service(monkeypatch)
    leftover_id = await add_job(
        make_job(status="pending", assigned_worker_id=None, cluster_job_id="B:1")
    )
    distributed.request_leader = AsyncMock(
        side_effect=LeaderRequestError(409, "not holding")
    )

    await distributed.reconcile_with_leader()

    assert (await refresh_job(leftover_id)).status == "pending"


@pytest.mark.asyncio
async def test_leader_reports_only_jobs_in_its_queue(db_session, monkeypatch):
    await add_job(
        make_job(status="completed", assigned_worker_id="node-c", cluster_job_id="K:1")
    )
    distributed = leader_service(monkeypatch)

    verdicts = await distributed.reconcile_follower_jobs(db_session, ["K:1", "B:2"])

    assert verdicts == {"K:1": {"status": "completed", "assigned_worker_id": "node-c"}}
    assert [job.cluster_job_id for job in await all_jobs()] == ["K:1"]


@pytest.mark.asyncio
async def test_leader_without_queue_refuses_reconcile(db_session, monkeypatch):
    distributed = candidate_service(monkeypatch)

    assert await distributed.reconcile_follower_jobs(db_session, []) is None


@pytest.mark.asyncio
async def test_stop_announces_leave_to_peers():
    distributed = DistributedService()
    distributed._remember_peer(
        PeerNode("node-b", "Worker", "http://node-b:8000", time.monotonic())
    )
    client = AsyncMock()
    client.post = AsyncMock(
        return_value=httpx.Response(
            200, request=httpx.Request("POST", "http://node-b:8000")
        )
    )
    distributed._client = client

    await distributed.stop()

    client.post.assert_awaited_once()
    assert client.post.await_args.args[0] == "http://node-b:8000/api/cluster/leave"
    assert client.post.await_args.kwargs["json"] == {"node_id": distributed.node_id}
