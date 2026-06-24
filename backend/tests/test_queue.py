"""Tests for queue API endpoints."""

import asyncio
import json
import time

import pytest
from sqlalchemy import select
from app.config import settings
from app.models.job import Job
from app.models.schemas import QueueReplicationRequest, ReplicatedJob
from app.routes.cluster import get_cluster_status
from app.services.job_queue import JobQueue
from app.models.app_state import AppState
from app.services.distributed import PeerNode, distributed_service


class TestQueueStatus:
    def test_get_queue_status(self, seeded_client):
        response = seeded_client.get("/api/queue")
        assert response.status_code == 200
        data = response.json()
        assert "paused" in data
        assert "active_job_id" in data
        assert "pending_count" in data

    def test_pause_queue(self, seeded_client):
        response = seeded_client.post("/api/queue/pause")
        assert response.status_code == 200
        assert response.json()["paused"] is True

        # Verify it persisted
        status = seeded_client.get("/api/queue")
        assert status.json()["paused"] is True

    def test_resume_queue(self, seeded_client):
        seeded_client.post("/api/queue/pause")
        response = seeded_client.post("/api/queue/resume")
        assert response.status_code == 200
        assert response.json()["paused"] is False

        status = seeded_client.get("/api/queue")
        assert status.json()["paused"] is False


