"""Tests for the /files API routes."""

import pytest

import app.routes.files as files_routes
from app.services.file_service import file_service


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    """Point the global file_service singleton at an isolated source mount."""
    monkeypatch.setattr(file_service, "source_mount", tmp_path)
    return tmp_path


def test_browse_files_lists_directories_and_files(client, mounted):
    (mounted / "Show S01").mkdir()
    (mounted / "Show S01" / "episode.mkv").write_bytes(b"video")
    (mounted / "movie.mkv").write_bytes(b"video")
    (mounted / "notes.txt").write_bytes(b"not a video")

    response = client.get("/api/files")

    assert response.status_code == 200
    data = response.json()
    assert {"name": "Show S01", "path": "Show S01"} in data["directories"]
    names = {f["name"] for f in data["files"]}
    assert names == {"movie.mkv"}


def test_browse_files_empty_directories_are_hidden(client, mounted):
    (mounted / "Empty").mkdir()

    response = client.get("/api/files")

    assert response.status_code == 200
    assert response.json()["directories"] == []


def test_browse_files_rejects_path_outside_mount(client, mounted):
    response = client.get("/api/files", params={"path": "../outside"})

    assert response.status_code == 400
    assert "Invalid path" in response.json()["detail"]


def test_browse_files_rejects_nonexistent_path(client, mounted):
    response = client.get("/api/files", params={"path": "does-not-exist"})

    assert response.status_code == 400


def test_browse_files_unexpected_error_returns_500(client, mounted, monkeypatch):
    async def boom(path=None):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(file_service, "browse_directory", boom)

    response = client.get("/api/files")

    assert response.status_code == 500


def test_get_file_info_returns_metadata(client, mounted, monkeypatch):
    video = mounted / "movie.mkv"
    video.write_bytes(b"video-bytes")

    async def fake_get_video_info(path):
        return {"codec": "av1", "width": 1920, "height": 1080}

    monkeypatch.setattr("app.services.file_service.get_video_info", fake_get_video_info)

    response = client.get("/api/files/info", params={"path": str(video)})

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "movie.mkv"
    assert data["codec"] == "av1"
    assert data["has_converted"] is False


def test_get_file_info_missing_file_returns_400(client, mounted):
    response = client.get(
        "/api/files/info", params={"path": str(mounted / "missing.mkv")}
    )

    assert response.status_code == 400


def test_get_file_info_outside_mount_returns_400(client, mounted, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "movie.mkv"
    outside.write_bytes(b"video")

    response = client.get("/api/files/info", params={"path": str(outside)})

    assert response.status_code == 400


def test_analyze_file_returns_estimate(client, mounted, monkeypatch):
    video = mounted / "movie.mkv"
    video.write_bytes(b"video-bytes")

    async def fake_estimate_grain(path):
        return {"film_grain": 8, "denoise": 0, "confidence": "medium", "reason": "test"}

    monkeypatch.setattr(files_routes, "estimate_grain", fake_estimate_grain)

    response = client.get("/api/files/analyze", params={"path": str(video)})

    assert response.status_code == 200
    assert response.json()["film_grain"] == 8


def test_analyze_file_with_preset_suggestion(client, mounted, monkeypatch):
    video = mounted / "movie.mkv"
    video.write_bytes(b"video-bytes")

    async def fake_estimate_grain(path):
        return {
            "film_grain": 20,
            "denoise": 1,
            "confidence": "medium",
            "reason": "test",
        }

    async def fake_suggest_preset(film_grain):
        return (42, "Very high film grain detected")

    monkeypatch.setattr(files_routes, "estimate_grain", fake_estimate_grain)
    monkeypatch.setattr(file_service, "suggest_preset", fake_suggest_preset)

    response = client.get(
        "/api/files/analyze", params={"path": str(video), "suggest_preset": True}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["suggested_preset_id"] == 42
    assert "reason" in data


def test_analyze_file_rejects_missing_file(client, mounted):
    response = client.get(
        "/api/files/analyze", params={"path": str(mounted / "missing.mkv")}
    )

    assert response.status_code == 400


def test_analyze_file_rejects_path_outside_mount(client, mounted, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "movie.mkv"
    outside.write_bytes(b"video")

    response = client.get("/api/files/analyze", params={"path": str(outside)})

    assert response.status_code == 400


def test_delete_converted_file_success(client, mounted):
    converted = mounted / "movie_conv.mkv"
    converted.write_bytes(b"converted")

    response = client.delete("/api/files/converted", params={"path": str(converted)})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert not converted.exists()


def test_delete_converted_file_missing_returns_400(client, mounted):
    response = client.delete(
        "/api/files/converted", params={"path": str(mounted / "missing_conv.mkv")}
    )

    assert response.status_code == 400


def test_delete_file_requires_converted_version(client, mounted):
    source = mounted / "movie.mkv"
    source.write_bytes(b"source")

    response = client.delete("/api/files", params={"path": str(source)})

    assert response.status_code == 400
    assert source.exists()


def test_delete_file_success_when_converted_exists(client, mounted):
    source = mounted / "movie.mkv"
    source.write_bytes(b"source")
    converted = mounted / "movie_conv.mkv"
    converted.write_bytes(b"converted")

    response = client.delete("/api/files", params={"path": str(source)})

    assert response.status_code == 200
    assert not source.exists()
    assert converted.exists()
