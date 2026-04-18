"""Tests for queue API endpoints."""

import pytest
from app.services.job_queue import JobQueue
from app.models.app_state import AppState


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
