"""Tests for job API endpoints."""

import asyncio
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.job import Job


class TestCreateJob:
    def test_create_job_with_preset(self, seeded_client):
        payload = {
            "source_file": "/videos/test.mkv",
            "preset_id": 1,
        }
        response = seeded_client.post("/api/jobs", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert len(data["job_ids"]) == 1

    def test_create_job_with_settings_only(self, seeded_client):
        payload = {
            "source_file": "/videos/test.mkv",
            "settings": {
                "crf": 28,
                "encoder_preset": 4,
                "svt_params": "tune=0",
                "audio_bitrate": "96k",
                "skip_crop_detect": False,
                "max_resolution": 1080,
            },
        }
        response = seeded_client.post("/api/jobs", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert len(data["job_ids"]) == 1

    def test_create_job_without_preset_or_settings(self, seeded_client):
        payload = {
            "source_file": "/videos/test.mkv",
        }
        response = seeded_client.post("/api/jobs", json=payload)
        assert response.status_code == 422

    def test_create_job_with_preset_and_settings_override(self, seeded_client):
        payload = {
            "source_file": "/videos/test.mkv",
            "preset_id": 1,
            "settings": {
                "crf": 30,
                "encoder_preset": 4,
                "svt_params": "tune=0",
                "audio_bitrate": "96k",
                "skip_crop_detect": False,
                "max_resolution": 1080,
            },
        }
        response = seeded_client.post("/api/jobs", json=payload)
        assert response.status_code == 200
        job_id = response.json()["job_ids"][0]

        # Verify snapshot name includes "modified"
        get_resp = seeded_client.get(f"/api/jobs/{job_id}")
        assert get_resp.status_code == 200
        assert "modified" in get_resp.json()["preset_name_snapshot"]


class TestGetJob:
    def test_get_job_returns_settings_as_dict(self, seeded_client):
        payload = {
            "source_file": "/videos/test.mkv",
            "preset_id": 1,
        }
        create_resp = seeded_client.post("/api/jobs", json=payload)
        job_id = create_resp.json()["job_ids"][0]

        response = seeded_client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["settings"], dict)
        assert "crf" in data["settings"]


class TestListJobs:
    def test_list_jobs_with_status_filter(self, seeded_client):
        # Create a job
        payload = {"source_file": "/videos/test.mkv", "preset_id": 1}
        seeded_client.post("/api/jobs", json=payload)

        response = seeded_client.get("/api/jobs?status=pending&limit=10&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert all(j["status"] == "pending" for j in data["jobs"])


class TestBatchJobs:
    def test_create_batch_jobs(self, seeded_client):
        payload = {
            "files": ["/videos/a.mkv", "/videos/b.mkv"],
            "preset_id": 1,
        }
        response = seeded_client.post("/api/jobs/batch", json=payload)
        assert response.status_code == 200
        assert len(response.json()["job_ids"]) == 2


class TestRetryJob:
    def test_retry_job(self, seeded_client):
        # Create and complete a job manually
        payload = {"source_file": "/videos/test.mkv", "preset_id": 1}
        create_resp = seeded_client.post("/api/jobs", json=payload)
        job_id = create_resp.json()["job_ids"][0]

        # Mark as completed
        async def complete_job():
            async with AsyncSessionLocal() as db:
                from datetime import datetime, timezone

                result = await db.execute(select(Job).where(Job.id == job_id))
                job = result.scalar_one()
                job.status = "completed"
                job.completed_at = datetime.now(timezone.utc)
                await db.commit()

        asyncio.run(complete_job())

        response = seeded_client.post(f"/api/jobs/{job_id}/retry")
        assert response.status_code == 200
        assert len(response.json()["job_ids"]) == 1


class TestSaveJobAsPreset:
    def test_save_job_as_preset(self, seeded_client):
        payload = {"source_file": "/videos/test.mkv", "preset_id": 1}
        create_resp = seeded_client.post("/api/jobs", json=payload)
        job_id = create_resp.json()["job_ids"][0]

        response = seeded_client.post(f"/api/jobs/{job_id}/save-as-preset?name=FromJob")
        assert response.status_code == 200
        assert response.json()["name"] == "FromJob"


class TestClearJobs:
    def test_clear_queued(self, seeded_client):
        payload = {"source_file": "/videos/test.mkv", "preset_id": 1}
        seeded_client.post("/api/jobs", json=payload)

        response = seeded_client.delete("/api/jobs/queued")
        assert response.status_code == 200
        assert response.json()["deleted_count"] >= 1

    def test_clear_completed(self, seeded_client):
        response = seeded_client.delete("/api/jobs/completed")
        assert response.status_code == 200

    def test_delete_history_older_than(self, seeded_client):
        response = seeded_client.delete(
            "/api/jobs/history?older_than=2099-01-01T00:00:00Z"
        )
        assert response.status_code == 200
