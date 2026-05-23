"""Tests for queue API endpoints."""

import time

import pytest
from app.config import settings
from app.models.job import Job
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
