"""Tests for queue API endpoints."""

import time

import pytest
from app.config import settings
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
