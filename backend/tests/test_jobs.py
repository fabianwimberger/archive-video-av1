"""Tests for job API endpoints."""

import asyncio
import httpx
from .conftest import VIDEO_ROOT
from sqlalchemy import select
from app.config import settings
from app.database import AsyncSessionLocal
from app.models.job import Job


class TestCreateJob:
    def test_create_job_with_preset(self, seeded_client):
        payload = {
            "source_file": str(VIDEO_ROOT / "test.mkv"),
            "preset_id": 1,
        }
        response = seeded_client.post("/api/jobs", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert len(data["job_ids"]) == 1

    def test_create_job_with_settings_only(self, seeded_client):
        payload = {
            "source_file": str(VIDEO_ROOT / "test.mkv"),
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

    def test_create_local_only_job_assigns_current_node(self, seeded_client):
        payload = {
            "source_file": str(VIDEO_ROOT / "test.mkv"),
            "preset_id": 1,
            "local_only": True,
        }
        response = seeded_client.post("/api/jobs", json=payload)
        assert response.status_code == 200
        job_id = response.json()["job_ids"][0]

        get_resp = seeded_client.get(f"/api/jobs/{job_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["assigned_worker_id"]
        assert data["remote_job_id"] is None

    def test_create_job_without_preset_or_settings(self, seeded_client):
        payload = {
            "source_file": str(VIDEO_ROOT / "test.mkv"),
        }
        response = seeded_client.post("/api/jobs", json=payload)
        assert response.status_code == 422

    def test_create_job_with_preset_and_settings_override(self, seeded_client):
        payload = {
            "source_file": str(VIDEO_ROOT / "test.mkv"),
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

    def test_create_job_forwards_to_leader_with_resolved_settings(
        self, seeded_client, monkeypatch
    ):
        from app.services.distributed import distributed_service

        captured = {}

        async def fake_request(method, path, *, params=None, json_body=None):
            captured["method"] = method
            captured["path"] = path
            captured["json_body"] = json_body
            return {"job_ids": [42]}

        monkeypatch.setattr(distributed_service, "should_use_leader", lambda: True)
        monkeypatch.setattr(distributed_service, "request_leader", fake_request)

        response = seeded_client.post(
            "/api/jobs",
            json={"source_file": str(VIDEO_ROOT / "test.mkv"), "preset_id": 1},
        )

        assert response.status_code == 200
        assert response.json()["job_ids"] == [42]
        assert captured["method"] == "POST"
        assert captured["path"] == "/api/jobs"
        assert captured["json_body"]["settings"]["crf"] == 26
        assert "preset_id" not in captured["json_body"]


class TestGetJob:
    def test_get_job_returns_settings_as_dict(self, seeded_client):
        payload = {
            "source_file": str(VIDEO_ROOT / "test.mkv"),
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
        payload = {"source_file": str(VIDEO_ROOT / "test.mkv"), "preset_id": 1}
        seeded_client.post("/api/jobs", json=payload)

        response = seeded_client.get("/api/jobs?status=pending&limit=10&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert all(j["status"] == "pending" for j in data["jobs"])

    def test_list_jobs_forwards_to_leader(self, seeded_client, monkeypatch):
        from app.services.distributed import distributed_service

        captured = {}

        async def fake_request(method, path, *, params=None, json_body=None):
            captured["method"] = method
            captured["path"] = path
            captured["params"] = params
            return {"jobs": [], "total": 0}

        monkeypatch.setattr(distributed_service, "should_use_leader", lambda: True)
        monkeypatch.setattr(distributed_service, "request_leader", fake_request)

        response = seeded_client.get("/api/jobs?status=pending&limit=10&offset=0")

        assert response.status_code == 200
        assert response.json() == {"jobs": [], "total": 0}
        assert captured["method"] == "GET"
        assert captured["path"] == "/api/jobs"
        assert captured["params"]["cluster"] == "true"

    def test_node_local_list_excludes_replicas(self, seeded_client):
        async def add_replica():
            async with AsyncSessionLocal() as db:
                db.add(
                    Job(
                        source_file=str(VIDEO_ROOT / "replica.mkv"),
                        output_file=str(VIDEO_ROOT / "replica_conv.mkv"),
                        settings="{}",
                        status="pending",
                        queue_position=1,
                        cluster_job_id="leader:1",
                        cluster_origin_node_id="leader",
                        cluster_origin_job_id=1,
                        is_cluster_replica=True,
                    )
                )
                await db.commit()

        asyncio.run(add_replica())

        response = seeded_client.get(
            "/api/jobs?status=pending&cluster=false&limit=100&offset=0"
        )

        assert response.status_code == 200
        assert all(
            job["source_file"] != str(VIDEO_ROOT / "replica.mkv")
            for job in response.json()["jobs"]
        )

    def test_node_local_get_replica_returns_404(self, seeded_client):
        async def add_replica():
            async with AsyncSessionLocal() as db:
                replica = Job(
                    source_file=str(VIDEO_ROOT / "replica.mkv"),
                    output_file=str(VIDEO_ROOT / "replica_conv.mkv"),
                    settings="{}",
                    status="pending",
                    cluster_job_id="leader:1",
                    cluster_origin_node_id="leader",
                    cluster_origin_job_id=1,
                    is_cluster_replica=True,
                )
                db.add(replica)
                await db.commit()
                await db.refresh(replica)
                return replica.id

        job_id = asyncio.run(add_replica())

        response = seeded_client.get(f"/api/jobs/{job_id}?cluster=false")

        assert response.status_code == 404


class TestBatchJobs:
    def test_create_batch_jobs(self, seeded_client):
        payload = {
            "files": [str(VIDEO_ROOT / "a.mkv"), str(VIDEO_ROOT / "b.mkv")],
            "preset_id": 1,
        }
        response = seeded_client.post("/api/jobs/batch", json=payload)
        assert response.status_code == 200
        assert len(response.json()["job_ids"]) == 2

    def test_create_batch_jobs_rejects_invalid_source(self, seeded_client):
        converted = VIDEO_ROOT / "a_conv.mkv"
        converted.write_bytes(b"converted output")
        try:
            response = seeded_client.post(
                "/api/jobs/batch",
                json={"files": [str(converted)], "preset_id": 1},
            )
        finally:
            converted.unlink(missing_ok=True)

        assert response.status_code == 422
        assert (
            "Converted outputs cannot be queued as sources" in response.json()["detail"]
        )


class TestRetryJob:
    def test_retry_job(self, seeded_client):
        # Create and complete a job manually
        payload = {"source_file": str(VIDEO_ROOT / "test.mkv"), "preset_id": 1}
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
        payload = {"source_file": str(VIDEO_ROOT / "test.mkv"), "preset_id": 1}
        create_resp = seeded_client.post("/api/jobs", json=payload)
        job_id = create_resp.json()["job_ids"][0]

        response = seeded_client.post(f"/api/jobs/{job_id}/save-as-preset?name=FromJob")
        assert response.status_code == 200
        assert response.json()["name"] == "FromJob"


class TestClearJobs:
    def test_clear_queued(self, seeded_client):
        payload = {"source_file": str(VIDEO_ROOT / "test.mkv"), "preset_id": 1}
        seeded_client.post("/api/jobs", json=payload)

        response = seeded_client.delete("/api/jobs/queued")
        assert response.status_code == 200
        assert response.json()["deleted_count"] >= 1

    def test_clear_queued_cluster_false_stays_node_local(
        self, seeded_client, monkeypatch
    ):
        from app.services.distributed import distributed_service

        payload = {"source_file": str(VIDEO_ROOT / "test.mkv"), "preset_id": 1}
        seeded_client.post("/api/jobs", json=payload)

        async def fail_request(*_args, **_kwargs):
            raise AssertionError("node-local clear should not forward")

        monkeypatch.setattr(distributed_service, "should_use_leader", lambda: True)
        monkeypatch.setattr(distributed_service, "request_leader", fail_request)

        response = seeded_client.delete("/api/jobs/queued?cluster=false")

        assert response.status_code == 200
        assert response.json()["deleted_count"] >= 1

    def test_clear_queued_includes_peer_queues(self, seeded_client, monkeypatch):
        from app.services.distributed import distributed_service

        payload = {"source_file": str(VIDEO_ROOT / "test.mkv"), "preset_id": 1}
        seeded_client.post("/api/jobs", json=payload)

        async def fake_clear_peer_jobs(path):
            assert path == "/api/jobs/queued"
            return 2

        monkeypatch.setattr(settings, "DISTRIBUTED_ENABLED", True)
        monkeypatch.setattr(distributed_service, "should_use_leader", lambda: False)
        monkeypatch.setattr(
            distributed_service, "clear_peer_jobs", fake_clear_peer_jobs
        )

        response = seeded_client.delete("/api/jobs/queued")

        assert response.status_code == 200
        assert response.json()["deleted_count"] >= 3

    def test_clear_completed(self, seeded_client):
        response = seeded_client.delete("/api/jobs/completed")
        assert response.status_code == 200

    def test_delete_history_older_than(self, seeded_client):
        response = seeded_client.delete(
            "/api/jobs/history?older_than=2099-01-01T00:00:00Z"
        )
        assert response.status_code == 200


class TestCreateJobDestinationGuards:
    def test_output_already_on_disk_returns_409(self, seeded_client):
        source = VIDEO_ROOT / "output_taken.mkv"
        output = VIDEO_ROOT / "output_taken_conv.mkv"
        source.write_bytes(b"source")
        output.write_bytes(b"existing output")
        try:
            response = seeded_client.post(
                "/api/jobs",
                json={"source_file": str(source), "preset_id": 1},
            )
        finally:
            output.unlink(missing_ok=True)
            source.unlink(missing_ok=True)

        assert response.status_code == 409
        assert response.json()["detail"] == "Conversion output already exists"

    def test_cluster_identity_requires_local_only(self, seeded_client):
        payload = {
            "source_file": str(VIDEO_ROOT / "test.mkv"),
            "preset_id": 1,
            "cluster_job_id": "node-a:5",
            "cluster_origin_node_id": "node-a",
            "cluster_origin_job_id": 5,
        }
        response = seeded_client.post("/api/jobs", json=payload)

        assert response.status_code == 422
        assert response.json()["detail"] == "Cluster identity requires local_only"

    def test_cluster_identity_conflict_returns_409(self, seeded_client):
        source = VIDEO_ROOT / "test.mkv"
        base_payload = {
            "source_file": str(source),
            "preset_id": 1,
            "local_only": True,
            "cluster_job_id": "node-a:77",
            "cluster_origin_node_id": "node-a",
            "cluster_origin_job_id": 77,
        }
        first = seeded_client.post("/api/jobs", json=base_payload)
        assert first.status_code == 200

        conflict = seeded_client.post(
            "/api/jobs",
            json={**base_payload, "source_file": str(VIDEO_ROOT / "a.mkv")},
        )

        assert conflict.status_code == 409
        assert conflict.json()["detail"] == (
            "Cluster identity belongs to a different conversion"
        )

    def test_cluster_identity_replaces_stale_replica(self, seeded_client):
        async def add_replica():
            async with AsyncSessionLocal() as db:
                replica = Job(
                    source_file=str(VIDEO_ROOT / "replica.mkv"),
                    output_file=str(VIDEO_ROOT / "replica_conv.mkv"),
                    settings="{}",
                    status="pending",
                    queue_position=1,
                    cluster_job_id="node-a:88",
                    cluster_origin_node_id="node-a",
                    cluster_origin_job_id=88,
                    is_cluster_replica=True,
                )
                db.add(replica)
                await db.commit()
                await db.refresh(replica)
                return replica.id

        asyncio.run(add_replica())

        response = seeded_client.post(
            "/api/jobs",
            json={
                "source_file": str(VIDEO_ROOT / "test.mkv"),
                "preset_id": 1,
                "local_only": True,
                "cluster_job_id": "node-a:88",
                "cluster_origin_node_id": "node-a",
                "cluster_origin_job_id": 88,
            },
        )

        assert response.status_code == 200
        new_job_id = response.json()["job_ids"][0]

        async def count_replicas():
            async with AsyncSessionLocal() as db:
                from sqlalchemy import func, select as _select

                remaining = (
                    await db.execute(
                        _select(func.count())
                        .select_from(Job)
                        .where(Job.cluster_job_id == "node-a:88")
                    )
                ).scalar_one()
                job = await db.get(Job, new_job_id)
                return remaining, job.is_cluster_replica, job.source_file

        remaining, is_replica, source_file = asyncio.run(count_replicas())

        assert remaining == 1
        assert is_replica is False
        assert source_file == str(VIDEO_ROOT / "test.mkv")


class TestNodeForwarding:
    @staticmethod
    def fake_node_client(handler):
        class FakeResponse:
            def __init__(self, payload, status_code=200):
                self._payload = payload
                self.status_code = status_code

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class FakeAsyncClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def request(self, method, url, params=None, json=None):
                return await handler(method, url, params, json)

        return FakeAsyncClient

    def test_get_job_with_unknown_node_returns_503(self, seeded_client, monkeypatch):
        from app.services.distributed import distributed_service
        import app.routes.jobs as jobs_routes

        monkeypatch.setattr(distributed_service, "peers", lambda: [])
        monkeypatch.setattr(
            jobs_routes.httpx, "AsyncClient", self.fake_node_client(None)
        )

        response = seeded_client.get("/api/jobs/1", params={"node_id": "ghost"})

        assert response.status_code == 503
        assert response.json()["detail"] == "Worker is unavailable"

    def test_get_job_forwards_to_node_and_returns_payload(
        self, seeded_client, monkeypatch
    ):
        import app.routes.jobs as jobs_routes
        from app.services.distributed import PeerNode, distributed_service

        payload = {"source_file": str(VIDEO_ROOT / "test.mkv"), "preset_id": 1}
        job_id = seeded_client.post("/api/jobs", json=payload).json()["job_ids"][0]
        job_json = seeded_client.get(f"/api/jobs/{job_id}").json()

        class FakeResponse200:
            def __init__(self, payload):
                self.status_code = 200
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        captured = {}

        async def handler(method, url, params, json):
            captured["method"] = method
            captured["url"] = url
            captured["params"] = params
            return FakeResponse200(job_json)

        peer = PeerNode("node-b", "Worker", "http://node-b:8000", 0)
        monkeypatch.setattr(distributed_service, "peers", lambda: [peer])
        monkeypatch.setattr(
            jobs_routes.httpx, "AsyncClient", self.fake_node_client(handler)
        )

        response = seeded_client.get(
            f"/api/jobs/{job_id}", params={"node_id": "node-b"}
        )

        assert response.status_code == 200
        assert response.json()["id"] == job_id
        assert captured["method"] == "GET"
        assert captured["url"] == f"http://node-b:8000/api/jobs/{job_id}"
        assert captured["params"]["cluster"] == "false"
        assert captured["params"]["node_id"] == "node-b"

    def test_node_request_maps_http_status_error(self, seeded_client, monkeypatch):
        import app.routes.jobs as jobs_routes
        from app.services.distributed import PeerNode, distributed_service

        class FakeResponse404:
            status_code = 404

            def raise_for_status(self):
                request = httpx.Request("GET", "http://node-b:8000/api/jobs/1")
                response = httpx.Response(
                    404, request=httpx.Request("GET", "http://node-b:8000/api/jobs/1")
                )
                raise httpx.HTTPStatusError("404", request=request, response=response)

            def json(self):
                return {}

        async def handler(method, url, params, json):
            return FakeResponse404()

        peer = PeerNode("node-b", "Worker", "http://node-b:8000", 0)
        monkeypatch.setattr(distributed_service, "peers", lambda: [peer])
        monkeypatch.setattr(
            jobs_routes.httpx, "AsyncClient", self.fake_node_client(handler)
        )

        response = seeded_client.get("/api/jobs/1", params={"node_id": "node-b"})

        assert response.status_code == 404
        assert response.json()["detail"] == "Worker request failed"

    def test_node_request_maps_transport_error_to_502(self, seeded_client, monkeypatch):
        import app.routes.jobs as jobs_routes
        from app.services.distributed import PeerNode, distributed_service

        async def handler(method, url, params, json):
            raise httpx.ConnectError("connection refused")

        peer = PeerNode("node-b", "Worker", "http://node-b:8000", 0)
        monkeypatch.setattr(distributed_service, "peers", lambda: [peer])
        monkeypatch.setattr(
            jobs_routes.httpx, "AsyncClient", self.fake_node_client(handler)
        )

        response = seeded_client.get("/api/jobs/1", params={"node_id": "node-b"})

        assert response.status_code == 502
        assert response.json()["detail"] == "Worker is unavailable"

    def test_patch_position_forwards_to_node(self, seeded_client, monkeypatch):
        import app.routes.jobs as jobs_routes
        from app.services.distributed import PeerNode, distributed_service

        payload = {"source_file": str(VIDEO_ROOT / "test.mkv"), "preset_id": 1}
        job_id = seeded_client.post("/api/jobs", json=payload).json()["job_ids"][0]

        captured = {}

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"success": True}

        async def handler(method, url, params, json):
            captured["method"] = method
            captured["json"] = json
            return FakeResponse()

        peer = PeerNode("node-b", "Worker", "http://node-b:8000", 0)
        monkeypatch.setattr(distributed_service, "peers", lambda: [peer])
        monkeypatch.setattr(
            jobs_routes.httpx, "AsyncClient", self.fake_node_client(handler)
        )

        response = seeded_client.patch(
            f"/api/jobs/{job_id}/position",
            json={"absolute": 1},
            params={"node_id": "node-b"},
        )

        assert response.status_code == 200
        assert captured["method"] == "PATCH"
        assert captured["json"] == {"absolute": 1}

    def test_patch_position_forwards_to_leader(self, seeded_client, monkeypatch):
        from app.services.distributed import distributed_service

        payload = {"source_file": str(VIDEO_ROOT / "test.mkv"), "preset_id": 1}
        job_id = seeded_client.post("/api/jobs", json=payload).json()["job_ids"][0]

        captured = {}

        async def fake_request(method, path, *, params=None, json_body=None):
            captured["method"] = method
            captured["path"] = path
            captured["json_body"] = json_body
            return {"success": True}

        monkeypatch.setattr(distributed_service, "should_use_leader", lambda: True)
        monkeypatch.setattr(distributed_service, "request_leader", fake_request)

        response = seeded_client.patch(
            f"/api/jobs/{job_id}/position", json={"absolute": 1}
        )

        assert response.status_code == 200
        assert captured["method"] == "PATCH"
        assert captured["path"] == f"/api/jobs/{job_id}/position"
        assert captured["json_body"] == {"absolute": 1}

    def test_delete_job_with_unknown_node_returns_503(self, seeded_client, monkeypatch):
        import app.routes.jobs as jobs_routes
        from app.services.distributed import distributed_service

        monkeypatch.setattr(distributed_service, "peers", lambda: [])
        monkeypatch.setattr(
            jobs_routes.httpx, "AsyncClient", self.fake_node_client(None)
        )

        response = seeded_client.delete("/api/jobs/1", params={"node_id": "ghost"})

        assert response.status_code == 503


class TestCancelEdgeCases:
    def test_cancel_processing_job_without_remote_state_returns_409(
        self, seeded_client
    ):
        payload = {"source_file": str(VIDEO_ROOT / "test.mkv"), "preset_id": 1}
        job_id = seeded_client.post("/api/jobs", json=payload).json()["job_ids"][0]

        async def mark_processing():
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Job).where(Job.id == job_id))
                job = result.scalar_one()
                job.status = "processing"
                await db.commit()

        asyncio.run(mark_processing())

        response = seeded_client.delete(f"/api/jobs/{job_id}")

        assert response.status_code == 409
        assert response.json()["detail"] == (
            "Worker state is unknown; cancellation cannot be confirmed"
        )


class TestRetryGuards:
    def test_retry_job_with_missing_source_returns_422(self, seeded_client):
        source = VIDEO_ROOT / "gone.mkv"
        source.write_bytes(b"source")
        payload = {"source_file": str(source), "preset_id": 1}
        job_id = seeded_client.post("/api/jobs", json=payload).json()["job_ids"][0]

        async def finish():
            async with AsyncSessionLocal() as db:
                from datetime import datetime, timezone

                result = await db.execute(select(Job).where(Job.id == job_id))
                job = result.scalar_one()
                job.status = "completed"
                job.completed_at = datetime.now(timezone.utc)
                await db.commit()

        asyncio.run(finish())
        source.unlink()

        response = seeded_client.post(f"/api/jobs/{job_id}/retry")

        assert response.status_code == 422


class TestClusterJobDedupe:
    def test_cluster_key_collapses_replicas_of_same_cluster_job(self):
        from app.routes.jobs import _cluster_job_key, _dedupe_cluster_jobs

        replica = {
            "cluster_job_id": "node-a:1",
            "source_file": "/videos/a.mkv",
            "output_file": "/videos/a_conv.mkv",
            "assigned_worker_id": "node-b",
            "status": "pending",
        }
        stale_replica = dict(replica, status="processing", assigned_worker_id="")
        original = {
            "cluster_job_id": None,
            "source_file": "/videos/a.mkv",
            "output_file": "/videos/a_conv.mkv",
            "assigned_worker_id": "node-a",
            "status": "pending",
        }

        assert _cluster_job_key(replica) == ("node-a:1", "", "", "")
        assert _cluster_job_key(original) == (
            "/videos/a.mkv",
            "/videos/a_conv.mkv",
            "node-a",
            "pending",
        )

        deduped = _dedupe_cluster_jobs([replica, stale_replica, original])
        assert len(deduped) == 2
