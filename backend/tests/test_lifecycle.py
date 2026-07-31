"""Tests for startup/shutdown lifecycle helpers."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.job import Job
from app.models.preset import Preset
from app.services.lifecycle import BUILTIN_PRESETS


def make_job(**overrides):
    defaults = dict(
        source_file="/videos/movie.mkv",
        output_file="/videos/movie_conv.mkv",
        status="processing",
        settings="{}",
        is_cluster_replica=False,
    )
    defaults.update(overrides)
    return Job(**defaults)


@pytest.mark.asyncio
async def test_sync_builtin_presets_creates_missing_presets(
    db_session, original_lifecycle_functions
):
    await original_lifecycle_functions["sync_builtin_presets"]()

    result = await db_session.execute(select(Preset))
    presets = {p.name: p for p in result.scalars().all()}

    assert set(presets) == {b["name"] for b in BUILTIN_PRESETS}
    assert presets["Default"].crf == 26
    assert presets["Default"].is_builtin is True


@pytest.mark.asyncio
async def test_sync_builtin_presets_updates_drifted_builtin(
    db_session, original_lifecycle_functions
):
    db_session.add(
        Preset(
            name="Default",
            description="stale",
            is_builtin=True,
            crf=99,
            encoder_preset=1,
            svt_params="stale",
            audio_bitrate="64k",
            skip_crop_detect=True,
            max_resolution=720,
        )
    )
    await db_session.commit()

    await original_lifecycle_functions["sync_builtin_presets"]()

    result = await db_session.execute(select(Preset).where(Preset.name == "Default"))
    preset = result.scalar_one()

    assert preset.crf == 26
    assert preset.audio_bitrate == "96k"
    assert preset.max_resolution == 1080


@pytest.mark.asyncio
async def test_sync_builtin_presets_preserves_user_preset_with_same_name(
    db_session, original_lifecycle_functions
):
    db_session.add(
        Preset(
            name="Default",
            description="user override",
            is_builtin=False,
            crf=51,
            encoder_preset=8,
            svt_params="tune=2",
            audio_bitrate="128k",
            skip_crop_detect=True,
            max_resolution=2160,
        )
    )
    await db_session.commit()

    await original_lifecycle_functions["sync_builtin_presets"]()

    result = await db_session.execute(select(Preset).where(Preset.name == "Default"))
    preset = result.scalar_one()

    assert preset.is_builtin is False
    assert preset.crf == 51
    assert preset.max_resolution == 2160


@pytest.mark.asyncio
async def test_recover_interrupted_jobs_marks_local_processing_jobs_failed(
    db_session, original_lifecycle_functions
):
    job = make_job()
    db_session.add(job)
    await db_session.commit()
    job_id = job.id

    await original_lifecycle_functions["recover_interrupted_jobs"]()
    db_session.expire_all()

    result = await db_session.execute(select(Job).where(Job.id == job_id))
    refreshed = result.scalar_one()

    assert refreshed.status == "failed"
    assert refreshed.error_message == "Interrupted by service restart"
    assert refreshed.completed_at is not None


@pytest.mark.asyncio
async def test_recover_interrupted_jobs_skips_remote_delegated_jobs(
    db_session, original_lifecycle_functions
):
    job = make_job(remote_job_id=7)
    db_session.add(job)
    await db_session.commit()
    job_id = job.id

    await original_lifecycle_functions["recover_interrupted_jobs"]()
    db_session.expire_all()

    result = await db_session.execute(select(Job).where(Job.id == job_id))
    assert result.scalar_one().status == "processing"


@pytest.mark.asyncio
async def test_recover_interrupted_jobs_skips_cluster_replica_jobs(
    db_session, original_lifecycle_functions
):
    job = make_job(is_cluster_replica=True)
    db_session.add(job)
    await db_session.commit()
    job_id = job.id

    await original_lifecycle_functions["recover_interrupted_jobs"]()
    db_session.expire_all()

    result = await db_session.execute(select(Job).where(Job.id == job_id))
    assert result.scalar_one().status == "processing"


@pytest.mark.asyncio
async def test_prune_history_noop_when_disabled(
    db_session, original_lifecycle_functions, monkeypatch
):
    monkeypatch.setattr(settings, "JOB_HISTORY_RETENTION_DAYS", 0)
    monkeypatch.setattr(settings, "JOB_HISTORY_MAX_ROWS", 0)

    old_job = make_job(
        status="completed",
        completed_at=datetime.now(timezone.utc) - timedelta(days=999),
    )
    db_session.add(old_job)
    await db_session.commit()

    await original_lifecycle_functions["prune_history"]()
    db_session.expire_all()

    result = await db_session.execute(select(Job))
    assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_prune_history_removes_jobs_past_retention(
    db_session, original_lifecycle_functions, monkeypatch
):
    monkeypatch.setattr(settings, "JOB_HISTORY_RETENTION_DAYS", 7)
    monkeypatch.setattr(settings, "JOB_HISTORY_MAX_ROWS", 0)

    old_job = make_job(
        status="completed", completed_at=datetime.now(timezone.utc) - timedelta(days=30)
    )
    recent_job = make_job(
        status="completed", completed_at=datetime.now(timezone.utc) - timedelta(days=1)
    )
    db_session.add_all([old_job, recent_job])
    await db_session.commit()

    await original_lifecycle_functions["prune_history"]()
    db_session.expire_all()

    result = await db_session.execute(select(Job))
    remaining = result.scalars().all()
    assert len(remaining) == 1
    assert remaining[0].id == recent_job.id


@pytest.mark.asyncio
async def test_prune_history_respects_max_rows(
    db_session, original_lifecycle_functions, monkeypatch
):
    monkeypatch.setattr(settings, "JOB_HISTORY_RETENTION_DAYS", 0)
    monkeypatch.setattr(settings, "JOB_HISTORY_MAX_ROWS", 2)

    now = datetime.now(timezone.utc)
    jobs = [
        make_job(status="completed", completed_at=now - timedelta(days=3)),
        make_job(status="completed", completed_at=now - timedelta(days=2)),
        make_job(status="completed", completed_at=now - timedelta(days=1)),
    ]
    db_session.add_all(jobs)
    await db_session.commit()
    newest_ids = {jobs[1].id, jobs[2].id}

    await original_lifecycle_functions["prune_history"]()
    db_session.expire_all()

    result = await db_session.execute(select(Job))
    remaining_ids = {j.id for j in result.scalars().all()}
    assert remaining_ids == newest_ids


@pytest.mark.asyncio
async def test_prune_history_ignores_active_jobs(
    db_session, original_lifecycle_functions, monkeypatch
):
    monkeypatch.setattr(settings, "JOB_HISTORY_RETENTION_DAYS", 1)
    monkeypatch.setattr(settings, "JOB_HISTORY_MAX_ROWS", 0)

    active_job = make_job(status="processing", completed_at=None)
    db_session.add(active_job)
    await db_session.commit()

    await original_lifecycle_functions["prune_history"]()
    db_session.expire_all()

    result = await db_session.execute(select(Job))
    assert len(result.scalars().all()) == 1