class TestClusterStatus:
    def test_cluster_status_defaults_to_disabled(self, seeded_client):
        response = seeded_client.get("/api/cluster/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["node_id"]
        assert data["leader_url"] is None
        assert data["is_leader"] is True
        assert data["peers"] == []

    def test_configured_peer_expires_from_status(self, monkeypatch):
        monkeypatch.setattr(distributed_service, "_peers", {})
        monkeypatch.setattr(settings, "DISTRIBUTED_PEERS", ["http://peer:8000"])
        monkeypatch.setattr(settings, "DISTRIBUTED_PEER_TTL_SECONDS", 20)

        distributed_service._remember_peer(
            PeerNode(
                node_id="peer",
                node_name="peer",
                base_url="http://peer:8000",
                last_seen=time.monotonic() - 21,
            )
        )

        assert distributed_service.peers() == []
        assert [peer.base_url for peer in distributed_service._peer_candidates()] == [
            "http://peer:8000"
        ]

    @pytest.mark.asyncio
    async def test_configured_peer_is_probed_after_expiry(self, monkeypatch):
        monkeypatch.setattr(distributed_service, "_peers", {})
        monkeypatch.setattr(settings, "DISTRIBUTED_PEERS", ["http://peer:8000"])

        async def fake_status(peer):
            peer.node_id = "peer"
            peer.node_name = "peer"
            peer.last_seen = time.monotonic()
            distributed_service._remember_peer(peer)
            return {"enabled": True, "active_job_id": None, "pending_count": 0}

        monkeypatch.setattr(distributed_service, "_get_peer_status", fake_status)

        available = await distributed_service._available_peers()

        assert [peer.node_id for peer in available] == ["peer"]

    @pytest.mark.asyncio
    async def test_worker_cluster_status_forwards_to_leader(self, monkeypatch):
        captured = {}

        async def fake_request(method, path):
            captured["method"] = method
            captured["path"] = path
            return {
                "enabled": True,
                "node_id": "node-a",
                "node_name": "node-a",
                "public_url": "http://node-a:8000",
                "leader_url": "http://node-a:8000",
                "is_leader": True,
                "pending_count": 0,
                "active_job_id": None,
                "peers": [],
            }

        monkeypatch.setattr(distributed_service, "should_use_leader", lambda: True)
        monkeypatch.setattr(distributed_service, "request_leader", fake_request)

        response = await get_cluster_status(cluster=True)

        assert captured == {"method": "GET", "path": "/api/cluster/status"}
        assert response["node_id"] == "node-a"

    def test_live_node_is_elected_by_stable_hash(self, monkeypatch):
        monkeypatch.setattr(distributed_service, "_peers", {})
        monkeypatch.setattr(distributed_service, "_leader_id", None)
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_ID", "node-b")
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_NAME", "node-b")
        monkeypatch.setattr(settings, "DISTRIBUTED_PUBLIC_URL", "http://node-b:8000")
        monkeypatch.setattr(settings, "DISTRIBUTED_LEADER_URL", "")
        monkeypatch.setattr(settings, "DISTRIBUTED_PEER_TTL_SECONDS", 20)

        distributed_service._remember_peer(
            PeerNode(
                node_id="node-a",
                node_name="node-a",
                base_url="http://node-a:8000",
                last_seen=time.monotonic(),
            )
        )
        distributed_service._remember_peer(
            PeerNode(
                node_id="node-c",
                node_name="node-c",
                base_url="http://node-c:8000",
                last_seen=time.monotonic(),
            )
        )

        expected = max(
            distributed_service.cluster_nodes(),
            key=lambda node: distributed_service._election_key(node.node_id),
        )
        assert distributed_service.leader_url == expected.base_url
        assert distributed_service.is_leader is (expected.node_id == "node-b")

    def test_leader_moves_when_current_leader_expires(self, monkeypatch):
        monkeypatch.setattr(distributed_service, "_peers", {})
        monkeypatch.setattr(distributed_service, "_leader_id", None)
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_ID", "node-b")
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_NAME", "node-b")
        monkeypatch.setattr(settings, "DISTRIBUTED_PUBLIC_URL", "http://node-b:8000")
        monkeypatch.setattr(settings, "DISTRIBUTED_LEADER_URL", "")
        monkeypatch.setattr(settings, "DISTRIBUTED_PEER_TTL_SECONDS", 20)

        distributed_service._remember_peer(
            PeerNode(
                node_id="node-c",
                node_name="node-c",
                base_url="http://node-c:8000",
                last_seen=time.monotonic() - 21,
            )
        )

        assert distributed_service.leader_url == "http://node-b:8000"
        assert distributed_service.is_leader is True

    def test_returning_node_keeps_established_leader(self, monkeypatch):
        monkeypatch.setattr(distributed_service, "_peers", {})
        monkeypatch.setattr(distributed_service, "_leader_id", "node-c")
        monkeypatch.setattr(distributed_service, "_leader_since", time.monotonic())
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_ID", "node-c")
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_NAME", "node-c")
        monkeypatch.setattr(settings, "DISTRIBUTED_PUBLIC_URL", "http://node-c:8000")
        monkeypatch.setattr(settings, "DISTRIBUTED_LEADER_URL", "")

        distributed_service._remember_peer(
            PeerNode(
                node_id="node-b",
                node_name="node-b",
                base_url="http://node-b:8000",
                last_seen=time.monotonic(),
            )
        )

        distributed_service._remember_reported_leader(
            {
                "node_id": "node-b",
                "leader_url": "http://node-b:8000",
                "is_leader": True,
                "leader_age_seconds": 30,
            }
        )

        assert distributed_service.leader_url == "http://node-b:8000"
        assert distributed_service.is_leader is False

    def test_current_leader_can_report_successor(self, monkeypatch):
        monkeypatch.setattr(distributed_service, "_peers", {})
        monkeypatch.setattr(distributed_service, "_leader_id", "node-b")
        monkeypatch.setattr(distributed_service, "_leader_since", time.monotonic() - 60)
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_ID", "node-c")
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_NAME", "node-c")
        monkeypatch.setattr(settings, "DISTRIBUTED_PUBLIC_URL", "http://node-c:8000")
        monkeypatch.setattr(settings, "DISTRIBUTED_LEADER_URL", "")
        monkeypatch.setattr(settings, "DISTRIBUTED_PEER_TTL_SECONDS", 20)

        distributed_service._remember_peer(
            PeerNode(
                node_id="node-a",
                node_name="node-a",
                base_url="http://node-a:8000",
                last_seen=time.monotonic(),
            )
        )
        distributed_service._remember_peer(
            PeerNode(
                node_id="node-b",
                node_name="node-b",
                base_url="http://node-b:8000",
                last_seen=time.monotonic(),
            )
        )

        distributed_service._remember_reported_leader(
            {
                "node_id": "node-b",
                "leader_url": "http://node-a:8000",
                "is_leader": False,
                "leader_age_seconds": 60,
            }
        )

        assert distributed_service.leader_url == "http://node-a:8000"

    def test_simultaneous_leaders_resolve_by_election_key(self, monkeypatch):
        monkeypatch.setattr(distributed_service, "_peers", {})
        node_ids = ["node-a", "node-b"]
        current_id = min(node_ids, key=distributed_service._election_key)
        reported_id = max(node_ids, key=distributed_service._election_key)
        monkeypatch.setattr(distributed_service, "_leader_id", current_id)
        monkeypatch.setattr(distributed_service, "_leader_since", time.monotonic())
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_ID", current_id)
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_NAME", current_id)
        monkeypatch.setattr(
            settings, "DISTRIBUTED_PUBLIC_URL", f"http://{current_id}:8000"
        )
        monkeypatch.setattr(settings, "DISTRIBUTED_LEADER_URL", "")

        distributed_service._remember_peer(
            PeerNode(
                node_id=reported_id,
                node_name=reported_id,
                base_url=f"http://{reported_id}:8000",
                last_seen=time.monotonic(),
            )
        )

        distributed_service._remember_reported_leader(
            {
                "node_id": reported_id,
                "leader_url": f"http://{reported_id}:8000",
                "is_leader": True,
                "leader_age_seconds": distributed_service.leader_age_seconds(),
            }
        )

        assert distributed_service.leader_url == f"http://{reported_id}:8000"
        assert distributed_service.is_leader is False

    @pytest.mark.asyncio
    async def test_remote_job_is_requeued_when_worker_expires(self, monkeypatch):
        monkeypatch.setattr(distributed_service, "_peers", {})

        job = Job(
            source_file="/videos/test.mkv",
            output_file="/videos/test.av1.mkv",
            settings="{}",
            status="processing",
            assigned_worker_id="node-a",
            assigned_worker_name="node-a",
            assigned_worker_url="http://node-a:8000",
            remote_job_id=7,
            progress_percent=42,
        )

        class FakeResult:
            def scalar(self):
                return 0

        class FakeDb:
            async def execute(self, _statement):
                return FakeResult()

        assert distributed_service._peer_is_fresh("http://node-a:8000") is False

        await distributed_service._requeue_remote_job(FakeDb(), job)

        requeued = job
        assert requeued.status == "pending"
        assert requeued.assigned_worker_id is None
        assert requeued.remote_job_id is None
        assert requeued.queue_position == 1

    @pytest.mark.asyncio
    async def test_queue_replication_stores_follower_copy(self, monkeypatch):
        monkeypatch.setattr(distributed_service, "_peers", {})
        monkeypatch.setattr(distributed_service, "_leader_id", "node-a")
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_ID", "node-b")
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_NAME", "node-b")
        monkeypatch.setattr(settings, "DISTRIBUTED_PUBLIC_URL", "http://node-b:8000")
        monkeypatch.setattr(settings, "DISTRIBUTED_LEADER_URL", "")

        distributed_service._remember_peer(
            PeerNode(
                node_id="node-a",
                node_name="node-a",
                base_url="http://node-a:8000",
                last_seen=time.monotonic(),
            )
        )

        payload = QueueReplicationRequest(
            leader_node_id="node-a",
            leader_url="http://node-a:8000",
            leader_age_seconds=30,
            jobs=[
                ReplicatedJob(
                    cluster_job_id="node-a:1",
                    cluster_origin_node_id="node-a",
                    cluster_origin_job_id=1,
                    source_file="/videos/a.mkv",
                    output_file="/videos/a.av1.mkv",
                    settings="{}",
                    queue_position=1,
                    status="pending",
                )
            ],
        )

        class FakeScalars:
            def all(self):
                return []

        class FakeResult:
            def scalars(self):
                return FakeScalars()

        class FakeDb:
            def __init__(self):
                self.added = []

            async def execute(self, _statement):
                return FakeResult()

            def add(self, job):
                self.added.append(job)

            async def commit(self):
                pass

        db = FakeDb()
        applied = await distributed_service.apply_queue_replication(db, payload)
        replica = db.added[0]

        assert applied == 1
        assert replica.is_cluster_replica is True
        assert replica.status == "pending"
        assert replica.source_file == "/videos/a.mkv"

    @pytest.mark.asyncio
    async def test_queue_replication_prunes_old_leader_replicas(
        self, db_session, monkeypatch
    ):
        monkeypatch.setattr(distributed_service, "_peers", {})
        monkeypatch.setattr(distributed_service, "_leader_id", "node-a")
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_ID", "node-b")
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_NAME", "node-b")
        monkeypatch.setattr(settings, "DISTRIBUTED_PUBLIC_URL", "http://node-b:8000")
        monkeypatch.setattr(settings, "DISTRIBUTED_LEADER_URL", "")

        distributed_service._remember_peer(
            PeerNode(
                node_id="node-a",
                node_name="node-a",
                base_url="http://node-a:8000",
                last_seen=time.monotonic(),
            )
        )

        db_session.add_all(
            [
                Job(
                    cluster_job_id="node-a:1",
                    cluster_origin_node_id="node-a",
                    cluster_origin_job_id=1,
                    source_file="/videos/a.mkv",
                    output_file="/videos/a.av1.mkv",
                    settings="{}",
                    status="pending",
                    is_cluster_replica=True,
                ),
                Job(
                    cluster_job_id="old-leader:2",
                    cluster_origin_node_id="old-leader",
                    cluster_origin_job_id=2,
                    source_file="/videos/old.mkv",
                    output_file="/videos/old.av1.mkv",
                    settings="{}",
                    status="processing",
                    is_cluster_replica=True,
                ),
            ]
        )
        await db_session.commit()

        payload = QueueReplicationRequest(
            leader_node_id="node-a",
            leader_url="http://node-a:8000",
            leader_age_seconds=30,
            jobs=[
                ReplicatedJob(
                    cluster_job_id="node-a:1",
                    cluster_origin_node_id="node-a",
                    cluster_origin_job_id=1,
                    source_file="/videos/a.mkv",
                    output_file="/videos/a.av1.mkv",
                    settings="{}",
                    queue_position=1,
                    status="pending",
                )
            ],
        )

        await distributed_service.apply_queue_replication(db_session, payload)

        result = await db_session.execute(select(Job.cluster_job_id))
        assert result.scalars().all() == ["node-a:1"]

    @pytest.mark.asyncio
    async def test_peer_job_listing_skips_disabled_peer(self, monkeypatch):
        monkeypatch.setattr(distributed_service, "_peers", {})
        peer = PeerNode(
            node_id="pc",
            node_name="pc",
            base_url="http://pc:8000",
            last_seen=time.monotonic(),
        )
        distributed_service._remember_peer(peer)

        async def fake_status(_peer):
            return {"enabled": False}

        class FakeClient:
            async def get(self, *_args, **_kwargs):
                raise AssertionError("disabled peers should not be queried for jobs")

        monkeypatch.setattr(distributed_service, "_get_peer_status", fake_status)
        monkeypatch.setattr(distributed_service, "_client", FakeClient())

        jobs = await distributed_service.list_peer_jobs({"cluster": "false"})

        assert jobs == []

    @pytest.mark.asyncio
    async def test_replicated_jobs_promote_on_leader_takeover(self, monkeypatch):
        monkeypatch.setattr(distributed_service, "_peers", {})
        monkeypatch.setattr(distributed_service, "_leader_id", "node-b")
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_ID", "node-b")
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_NAME", "node-b")
        monkeypatch.setattr(settings, "DISTRIBUTED_PUBLIC_URL", "http://node-b:8000")
        monkeypatch.setattr(settings, "DISTRIBUTED_LEADER_URL", "")

        replica = Job(
            cluster_job_id="node-a:1",
            cluster_origin_node_id="node-a",
            cluster_origin_job_id=1,
            source_file="/videos/a.mkv",
            output_file="/videos/a.av1.mkv",
            settings="{}",
            status="pending",
            queue_position=1,
            is_cluster_replica=True,
        )

        class FakeScalars:
            def all(self):
                return [replica]

        class FakeResult:
            def scalars(self):
                return FakeScalars()

        class FakeDb:
            async def execute(self, _statement):
                return FakeResult()

        promoted = await distributed_service._promote_replicated_jobs(FakeDb())

        assert promoted == [replica]
        assert replica.is_cluster_replica is False
        assert replica.status == "pending"


class TestQueuePauseRehydration:
    @pytest.mark.asyncio
    async def test_start_worker_rehydrates_paused_state(
        self, db_session, original_jobqueue_methods
    ):
        """A restart under paused state must stay paused (PLAN.md §E(4))."""
        # Persist paused=true in app_state
        db_session.add(AppState(key="queue_paused", value="true"))
        await db_session.commit()

        # Swap back original methods so we can test real start_worker/stop_worker
        patched_start = JobQueue.start_worker
        patched_stop = JobQueue.stop_worker
        JobQueue.start_worker = original_jobqueue_methods["start"]
        JobQueue.stop_worker = original_jobqueue_methods["stop"]

        try:
            queue = JobQueue()
            await queue.start_worker()
            assert queue._paused_event is not None
            assert not queue._paused_event.is_set()
            await queue.stop_worker()
        finally:
            JobQueue.start_worker = patched_start
            JobQueue.stop_worker = patched_stop


class TestDistributedStartupClaims:
    @pytest.mark.asyncio
    async def test_unstable_leader_does_not_claim_unassigned_jobs(
        self, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "DISTRIBUTED_ENABLED", True)
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_ID", "pc")
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_NAME", "PC")
        monkeypatch.setattr(settings, "DISTRIBUTED_PUBLIC_URL", "http://pc:8000")
        monkeypatch.setattr(settings, "DISTRIBUTED_PEER_TTL_SECONDS", 20)
        monkeypatch.setattr(distributed_service, "_peers", {})
        monkeypatch.setattr(distributed_service, "_leader_id", "pc")
        monkeypatch.setattr(distributed_service, "_leader_since", time.monotonic())

        job = Job(
            source_file="/videos/stale.mkv",
            output_file="/videos/stale_conv.mkv",
            settings="{}",
            status="pending",
            is_cluster_replica=False,
        )
        db_session.add(job)
        await db_session.commit()

        queue = JobQueue()
        claimed = await queue._claim_next_job(db_session)

        await db_session.refresh(job)
        assert claimed is None
        assert job.status == "pending"

    @pytest.mark.asyncio
    async def test_unstable_leader_claims_jobs_assigned_to_this_worker(
        self, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "DISTRIBUTED_ENABLED", True)
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_ID", "pc")
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_NAME", "PC")
        monkeypatch.setattr(settings, "DISTRIBUTED_PUBLIC_URL", "http://pc:8000")
        monkeypatch.setattr(settings, "DISTRIBUTED_PEER_TTL_SECONDS", 20)
        monkeypatch.setattr(distributed_service, "_peers", {})
        monkeypatch.setattr(distributed_service, "_leader_id", "pc")
        monkeypatch.setattr(distributed_service, "_leader_since", time.monotonic())

        job = Job(
            source_file="/videos/delegated.mkv",
            output_file="/videos/delegated_conv.mkv",
            settings="{}",
            status="pending",
            assigned_worker_id="pc",
            is_cluster_replica=False,
        )
        db_session.add(job)
        await db_session.commit()

        queue = JobQueue()
        claimed = await queue._claim_next_job(db_session)

        assert claimed is not None
        assert claimed.id == job.id
        assert claimed.status == "processing"


class TestClusterMulticast:
    """Multicast send/receive must use real socket calls, not uvloop's
    unimplemented loop.sock_sendto()/sock_recvfrom()."""

    @pytest.mark.asyncio
    async def test_broadcast_loop_sends_on_real_socket(self, monkeypatch):
        import socket as socket_module

        receiver = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(2)
        port = receiver.getsockname()[1]

        sender = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_DGRAM)
        monkeypatch.setattr(distributed_service, "_socket", sender)
        monkeypatch.setattr(distributed_service, "_running", True)
        monkeypatch.setattr(settings, "DISTRIBUTED_DISCOVERY_GROUP", "127.0.0.1")
        monkeypatch.setattr(settings, "DISTRIBUTED_DISCOVERY_PORT", port)
        monkeypatch.setattr(settings, "DISTRIBUTED_HEARTBEAT_SECONDS", 100)

        task = asyncio.create_task(distributed_service._broadcast_loop())
        try:
            data, _addr = await asyncio.get_event_loop().run_in_executor(
                None, receiver.recvfrom, 65535
            )
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            sender.close()
            receiver.close()

        payload = json.loads(data.decode("utf-8"))
        assert payload["service"] == "archive-video-av1"

    @pytest.mark.asyncio
    async def test_listen_loop_remembers_peer_from_real_socket(self, monkeypatch):
        import socket as socket_module

        listener = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_DGRAM)
        listener.bind(("127.0.0.1", 0))
        listener.setblocking(False)
        port = listener.getsockname()[1]

        monkeypatch.setattr(distributed_service, "_socket", listener)
        monkeypatch.setattr(distributed_service, "_running", True)
        monkeypatch.setattr(distributed_service, "_peers", {})
        monkeypatch.setattr(settings, "DISTRIBUTED_NODE_ID", "pc")

        task = asyncio.create_task(distributed_service._listen_loop())
        try:
            sender = socket_module.socket(
                socket_module.AF_INET, socket_module.SOCK_DGRAM
            )
            sender.sendto(
                json.dumps(
                    {
                        "service": "archive-video-av1",
                        "node_id": "server",
                        "node_name": "server",
                        "base_url": "http://server:8000",
                        "is_leader": True,
                    }
                ).encode("utf-8"),
                ("127.0.0.1", port),
            )
            sender.close()

            for _ in range(50):
                if distributed_service.peers():
                    break
                await asyncio.sleep(0.1)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            listener.close()

        assert [peer.node_id for peer in distributed_service.peers()] == ["server"]
