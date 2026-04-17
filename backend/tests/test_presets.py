"""Tests for preset API endpoints."""


class TestListPresets:
    def test_list_presets(self, seeded_client):
        response = seeded_client.get("/api/presets")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3
        assert any(p["name"] == "Default" and p["is_default"] for p in data)


class TestCreatePreset:
    def test_create_preset(self, seeded_client):
        payload = {
            "name": "MyCustom",
            "description": "Test preset",
            "crf": 24,
            "encoder_preset": 4,
            "svt_params": "tune=0",
            "audio_bitrate": "128k",
            "skip_crop_detect": True,
            "max_resolution": 1080,
        }
        response = seeded_client.post("/api/presets", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "MyCustom"
        assert data["crf"] == 24
        assert data["is_builtin"] is False

    def test_create_preset_name_collision(self, seeded_client):
        payload = {
            "name": "Default",
            "crf": 24,
            "encoder_preset": 4,
            "svt_params": "tune=0",
            "audio_bitrate": "128k",
            "skip_crop_detect": True,
            "max_resolution": 1080,
        }
        response = seeded_client.post("/api/presets", json=payload)
        assert response.status_code == 409


class TestUpdatePreset:
    def test_update_user_preset(self, seeded_client):
        payload = {
            "name": "UserPreset",
            "crf": 24,
            "encoder_preset": 4,
            "svt_params": "tune=0",
            "audio_bitrate": "128k",
            "skip_crop_detect": True,
            "max_resolution": 1080,
        }
        create_resp = seeded_client.post("/api/presets", json=payload)
        preset_id = create_resp.json()["id"]

        response = seeded_client.patch(f"/api/presets/{preset_id}", json={"crf": 30})
        assert response.status_code == 200
        assert response.json()["crf"] == 30

    def test_update_builtin_fails(self, seeded_client):
        response = seeded_client.patch("/api/presets/1", json={"crf": 30})
        assert response.status_code == 409


class TestDeletePreset:
    def test_delete_user_preset(self, seeded_client):
        payload = {
            "name": "ToDelete",
            "crf": 24,
            "encoder_preset": 4,
            "svt_params": "tune=0",
            "audio_bitrate": "128k",
            "skip_crop_detect": True,
            "max_resolution": 1080,
        }
        create_resp = seeded_client.post("/api/presets", json=payload)
        preset_id = create_resp.json()["id"]

        response = seeded_client.delete(f"/api/presets/{preset_id}")
        assert response.status_code == 204

    def test_delete_builtin_fails(self, seeded_client):
        response = seeded_client.delete("/api/presets/1")
        assert response.status_code == 409


class TestDeleteAllPresets:
    def test_delete_all_user_presets(self, seeded_client):
        # Create a couple user presets
        for name in ("UserA", "UserB"):
            seeded_client.post("/api/presets", json={
                "name": name,
                "crf": 24,
                "encoder_preset": 4,
                "svt_params": "tune=0",
                "audio_bitrate": "128k",
                "skip_crop_detect": True,
                "max_resolution": 1080,
            })

        response = seeded_client.delete("/api/presets/all")
        assert response.status_code == 204

        # Should only have built-ins left
        list_resp = seeded_client.get("/api/presets")
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert len(data) == 3
        assert all(p["is_builtin"] for p in data)

    def test_delete_all_resets_default_to_builtin(self, seeded_client):
        create_resp = seeded_client.post("/api/presets", json={
            "name": "MyDefault",
            "crf": 24,
            "encoder_preset": 4,
            "svt_params": "tune=0",
            "audio_bitrate": "128k",
            "skip_crop_detect": True,
            "max_resolution": 1080,
        })
        preset_id = create_resp.json()["id"]
        seeded_client.post(f"/api/presets/{preset_id}/set-default")

        response = seeded_client.delete("/api/presets/all")
        assert response.status_code == 204

        list_resp = seeded_client.get("/api/presets")
        data = list_resp.json()
        default = next(p for p in data if p["is_default"])
        assert default["name"] == "Default"
        assert default["is_builtin"] is True


class TestDuplicatePreset:
    def test_duplicate_preset(self, seeded_client):
        response = seeded_client.post("/api/presets/1/duplicate")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Default (copy)"
        assert data["is_builtin"] is False


class TestSetDefaultPreset:
    def test_set_default(self, seeded_client):
        payload = {
            "name": "NewDefault",
            "crf": 24,
            "encoder_preset": 4,
            "svt_params": "tune=0",
            "audio_bitrate": "128k",
            "skip_crop_detect": True,
            "max_resolution": 1080,
        }
        create_resp = seeded_client.post("/api/presets", json=payload)
        preset_id = create_resp.json()["id"]

        response = seeded_client.post(f"/api/presets/{preset_id}/set-default")
        assert response.status_code == 200
        assert response.json()["is_default"] is True


class TestExportPresets:
    def test_export_all(self, seeded_client):
        response = seeded_client.get("/api/presets/export")
        assert response.status_code == 200
        data = response.json()
        assert data["format"] == "archive-video-av1.presets"
        assert data["version"] == 1

    def test_export_single(self, seeded_client):
        response = seeded_client.get("/api/presets/1/export")
        assert response.status_code == 200
        data = response.json()
        assert len(data["presets"]) == 1
