"""Tests for file service operations."""

import pytest

from app.models.job import Job
from app.services.file_service import FileService


@pytest.mark.asyncio
async def test_browse_directory_includes_latest_job(db_session, tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"source")
    converted = tmp_path / "movie_conv.mkv"
    converted.write_bytes(b"converted")

    older_job = Job(
        source_file=str(source),
        output_file=str(converted),
        preset_name_snapshot="Default",
        settings="{}",
        status="failed",
    )
    latest_job = Job(
        source_file=str(source),
        output_file=str(converted),
        preset_name_snapshot="Grainy",
        settings="{}",
        status="completed",
    )
    db_session.add_all([older_job, latest_job])
    await db_session.commit()

    service = FileService()
    service.source_mount = tmp_path

    result = await service.browse_directory()

    files = {item["name"]: item for item in result["files"]}
    assert files["movie.mkv"]["has_converted"] is True
    assert files["movie.mkv"]["converted_path"] == str(converted)
    assert files["movie.mkv"]["last_job"]["job_id"] == latest_job.id
    assert files["movie.mkv"]["last_job"]["status"] == "completed"


@pytest.mark.asyncio
async def test_browse_directory_includes_mp4_sources(db_session, tmp_path):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"source")
    converted = tmp_path / "movie_conv.mkv"
    converted.write_bytes(b"converted")

    service = FileService()
    service.source_mount = tmp_path

    result = await service.browse_directory()

    files = {item["name"]: item for item in result["files"]}
    assert files["movie.mp4"]["has_converted"] is True
    assert files["movie.mp4"]["converted_path"] == str(converted)


@pytest.mark.asyncio
async def test_delete_file_rejects_empty_converted_file(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"source")
    converted = tmp_path / "movie_conv.mkv"
    converted.touch()

    service = FileService()
    service.source_mount = tmp_path

    with pytest.raises(ValueError, match="No converted version found"):
        await service.delete_file(str(source))

    assert source.exists()
